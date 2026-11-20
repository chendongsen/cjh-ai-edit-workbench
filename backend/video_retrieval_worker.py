"""Isolated MLX Qwen3-VL video embedding worker. Local files only.

Uses the fixed MLX-VLM encoder model implementation, not next-token logits.
The ordered video frames carry their sampling times to the Qwen processor.
"""
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import time

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['HF_HUB_DISABLE_TELEMETRY'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'

REVISION = 'video-retrieval-2'
PREPROCESSING = 'ordered-video-4frames-384px-native-video-forward-v2'


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')
    temporary.replace(path)


def progress(message):
    print('CJH_RETRIEVAL '+message, flush=True)


def model_identity(model):
    files = sorted(model.glob('*.safetensors'))
    if not files:
        raise ValueError('本地视频检索模型没有权重文件')
    records = [[f.name, f.stat().st_size, f.stat().st_mtime_ns] for f in files]
    config = json.loads((model/'config.json').read_text())
    if config.get('model_type') not in ('qwen3_vl', 'qwen3_vl_embedding'):
        raise ValueError('视频检索需要 Qwen3-VL-Embedding 模型，配置类型不符')
    files = sorted(p for p in model.glob('*.json') if 'index' not in p.name)
    details = [[p.name, hashlib.sha256(p.read_bytes()).hexdigest()] for p in files]
    identity = [REVISION, PREPROCESSING, str(model.resolve()), records, details,
                importlib.metadata.version('mlx-vlm'), importlib.metadata.version('transformers')]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def checked_vector(value, expected=None):
    import numpy as np
    vector = np.asarray(value, dtype=np.float32)
    if vector.ndim != 1 or vector.size < 128 or not np.isfinite(vector).all():
        raise ValueError('模型未返回有效检索向量')
    if expected is not None and vector.size != expected:
        raise ValueError('查询与视频检索向量维数不一致')
    length = float(np.linalg.norm(vector))
    if not math.isfinite(length) or length <= 1e-8:
        raise ValueError('模型返回零向量，不能用于画面匹配')
    return vector / length


class Encoder:
    def __init__(self, path):
        import mlx.core as mx
        from mlx_vlm.encoder_loader import load_encoder_model
        from mlx_vlm.utils import load_processor
        self.mx = mx
        # Community quantized checkpoints keep the original qwen3_vl type.
        # Explicit encoder remapping selects last-hidden-state pooling and
        # removes the generative lm_head; strict weight loading remains enabled.
        self.model = load_encoder_model(path, model_remapping={'qwen3_vl': 'qwen3_vl_embedding'}, strict=True)
        self.processor = load_processor(path, add_detokenizer=False,
                                        local_files_only=True, trust_remote_code=False)
        self.model.language_model._rope_deltas = None
        self.model.language_model._position_ids = None

    def _forward(self, inputs):
        """Compatibility for MLX-VLM 0.7.12's incomplete embedding wrapper.

        Its Qwen3VL embedding wrapper drops pixel_values_videos, although the
        underlying model has a native video branch. Pass the real video tensor
        through that branch, then use the same final norm/last-token pooling as
        the official embedding implementation. No dependency is monkeypatched.
        """
        from mlx_vlm.models.cache import create_causal_mask
        from mlx_vlm.models.pooling import normalize_embeddings
        from mlx_vlm.models.qwen3_vl_embedding.qwen3_vl_embedding import last_non_padding_token
        ids, mask = inputs['input_ids'], inputs.get('attention_mask')
        features = self.model.get_input_embeddings(
            input_ids=ids, pixel_values=inputs.get('pixel_values'),
            pixel_values_videos=inputs.get('pixel_values_videos'),
            image_grid_thw=inputs.get('image_grid_thw'),
            video_grid_thw=inputs.get('video_grid_thw'), mask=mask)
        model_mask = mask
        if mask is not None and mask.ndim == 2:
            causal = create_causal_mask(ids.shape[-1])
            valid = mask.astype(self.mx.bool_)
            model_mask = (causal[None, None, :, :] & self.mx.expand_dims(valid, axis=(1, 2))
                          & self.mx.expand_dims(valid, axis=(1, 3)))
        hidden = self.model.language_model.model(
            ids, inputs_embeds=features.inputs_embeds, mask=model_mask, cache=None,
            position_ids=features.position_ids, visual_pos_masks=features.visual_pos_masks,
            deepstack_visual_embeds=features.deepstack_visual_embeds)
        pooled = last_non_padding_token(hidden, mask)
        return normalize_embeddings(pooled)

    def _embed(self, text=None, frames=None, times=None):
        import numpy as np
        content = [{'type': 'text', 'text': text}] if frames is None else [{'type': 'video'}]
        conversation = [{'role': 'system', 'content': [{'type': 'text', 'text': "Represent the user's input."}]},
                        {'role': 'user', 'content': content}]
        prompt = self.processor.apply_chat_template(conversation, add_generation_prompt=True, tokenize=False)
        if frames is None:
            encoded = self.processor.tokenizer(prompt, return_tensors='np', padding=True,
                                               add_special_tokens=False)
        else:
            sampled_rate = (len(times)-1)/(times[-1]-times[0])
            metadata = {'fps': sampled_rate, 'total_num_frames': len(frames),
                        'frames_indices': list(range(len(frames))),
                        'duration': times[-1]-times[0]}
            if not np.allclose(np.diff(times), np.diff(times)[0], rtol=0, atol=1e-5):
                metadata.update(fps=1000., frames_indices=[round((t-times[0])*1000) for t in times],
                                total_num_frames=round((times[-1]-times[0])*1000)+1)
            # Explicit processor arguments avoid a generic HTTP image-only
            # embedding route. No video sampling or timestamp guessing happens.
            encoded = self.processor(text=[prompt], videos=[np.stack(frames)],
                video_metadata=[metadata], do_sample_frames=False,
                min_pixels=32*32*16, max_pixels=384*384, padding=True, return_tensors='np')
            if 'pixel_values_videos' not in encoded or 'video_grid_thw' not in encoded:
                raise ValueError('视频处理器没有生成视频像素和时间网格，已停止而未按文本冒充')
            if not np.any(np.asarray(encoded['input_ids']) == 151656):
                raise ValueError('视频输入没有实际视频标记，无法建立检索索引')
        allowed = ('input_ids', 'attention_mask', 'pixel_values', 'pixel_values_videos',
                   'image_grid_thw', 'video_grid_thw')
        inputs = {name: self.mx.array(encoded[name]) for name in allowed if encoded.get(name) is not None}
        # Reset positional state between independent samples, including text
        # after videos; cached RoPE from another input is not compatible.
        self.model.language_model._rope_deltas = None
        self.model.language_model._position_ids = None
        vector = self._forward(inputs)
        self.mx.eval(vector)
        result = checked_vector(vector[0].tolist())
        del inputs
        self.mx.clear_cache()
        return result

    def text(self, text):
        return self._embed(text=text)

    def video(self, frames, times):
        return self._embed(frames=frames, times=times)


def frames_for(window, folder, ffmpeg):
    import numpy as np
    from PIL import Image
    folder.mkdir(parents=True, exist_ok=True)
    paths, arrays = [], []
    requested = list(window.get('requestedFrameTimes', window['frameTimes']))
    # An embedding may already record an EOF back-off even if its separate
    # timing sidecar was removed. Preserve those actual seeks when restoring
    # another missing JPEG; never relabel an existing tail frame as requested.
    actual = list(window['frameTimes'])
    timing_path = folder/'frame-times.json'
    if timing_path.is_file():
        timing = json.loads(timing_path.read_text())
        if timing.get('requested') != requested:
            raise ValueError('视频抽帧缓存的时间依据不一致')
        actual = timing['actual']
    if len(actual) != len(requested) or any(not math.isfinite(t) for t in actual):
        raise ValueError('视频抽帧缓存时间无效')
    if any(a >= b for a, b in zip(actual, actual[1:])):
        raise ValueError('视频抽帧时间必须保持先后顺序')
    for index, at in enumerate(actual):
        path = folder/f'{index}.jpg'
        if not path.is_file():
            temporary = folder/f'{index}.tmp.jpg'
            lower = max(window['start'], actual[index-1]+.001 if index else window['start'])
            # A MOV's audio tail can outlast its video stream. Only when FFmpeg
            # decoded no frame, try nearby earlier times within this window;
            # record the actual seek times, never label a substituted frame as EOF.
            seeks = [at] + [round(at-delta, 6) for delta in (.1, .2, .4, .8) if at-delta >= lower]
            for seek in seeks:
                temporary.unlink(missing_ok=True)
                try:
                    subprocess.run([ffmpeg, '-v', 'error', '-nostdin', '-y', '-ss', str(seek),
                                    '-i', window['source'], '-frames:v', '1', '-vf',
                                    'scale=384:384:force_original_aspect_ratio=decrease',
                                    '-pix_fmt', 'yuvj420p', '-threads', '1', '-q:v', '3', str(temporary)],
                                   capture_output=True, check=True, timeout=60)
                except subprocess.CalledProcessError as error:
                    detail = (error.stderr or b'').decode(errors='replace')[-1200:]
                    raise ValueError(f'视频检索抽帧失败：{window["source"]} @ {seek:.3f}s：{detail}') from error
                if temporary.is_file() and temporary.stat().st_size:
                    actual[index] = seek
                    break
            if not temporary.is_file() or not temporary.stat().st_size:
                raise ValueError('无法解码检索片段：'+window['source'])
            temporary.replace(path)
            write(timing_path, {'requested': requested, 'actual': actual})
        with Image.open(path) as frame:
            arrays.append(np.asarray(frame.convert('RGB')))
        paths.append(str(path.resolve()))
    if len({a.shape for a in arrays}) != 1:
        raise ValueError('同一视频片段抽帧尺寸不一致')
    window['requestedFrameTimes'] = requested
    window['frameTimes'] = actual
    return paths, arrays


def run(payload, destination):
    import numpy as np
    if payload.get('revision') != REVISION:
        raise ValueError('视频检索任务版本不兼容')
    path = Path(payload['model']).resolve()
    if not path.is_dir():
        raise ValueError('本地视频检索模型不存在')
    model_key = model_identity(path)
    cache = Path(payload['cacheRoot'])/model_key
    cache.mkdir(parents=True, exist_ok=True)
    encoder = None
    started = time.monotonic()
    def model():
        nonlocal encoder
        if encoder is None:
            progress('正在载入本地 Qwen3-VL 视频检索模型')
            encoder = Encoder(path)
        return encoder
    vectors, windows, reused = [], [], 0
    for index, original in enumerate(payload['windows'], 1):
        window = dict(original)
        source = Path(window['source']).resolve()
        stat = source.stat()
        if window['sourceFingerprint'] != [str(source), stat.st_size, stat.st_mtime_ns]:
            raise ValueError('素材在检索期间发生变化：'+str(source))
        folder = cache/'windows'/window['id']
        vector_path = folder/'embedding.json'
        cached = False
        if vector_path.is_file():
            saved = json.loads(vector_path.read_text())
            vector = checked_vector(saved['vector'])
            paths = saved['frames']
            window['requestedFrameTimes'] = list(window['frameTimes'])
            window['frameTimes'] = saved.get('frameTimes', window['frameTimes'])
            cached = all(Path(p).is_file() for p in paths)
        if cached:
            reused += 1
        else:
            # Re-materializing a missing JPEG can select a different frame at a
            # container tail. Recompute only this window, so its vector, pixels
            # and reported times stay one evidence unit. Intact caches survive.
            paths, frames = frames_for(window, folder, payload['ffmpeg'])
            vector = model().video(frames, window['frameTimes'])
            write(vector_path, {'vector': vector.tolist(), 'frames': paths,
                                'frameTimes': window['frameTimes'],
                                'sourceFingerprint': window['sourceFingerprint'],
                                'modelKey': model_key, 'revision': REVISION})
        if vectors and len(vector) != len(vectors[0]):
            raise ValueError('缓存视频向量维数不一致')
        vectors.append(vector)
        window['frames'] = paths
        windows.append(window)
        progress(f'视频片段检索 {index}/{len(payload["windows"])}：{source.name} {window["start"]:.1f}–{window["end"]:.1f} 秒')
    if not vectors:
        raise ValueError('没有建立任何视频向量')
    matrix = np.stack(vectors)
    queries, query_reused = [], 0
    for index, original in enumerate(payload['queries'], 1):
        query = dict(original)
        vector_path = cache/'queries'/(query['id']+'.json')
        if vector_path.is_file():
            vector = checked_vector(json.loads(vector_path.read_text())['vector'], matrix.shape[1])
            query_reused += 1
        else:
            vector = checked_vector(model().text(query['text']), matrix.shape[1])
            write(vector_path, {'vector': vector.tolist(), 'text': query['text'], 'modelKey': model_key})
        scores = matrix @ vector
        query['hits'] = [{'windowId': windows[i]['id'], 'score': round(float(scores[i]), 7)}
                         for i in np.argsort(-scores, kind='stable')]
        queries.append(query)
        progress(f'脚本画面检索查询 {index}/{len(payload["queries"])}：{query["text"][:50]}')
    result = {'status': 'complete', 'revision': REVISION, 'modelPath': str(path),
              'modelKey': model_key, 'modelName': 'Qwen3-VL-Embedding-2B (MLX 4-bit)',
              'backend': 'mlx-vlm', 'backendVersion': importlib.metadata.version('mlx-vlm'),
              'vectorDimensions': matrix.shape[1], 'windows': windows, 'queries': queries,
              'reusedWindowCount': reused, 'reusedQueryCount': query_reused,
              'elapsedSeconds': round(time.monotonic()-started, 3),
              'scoresAreProbabilities': False, 'editorialVerified': False}
    write(destination, result)
    progress(f'视频检索完成：{len(windows)} 个片段、{len(queries)} 个查询；复用 {reused} 个片段缓存')


if __name__ == '__main__':
    run(json.loads(Path(sys.argv[1]).read_text()), Path(sys.argv[2]))
