"""Regression tests for video tensor routing; no MLX/GPU required."""
import sys
import json
import types
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np

from video_retrieval_worker import Encoder, checked_vector, frames_for, run, REVISION


class VideoTensorRoutingTests(unittest.TestCase):
    def test_forward_routes_video_pixels_to_native_video_branch(self):
        encoder = Encoder.__new__(Encoder)
        encoder.mx = types.SimpleNamespace(bool_=np.bool_, expand_dims=np.expand_dims)
        features = types.SimpleNamespace(inputs_embeds=np.zeros((1, 3, 4)),
            position_ids=np.zeros((3, 1, 3)), visual_pos_masks=np.ones((1, 3)),
            deepstack_visual_embeds=np.ones((3, 3, 4)))
        hidden = np.array([[[1., 0., 0., 0.], [0., 1., 0., 0.], [0., 0., 3., 4.]]])
        model = types.SimpleNamespace(get_input_embeddings=Mock(return_value=features),
                                      language_model=types.SimpleNamespace(model=Mock(return_value=hidden)))
        encoder.model = model
        modules = {
            'mlx_vlm.models.cache': types.SimpleNamespace(create_causal_mask=lambda n: np.tril(np.ones((n, n), dtype=bool))),
            'mlx_vlm.models.pooling': types.SimpleNamespace(normalize_embeddings=lambda x: x/np.linalg.norm(x, axis=-1, keepdims=True)),
            'mlx_vlm.models.qwen3_vl_embedding.qwen3_vl_embedding': types.SimpleNamespace(last_non_padding_token=lambda h, m: h[:, -1]),
        }
        pixels = np.ones((4, 6))
        grid = np.array([[2, 2, 2]])
        with patch.dict(sys.modules, modules):
            vector = encoder._forward({'input_ids': np.array([[151656, 151656, 10]]),
                                       'attention_mask': np.ones((1, 3)),
                                       'pixel_values_videos': pixels, 'video_grid_thw': grid})
        kwargs = model.get_input_embeddings.call_args.kwargs
        self.assertIs(kwargs['pixel_values_videos'], pixels)
        self.assertIs(kwargs['video_grid_thw'], grid)
        self.assertIsNone(kwargs['pixel_values'])
        self.assertIs(model.language_model.model.call_args.kwargs['position_ids'], features.position_ids)
        np.testing.assert_allclose(vector, [[0, 0, .6, .8]])

    def test_embedding_preserves_order_and_sample_times_and_requires_video_tokens(self):
        encoder = Encoder.__new__(Encoder)
        encoder.mx = types.SimpleNamespace(array=np.array, eval=lambda value: None, clear_cache=lambda: None)
        encoder.model = types.SimpleNamespace(language_model=types.SimpleNamespace(_rope_deltas=None, _position_ids=None))
        processor = Mock()
        processor.apply_chat_template.return_value = 'video chat'
        processor.return_value = {'input_ids': np.array([[151656, 151656]]),
            'attention_mask': np.ones((1, 2)), 'pixel_values_videos': np.ones((8, 16)),
            'video_grid_thw': np.array([[2, 2, 2]])}
        encoder.processor = processor
        encoder._forward = Mock(return_value=np.ones((1, 128)))
        frames = [np.full((8, 8, 3), value, dtype=np.uint8) for value in (11, 22, 33, 44)]
        encoder.video(frames, [4., 5., 6., 7.])
        kwargs = processor.call_args.kwargs
        self.assertFalse(kwargs['do_sample_frames'])
        self.assertEqual(kwargs['video_metadata'][0]['fps'], 1.)
        self.assertEqual(kwargs['video_metadata'][0]['frames_indices'], [0, 1, 2, 3])
        np.testing.assert_array_equal(kwargs['videos'][0][:, 0, 0, 0], [11, 22, 33, 44])
        self.assertIn('pixel_values_videos', encoder._forward.call_args.args[0])
        self.assertNotIn('pixel_values', encoder._forward.call_args.args[0])
        encoder.video(frames, [4., 5., 6., 6.9])
        metadata = processor.call_args.kwargs['video_metadata'][0]
        self.assertEqual(metadata['fps'], 1000.)
        self.assertEqual(metadata['frames_indices'], [0, 1000, 2000, 2900])
        self.assertEqual(metadata['total_num_frames'], 2901)
        np.testing.assert_allclose(np.array(metadata['frames_indices']) / metadata['fps'], [0., 1., 2., 2.9])
        processor.return_value['input_ids'] = np.array([[2, 3]])
        with self.assertRaisesRegex(ValueError, '没有实际视频标记'):
            encoder.video(frames, [4., 5., 6., 7.])

    def test_empty_or_nonfinite_embeddings_never_become_matches(self):
        for value in (np.zeros(128), np.full(128, np.nan), np.zeros((1, 128))):
            with self.assertRaises(ValueError):
                checked_vector(value)
        with self.assertRaises(ValueError):
            checked_vector(np.ones(128), expected=2048)

    def test_empty_container_tail_uses_recorded_earlier_frames_without_faking_time(self):
        from PIL import Image
        window = {'source':'movie.mov', 'start':4., 'end':9.945,
                  'frameTimes':[4., 5.931667, 7.863333, 9.795]}
        calls = []
        def extract(args, **kwargs):
            at = float(args[args.index('-ss')+1]); calls.append(at)
            if at <= 9.761:
                Image.new('RGB', (8, 8), (1, 2, 3)).save(args[-1])
        with tempfile.TemporaryDirectory() as tmp, patch('video_retrieval_worker.subprocess.run', side_effect=extract):
            paths, arrays = frames_for(window, Path(tmp), 'ffmpeg')
            self.assertEqual(len(arrays), 4)
            self.assertEqual(window['frameTimes'][-1], 9.695)
            self.assertEqual(window['requestedFrameTimes'][-1], 9.795)
            self.assertTrue(all(a < b for a,b in zip(window['frameTimes'], window['frameTimes'][1:])))
            count = len(calls)
            frames_for(window, Path(tmp), 'ffmpeg')
            self.assertEqual(len(calls), count)

    def test_missing_timing_sidecar_does_not_relabel_existing_backed_off_tail(self):
        from PIL import Image
        window = {'source':'movie.mov', 'start':4., 'end':9.945,
                  'requestedFrameTimes':[4., 5.931667, 7.863333, 9.795],
                  'frameTimes':[4., 5.931667, 7.863333, 9.695]}
        def extract(args, **kwargs):
            Image.new('RGB', (8, 8), (0, 2, 3)).save(args[-1])
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for index in (1, 2, 3):
                Image.new('RGB', (8, 8), (index, 2, 3)).save(folder/f'{index}.jpg')
            with patch('video_retrieval_worker.subprocess.run', side_effect=extract):
                frames_for(window, folder, 'ffmpeg')
            self.assertEqual(window['frameTimes'][-1], 9.695)
            self.assertEqual(window['requestedFrameTimes'][-1], 9.795)
            saved = json.loads((folder/'frame-times.json').read_text())
            self.assertEqual(saved['actual'], window['frameTimes'])

    def test_missing_jpeg_recomputes_only_affected_vector_and_keeps_old_cache(self):
        from PIL import Image
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root/'movie.mov'
            source.write_bytes(b'video fixture')
            model_path = root/'model'
            model_path.mkdir()
            cache = root/'cache'/'model-key'
            fingerprint = [str(source.resolve()), source.stat().st_size, source.stat().st_mtime_ns]
            requested = [4., 5.931667, 7.863333, 9.795]
            windows = []
            for identifier in ('restore', 'old-intact'):
                folder = cache/'windows'/identifier
                folder.mkdir(parents=True)
                paths = [str(folder/f'{i}.jpg') for i in range(4)]
                for index, path in enumerate(paths):
                    if identifier == 'restore' and index == 3:
                        continue
                    Image.new('RGB', (8, 8), (index, 2, 3)).save(path)
                saved = {'vector':[1.] * 128, 'frames':paths}
                if identifier == 'restore':
                    saved['frameTimes'] = [4., 5.931667, 7.863333, 9.695]
                (folder/'embedding.json').write_text(json.dumps(saved))
                windows.append({'id':identifier, 'source':str(source), 'sourceFingerprint':fingerprint,
                                'start':4., 'end':9.945, 'frameTimes':list(requested)})
            query_path = cache/'queries'/'query.json'
            query_path.parent.mkdir()
            query_path.write_text(json.dumps({'vector':[1.] * 128}))
            encoder = Mock()
            encoder.video.return_value = checked_vector(np.arange(1., 129.))
            seeks = []
            def extract(args, **kwargs):
                at = float(args[args.index('-ss')+1])
                seeks.append(at)
                if at <= 9.61:
                    Image.new('RGB', (8, 8), (0, 2, 3)).save(args[-1])
            payload = {'revision':REVISION, 'model':str(model_path), 'cacheRoot':str(root/'cache'),
                       'windows':windows, 'queries':[{'id':'query', 'text':'food'}], 'ffmpeg':'ffmpeg'}
            with patch('video_retrieval_worker.model_identity', return_value='model-key'), \
                 patch('video_retrieval_worker.Encoder', return_value=encoder), \
                 patch('video_retrieval_worker.importlib.metadata.version', return_value='test'), \
                 patch('video_retrieval_worker.progress'), \
                 patch('video_retrieval_worker.subprocess.run', side_effect=extract):
                run(payload, root/'result.json')
            result = json.loads((root/'result.json').read_text())
            self.assertEqual(seeks, [9.695, 9.595])
            self.assertEqual(encoder.video.call_count, 1)
            encoder.text.assert_not_called()
            self.assertEqual(result['reusedWindowCount'], 1)
            self.assertEqual(result['windows'][0]['frameTimes'][-1], 9.595)
            self.assertEqual(result['windows'][1]['frameTimes'], requested)
            restored = json.loads((cache/'windows'/'restore'/'embedding.json').read_text())
            self.assertEqual(restored['frameTimes'][-1], 9.595)
            np.testing.assert_allclose(restored['vector'], encoder.video.return_value)
            self.assertNotIn('frameTimes', json.loads((cache/'windows'/'old-intact'/'embedding.json').read_text()))


if __name__ == '__main__':
    unittest.main()
