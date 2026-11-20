"""Real local media probing, thumbnails, ASR; optional local Ollama vision."""
import base64
import hashlib
import json
import telemetry
import sys
import subprocess
import urllib.request
import uuid
from pathlib import Path
import av
import imageio_ffmpeg
from PIL import Image, ImageOps
from common import DATA, ROOT, RESOLVE, OLLAMA_URL, read, write, local_urlopen


def progress(text):
    print(text, flush=True)


def probe(path):
    with av.open(str(path)) as c:
        video = next(iter(c.streams.video), None)
        # Container duration may include an edit-list start offset (common in phone MOVs).
        # Source in/out points are relative to the streams, not that padded container clock.
        spans = [float(s.duration * s.time_base) for s in c.streams
                 if s.type in ('video', 'audio') and s.duration and s.time_base]
        return {'duration': float(c.duration or 0) / av.time_base,
                'usableDuration': min(spans) if spans else float(c.duration or 0) / av.time_base,
                'fps': float(video.average_rate or 30) if video else 0,
                'width': video.width if video else 0, 'height': video.height if video else 0,
                'audio': bool(c.streams.audio), 'video': bool(video)}


def frames(path, duration, output):
    found = []
    for i, ratio in enumerate((.15, .5, .85)):
        at = max(0, duration * ratio)
        filename = f'{i}.jpg'
        if Path(path).suffix.lower() in ('.jpg', '.jpeg', '.png'):
            with Image.open(path) as original:
                still = ImageOps.exif_transpose(original).convert('RGB')
                still.thumbnail((640, 640))
                still.save(output / filename, quality=90)
            found.append({'at': 0, 'file': str((output / filename).relative_to(DATA))})
            continue
        # FFmpeg honors phone display rotation; decoded raw AV frames do not.
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
            '-y', '-ss', str(at), '-i', str(path), '-frames:v', '1', '-vf',
            'scale=640:640:force_original_aspect_ratio=decrease', '-q:v', '3', str(output / filename)],
            check=True, timeout=60)
        if not (output / filename).is_file():
            raise ValueError('未能提取画面：' + str(path))
        found.append({'at': at, 'file': str((output / filename).relative_to(DATA))})
    return found


