"""Local, cached video-window retrieval. Scores propose; they never confirm edits.

The MLX worker uses a separate interpreter so the packaged editor's NumPy,
PyTorch and ASR dependencies remain untouched. No media is sent to a service.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import signal
import subprocess
import threading
import time

from common import DATA, read, write

REVISION = 'video-retrieval-2'
CONFIG = DATA / 'semantic-engines.json'
REPORT = 'video-retrieval.json'


def settings(request=None):
    config = dict(read(CONFIG, {}) or {})
    request = request or {}
    if 'videoRetrieval' in request:
        config['videoRetrieval'] = request['videoRetrieval'] is True
    return config


def enabled(request=None):
    return settings(request).get('videoRetrieval') is True


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def _duration(item):
    values = [item.get('duration'), item.get('usableDuration', item.get('duration'))]
    if not all(_number(x) and x > 0 for x in values):
        raise ValueError('视频检索素材时长无效：' + str(item.get('source')))
    return min(values)


def source_fingerprint(path):
    path = Path(path).resolve()
    stat = path.stat()
    return [str(path), stat.st_size, stat.st_mtime_ns]


def _key(text):
    return hashlib.sha256(' '.join(text.split()).encode()).hexdigest()


def broll_query(block):
    themes = sorted(set(block.get('tags', [])) | set(block.get('topics', [])))
    text = block['clip']['text'].strip()
    return text + ('；画面主题：' + '、'.join(themes) if themes else '')


def collect_queries(preparation, plan, items):
    """Use actual selected speech and literal script components, never invented lines."""
    from broll import proposals
    from script_shots import _decompose_requirement
    queries = {}
    def add(text, kind):
        if isinstance(text, str) and text.strip():
            text = ' '.join(text.split())
            queries.setdefault(_key(text), {'id': _key(text), 'text': text, 'kinds': []})
            if kind not in queries[_key(text)]['kinds']:
                queries[_key(text)]['kinds'].append(kind)
    for requirement in preparation.get('shotRequirements', []):
        add(requirement.get('text'), 'script-shot')
        for component in _decompose_requirement(requirement)[0]:
            add(component, 'script-shot-component')
    for block in proposals(plan or {'selectedSpeech': []}, items):
        add(broll_query(block), 'broll')
    return list(queries.values())


def windows_for(item, seconds=6., stride=4., frames=4):
    """Cover every video, including static food and tails; boundaries are proposals."""
    if not item.get('video') or item.get('excluded') is True:
        return []
    duration = _duration(item)
    if duration < 1.2:
        return []
    seconds, stride = float(seconds), float(stride)
    if not 1.2 <= seconds <= 12 or not 0 < stride <= seconds:
        raise ValueError('视频检索分窗参数无效')
    if not 3 <= frames <= 8:
        raise ValueError('视频检索每个片段必须有 3～8 个有序抽帧')
    starts = [i * stride for i in range(max(1, math.ceil(max(0, duration-seconds)/stride)+1))]
    result = []
    fingerprint = source_fingerprint(item['source'])
    for start in starts:
        end = min(duration, start+seconds)
        if end-start < 1.2:
            continue
        # The last decoded frame is strictly before the out point.
        fps = item.get('fps', 30)
        # Some phone MOVs have an audio/container tail after the final video
        # frame. Stay inside the window instead of seeking its nominal EOF.
        margin = max(.15, 2/max(1., fps)) if _number(fps) else .15
        last = max(start, end-margin)
        times = [round(start + (last-start)*i/(frames-1), 6) for i in range(frames)]
        identity = [REVISION, fingerprint, round(start, 6), round(end, 6), times]
        result.append({'id': hashlib.sha256(json.dumps(identity).encode()).hexdigest(),
                       'assetId': item['assetId'], 'source': str(Path(item['source']).resolve()),
                       'sourceFingerprint': fingerprint, 'start': round(start, 6),
                       'end': round(end, 6), 'frameTimes': times})
    return result


def _stop(process):
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)


def _worker(python, payload, destination, log, cancel=None, timeout=14400):
    environment = dict(os.environ, HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1',
                       HF_HUB_DISABLE_TELEMETRY='1', TOKENIZERS_PARALLELISM='false')
    process = subprocess.Popen([str(python), '-u', str(Path(__file__).with_name('video_retrieval_worker.py')),
                                str(payload), str(destination)], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, env=environment,
                               start_new_session=True, bufsize=1)
    lines, tail = queue.Queue(), []
    def consume():
        for line in process.stdout:
            lines.put(line.rstrip())
        lines.put(None)
    reader = threading.Thread(target=consume, daemon=True)
    reader.start()
    started = time.monotonic()
    ended = False
    try:
        while not ended or process.poll() is None:
            if cancel and cancel():
                raise RuntimeError('视频片段检索已取消；已完成片段缓存可复用')
            if time.monotonic()-started > timeout:
                raise TimeoutError('视频片段检索超时；未使用旧检索冒充完成')
            try:
                line = lines.get(timeout=.5)
            except queue.Empty:
                continue
            if line is None:
                ended = True
                continue
            tail = (tail+[line])[-12:]
            if line.startswith('CJH_RETRIEVAL '):
                log(line[len('CJH_RETRIEVAL '):])
        if process.wait() != 0:
            raise RuntimeError('本地视频检索未完成：'+'\n'.join(tail)[-3000:])
    finally:
        _stop(process)
        process.stdout.close()


def prepare(items, preparation, request, output, log=print, plan=None, cancel=None):
    """Build reusable video/query vectors and job-local rankings once per job."""
    config = settings(request)
    if config.get('videoRetrieval') is not True:
        return {'status': 'disabled', 'revision': REVISION}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    report_path = output/REPORT
    report = {'revision': REVISION, 'status': 'running', 'scoresAreProbabilities': False,
              'editorialVerified': False, 'warnings': []}
    write(report_path, report)
    started = time.monotonic()
    try:
        python = Path(config.get('python') or '').expanduser()
        model = Path(config.get('embeddingModel') or '').expanduser()
        if not python.is_file() or not model.is_dir() or not (model/'config.json').is_file():
            raise ValueError('视频检索已开启，但独立运行环境或本地检索模型未就绪')
        import imageio_ffmpeg
        windows = [w for item in items for w in windows_for(item)]
        queries = collect_queries(preparation, plan, items)
        if not windows:
            raise ValueError('没有可供视频检索的有效视频素材')
        if not queries:
            raise ValueError('没有实际入选台词或脚本镜头要求，无法建立检索查询')
        payload = {'revision': REVISION, 'model': str(model.resolve()),
                   'ffmpeg': imageio_ffmpeg.get_ffmpeg_exe(),
                   'cacheRoot': str((DATA/'video-retrieval-cache').resolve()),
                   'windows': windows, 'queries': queries}
        request_path = output/'video-retrieval-input.json'
        result_path = output/'video-retrieval-worker-result.json'
        write(request_path, payload)
        _worker(python, request_path, result_path, log, cancel)
        result = read(result_path, {})
        if result.get('revision') != REVISION or result.get('status') != 'complete':
            raise ValueError('视频检索没有返回完整结果')
        expected = {w['id'] for w in windows}
        if {w['id'] for w in result.get('windows', [])} != expected:
            raise ValueError('视频检索片段数量不完整')
        if {q['id'] for q in result.get('queries', [])} != {q['id'] for q in queries}:
            raise ValueError('视频检索查询数量不完整')
        report.update(result)
        report.update(status='complete', elapsedSeconds=round(time.monotonic()-started, 3),
                      sourceCount=len({w['assetId'] for w in windows}), windowCount=len(windows),
                      queryCount=len(queries), scoresAreProbabilities=False, editorialVerified=False)
        write(report_path, report)
        return report
    except BaseException as error:
        report.update(status='failed', error=str(error), elapsedSeconds=round(time.monotonic()-started, 3))
        write(report_path, report)
        raise


def _ranked(query, items, output):
    report = read(Path(output)/REPORT, {})
    if report.get('status') != 'complete' or report.get('revision') != REVISION:
        raise ValueError('视频检索未完成，不能读取候选')
    found = next((q for q in report.get('queries', []) if q['id'] == _key(query)), None)
    if found is None:
        raise ValueError('此镜头查询没有完成本地视频检索：'+query[:80])
    lookup = {i['assetId']: i for i in items}
    windows = {w['id']: w for w in report['windows']}
    fingerprints = {}
    for hit in found['hits']:
        window = windows[hit['windowId']]
        item = lookup.get(window['assetId'])
        if item is None or item.get('excluded') is True:
            continue
        if not _number(hit['score']):
            raise ValueError('视频检索分数无效')
        identifier = item['assetId']
        if identifier not in fingerprints:
            fingerprints[identifier] = source_fingerprint(item['source'])
        if fingerprints[identifier] != window['sourceFingerprint']:
            raise ValueError('素材在视频检索后发生变化，请重新分析：'+str(item['source']))
        yield item, window, hit['score']


def _subwindows(window, fps, seconds):
    """A retrieved event may be at the centre or tail, not only the in-point."""
    left = math.ceil(window['start']*fps)/fps
    right = math.floor(window['end']*fps)/fps
    length = math.floor(min(seconds, right-left)*fps)/fps
    if length < 1.2:
        return []
    positions = [('center', (left+right-length)/2), ('front', left), ('tail', right-length)]
    result, seen = [], set()
    for label, raw in positions:
        start = min(math.ceil((raw-1e-8)*fps)/fps, right-length)
        end = min(right, round((start+length)*fps)/fps)
        if round(start*fps) in seen:
            continue
        seen.add(round(start*fps))
        result.append((label, start, end))
    return result


def _diverse_windows(rows, limit=6):
    first, rest, used = [], [], set()
    for row in rows:
        identifier = row[0]['assetId']
        (rest if identifier in used else first).append(row)
        used.add(identifier)
    return (first+rest)[:limit]


def _expand_windows(rows, query, fps, seconds):
    # First try two different sources at their centres, then their front/tail
    # alternatives. Six visual checks can localize either of the top two hits.
    # Additional hits are bounded; a query never creates hundreds of VLM calls.
    rows = _diverse_windows(rows)
    result = []
    for offset in range(0, len(rows), 2):
        group = []
        for item, window, score in rows[offset:offset+2]:
            placements = _subwindows(window, fps, seconds)
            group.append([(item, window, score, p) for p in placements])
        result.extend(g[0] for g in group if g)
        result.extend(row for g in group for row in g[1:])
    for item, window, score, (label, start, end) in result[:18]:
        evidence = {'windowId': window['id'], 'sourceStart': window['start'],
                    'sourceEnd': window['end'], 'frameTimes': window['frameTimes'],
                    'query': query, 'placement': label, 'scoresAreProbabilities': False}
        at = (start+end)/2
        nearest = min(range(len(window['frameTimes'])), key=lambda i: abs(window['frameTimes'][i]-at))
        yield item, window, score, start, end, window['frames'][nearest], evidence


def retrieved_options(block, items, occupied, fps, output):
    """Same-scene V2 candidates, including formerly unlabelled video windows."""
    from broll import same_scene
    from editorial import dish_tags
    tags, topics = set(block.get('tags', [])), block.get('topics', [])
    dish = sorted(tags)[0] if tags else topics[0] if topics else '与口播对应的补充画面'
    kind = 'food' if tags or block.get('generic') else 'environment'
    rows = []
    for item, window, score in _ranked(broll_query(block), items, output):
        if (item['assetId'] == block['item']['assetId'] or not same_scene(block['item'], item)
                or item.get('foodReview')):
            continue
        named = dish_tags(Path(item['source']).stem)
        if tags and named and not tags.intersection(named):
            continue
        used = occupied.get(item['assetId'], [])
        if len(used) >= 3 or not any(not any(a < y and b > x for x, y in used)
                                   for _, a, b in _subwindows(window, fps, 3.2)):
            continue
        rows.append((item, window, score))
    result = []
    for item, window, score, start, end, frame, evidence in _expand_windows(rows, broll_query(block), fps, 3.2):
        if any(start < b and end > a for a, b in occupied.get(item['assetId'], [])):
            continue
        result.append({'item': item, 'start': start, 'maximum': end, 'dish': dish,
                       'brollKind': kind, 'confidence': 0, 'frame': frame,
                       'retrievalScore': score, 'semanticRetrieval': True,
                       'retrievalQuery': broll_query(block), 'retrievalWindowId': window['id'],
                       'retrievalEvidence': evidence,
                       'basis': 'Qwen3-VL 多帧片段检索候选；仍须主题与主线反应复核'})
    return result


def script_options(requirement, items, fps, seconds, output):
    result = []
    rows = list(_ranked(requirement['text'], items, output))
    for item, window, score, start, end, frame, evidence in _expand_windows(rows, requirement['text'], fps, seconds):
        result.append({'item': item, 'start': start, 'end': end, 'retrievalScore': score,
                       'retrievalBasis': 'Qwen3-VL 多帧视频检索候选（不是脚本画面确认）',
                       'frame': frame, 'semanticRetrieval': True,
                       'retrievalWindowId': window['id'], 'retrievalEvidence': evidence})
    return result
