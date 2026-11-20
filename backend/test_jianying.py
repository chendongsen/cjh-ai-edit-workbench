import copy
import json
import math
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import imageio_ffmpeg
from jianying import build_draft, bounded_duration, micros, safe_name


class JianyingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name)
        cls.media = cls.root / '中文素材.mp4'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-f', 'lavfi', '-i',
                        'color=c=red:s=64x96:r=30', '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000',
                        '-t', '3', '-c:v', 'libx264', '-c:a', 'aac', str(cls.media)], check=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def data(self):
        clip = {'name': '中文素材', 'path': str(self.media), 'at': 0, 'duration': 1,
                'sourceStart': 1, 'properties': {'AudioVolume': -6, 'ZoomX': 1.18, 'ZoomY': 1.18}}
        return {'project': 'test', 'timeline': '剪辑', 'timelineId': 'id', 'fps': 30, 'width': 1080,
                'height': 1920, 'tracks': [{'kind': 'video', 'index': 1, 'name': '人物', 'clips': [clip]},
                                         {'kind': 'audio', 'index': 1, 'name': '原声', 'clips': [copy.deepcopy(clip)]}]}

    def test_real_multitrack_and_audio_no_duplicate(self):
        folder = self.root / 'roundtrip'
        result = build_draft(self.data(), folder)
        d = json.loads((folder / 'draft_info.json').read_text())
        video, audio = [t['segments'][0] for t in d['tracks']]
        self.assertEqual(result['clipCount'], 2)
        self.assertEqual(video['source_timerange']['start'], 1_000_000)
        self.assertEqual(video['target_timerange']['duration'], 1_000_000)
        self.assertEqual(video['volume'], 0)
        self.assertAlmostEqual(audio['volume'], 10**(-6/20))
        self.assertEqual(video['clip']['scale']['x'], 1.18)
        self.assertEqual((folder/'draft_content.json').read_bytes(), (folder/'draft_info.json').read_bytes())
        self.assertTrue(all(Path(v['path']).exists() for v in d['materials']['videos']))
        self.assertEqual(self.media.stat().st_ino, Path(d['materials']['videos'][0]['path']).stat().st_ino)
        with self.assertRaises(FileExistsError):
            build_draft(self.data(), folder)

    def test_fractional_rate_not_silently_changed(self):
        data = self.data()
        data['fps'] = 29.97
        with self.assertRaisesRegex(ValueError, '整数帧率'):
            build_draft(data, self.root/'fractional')

    def test_same_track_overlap_rejected(self):
        data = self.data()
        data['tracks'][0]['clips'] *= 2
        with self.assertRaisesRegex(ValueError, '同轨重叠'):
            build_draft(data, self.root/'overlap')

    def test_numbers_and_names(self):
        for value in [-1, float('nan'), float('inf')]:
            with self.assertRaises(ValueError):
                micros(value)
        self.assertEqual(micros(1/30), 33333)
        self.assertNotIn('/', safe_name('../../test'))

    def test_tail_quantization_does_not_extend_or_stretch_source(self):
        length, trimmed = bounded_duration(52_466_667, 1_533_334, 53_970_000, 30, 140_233_333)
        self.assertEqual((length, trimmed), (1_503_333, 30_001))
        self.assertEqual(bounded_duration(0, 1_000_000, 3_000_000, 30), (1_000_000, 0))
        with self.assertRaisesRegex(ValueError, '超过一帧'):
            bounded_duration(2_000_000, 1_040_000, 3_000_000, 30)
        with self.assertRaisesRegex(ValueError, '入点'):
            bounded_duration(3_000_000, 1, 3_000_000, 30)
        with self.assertRaisesRegex(ValueError, '采样帧'):
            bounded_duration(2_000_000, 1_030_000, 3_000_000, 30)

    def test_real_tail_keeps_speed_and_next_position(self):
        data = self.data()
        for track in data['tracks']:
            first = track['clips'][0]
            first.update(sourceStart=2.033333, duration=1)
            second = copy.deepcopy(first)
            second.update(at=1, sourceStart=0, duration=1)
            track['clips'].append(second)
        folder = self.root / 'tail-roundtrip'
        result = build_draft(data, folder)
        content = json.loads((folder / 'draft_info.json').read_text())
        for track in content['tracks']:
            first, second = track['segments']
            self.assertEqual(first['speed'], 1)
            self.assertEqual(first['source_timerange']['duration'], first['target_timerange']['duration'])
            self.assertEqual(second['target_timerange']['start'], 1_000_000)
        self.assertTrue(result['tailAdjustments'])


if __name__ == '__main__':
    unittest.main()