def vision(model, paths, script):
    if not model or 'cloud' in model.lower():
        raise ValueError('只允许已安装的本地视觉模型，不能使用 cloud 模型')
    payload = {'model': model, 'stream': False, 'format': 'json', 'options': {'temperature': 0.1, 'num_ctx': 8192, 'num_predict': 512}, 'messages': [{
        'role': 'user', 'content': '分析这组同一素材的抽帧。只返回一个简短JSON对象：summary(不超过50字的客观描述)、kind(人物/食物/环境/不确定)、dishCandidates(最多3个菜名候选，不确定则空数组)、actions(最多3个可见动作)。不要重复列表，不要根据脚本猜看不见的菜，不要辨认人物身份。脚本仅作词汇参考：' + script[:6000],
        'images': [base64.b64encode(p.read_bytes()).decode() for p in paths]}]}
    request = urllib.request.Request(OLLAMA_URL + '/api/chat', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with local_urlopen(request, timeout=600) as r:
        return json.loads(telemetry.ollama(json.loads(r.read()))['message']['content'])


def run(request, output):
    results, model = [], None
    # Only this task's completed items may be resumed; never borrow an old editorial plan.
    previous = {i['assetId']: i for i in read(output / 'result.json', {}).get('items', [])} if request.get('resume') else {}
    shared = {}
    if request.get('unattended'):
        # Reuse raw analysis only. Every path/stat/config fingerprint is revalidated below.
        for path in (DATA/'jobs').glob('*/analysis/result.json'):
            for item in read(path,{}).get('items',[]):
                if item.get('fingerprint') and not item.get('error') and not any('失败' in w for w in item.get('warnings',[])):
                    if all((DATA/f['file']).is_file() for f in item.get('frames',[])):
                        shared[item['fingerprint']]=item
    config = {k: request.get(k) for k in ('model', 'asrEngine', 'visionModel', 'transcribe', 'script')}
    resolved = None
    if request.get('asrEngine') == 'resolve' and request.get('transcribe', True):
        subprocess.run([RESOLVE, str(ROOT / 'backend/resolve_transcribe.py'), str(output / 'input.json')], check=True)
        resolved = read(output / 'resolve-transcript.json')
    for index, a in enumerate(request['assets']):
        progress(f'读取素材 {index + 1}/{len(request["assets"])}：{a["name"]}')
        stat = Path(a['path']).stat()
        fingerprint = hashlib.sha256(json.dumps([a['path'], stat.st_size, stat.st_mtime_ns, config], sort_keys=True).encode()).hexdigest()
        old = previous.get(a['id'], {})
        if old.get('fingerprint') != fingerprint:
            old = shared.get(fingerprint,{})
        if old.get('fingerprint') == fingerprint and not old.get('error') and not any('失败' in w for w in old.get('warnings', [])):
            telemetry.event('cache', 'analysis_reused', count=1)
            results.append(old)
            progress('复用指纹一致的已完成分析：' + a['name'])
            write(output / 'result.json', {'items': results})
            continue
        item = {'assetId': a['id'], 'source': a['path'], 'segments': [], 'warnings': []}
        item['fingerprint'] = fingerprint
        try:
            info = probe(a['path'])
            item.update(info)
            folder = output / a['id']
            folder.mkdir(exist_ok=True)
            if info['video']:
                item['frames'] = frames(a['path'], info['duration'], folder)
                if request.get('visionModel'):
                    progress('本地视觉模型分析：' + a['name'])
                    try:
                        item['vision'] = vision(request['visionModel'], [DATA / f['file'] for f in item['frames']], request.get('script', ''))
                    except Exception as e:
                        item['warnings'].append('视觉分析失败：' + str(e))
            if info['audio'] and request.get('transcribe', True):
                if resolved is not None:
                    item['segments'] = resolved.get(a['id'], [])
                    item['warnings'].append('达芬奇已请求说话人检测；相同标签仍可能误合并，标签不是达人/编导身份。')
                    results.append(item)
                    write(output / 'result.json', {'items': results})
                    continue
                if model is None:
                    from faster_whisper import WhisperModel
                    size = request.get('model', 'base')
                    if size not in ('tiny', 'base', 'small'):
                        raise ValueError('不支持的语音模型')
                    offline = bool(request.get('autoLaunchEditors'))
                    progress(f'加载本地 Whisper {size} 模型；' + ('只读已安装模型，不下载、不上传素材' if offline else '未缓存时需下载模型，不上传素材'))
                    with telemetry.measure('whisper'):
                        model = WhisperModel(size, device='cpu', compute_type='int8', cpu_threads=4,
                                             download_root=str(DATA / 'models'), local_files_only=offline)
                progress('转写原声：' + a['name'])
                segments, meta = model.transcribe(a['path'], language='zh', vad_filter=True,
                    initial_prompt=request.get('script', '')[:1000], condition_on_previous_text=False,
                    word_timestamps=True)
                for s in segments:
                    item['segments'].append({'id': str(uuid.uuid4()), 'assetId': a['id'],
                        'source': a['path'], 'speaker': '待确认', 'start': round(s.start, 3),
                        'end': round(s.end, 3), 'text': s.text.strip(), 'decision': '待确认',
                        'words': [{'start': round(w.start, 3), 'end': round(w.end, 3),
                                   'text': w.word, 'probability': round(w.probability, 3)} for w in (s.words or [])]})
                    progress(f'{a["name"]} 已转写至 {s.end:.1f} 秒 / {info["duration"]:.1f} 秒')
                item['warnings'].append('本次只做语音转写，未执行说话人分离；达人/编导身份需人工确认。')
        except Exception as e:
            item['error'] = str(e)
        results.append(item)
        write(output / 'result.json', {'items': results})
    progress('分析结束；结果中有错误的素材不会冒充分析成功')


if __name__ == '__main__':
    source = Path(sys.argv[1])
    run(read(source), source.parent)
