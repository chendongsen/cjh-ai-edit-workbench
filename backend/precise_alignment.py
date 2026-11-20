"""Local, audio-evidenced word alignment in an isolated MLX process.

Only already observed transcriptions are passed to the audio models. Script text
is never a model input. Disagreement or invalid times retain every original unit.
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import time

from common import DATA, read, write
from editorial import norm

REVISION = 'precise-alignment-1'
APPLICATION_REVISION = 'precise-application-3'
CONFIG = DATA / 'semantic-engines.json'


def enabled(request):
    return request.get('preciseAlignment', read(CONFIG, {}).get('preciseAlignment', False)) is True


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def _span(row):
    return (_finite(row.get('start')) and _finite(row.get('end'))
            and 0 <= row['start'] < row['end'])


def choose_text(current, qwen, references=()):
    """Independent audio agreement, never similarity to the editing script."""
    observed = norm(qwen)
    if not observed:
        return None, '新语音模型没有返回可核对文字，保留原句'
    if observed == norm(current):
        return qwen, 'audio-agreement'
    # A separate stored SenseVoice / Whisper pass must cover the WHOLE group.
    if len(observed) >= 2 and any(observed == norm(t) for t in references if isinstance(t, str)):
        return qwen, 'independent-audio-consensus'
    return None, '新旧音频转写不一致，保留原句并列出新候选待听审'


def validate_words(words, text, start, end):
    """Reject missing/extra words, zero widths, overlap and out-of-window times."""
    if not words or not _finite(start) or not _finite(end) or end <= start:
        raise ValueError('没有完整词时间证据')
    previous = start
    valid = []
    for word in words:
        if not isinstance(word, dict) or not isinstance(word.get('text'), str):
            raise ValueError('词时间结果格式错误')
        if not norm(word['text']):
            continue
        a, b = word.get('start'), word.get('end')
        if (not _finite(a) or not _finite(b) or not start-1e-6 <= a < b <= end+1e-6
                or a < previous-1e-6):
            raise ValueError('词时间重叠、零长度或越过原音频窗口')
        valid.append({'text': word['text'], 'start': a, 'end': b,
                      'timing': 'Qwen3-ForcedAligner / observed audio agreement'})
        previous = b
    if not valid or norm(''.join(w['text'] for w in valid)) != norm(text):
        raise ValueError('对齐词没有完整覆盖音频转写；未补造或丢弃文字')
    return valid


def parse_raw_timestamps(word_list, timestamps):
    """Keep the model's raw time classes; never call upstream interpolation."""
    values = [float(t) for t in timestamps]
    if len(values) != 2*len(word_list) or any(not math.isfinite(t) for t in values):
        raise ValueError('原始对齐时间数量错误或非有限数')
    return [{'text': word, 'start_time': values[2*i], 'end_time': values[2*i+1]}
            for i, word in enumerate(word_list)]


def _origin_key(row, index):
    review = row.get('asrReview') or {}
    if _finite(review.get('start')) and _finite(review.get('end')):
        return ('review', review['start'], review['end'])
    if row.get('id'):
        return ('id', row['id'])
    return ('unit', index)


def groups_for(item):
    """Rejoin acoustic pieces from one original utterance before recognition."""
    rows = sorted(enumerate(item.get('segments', [])), key=lambda pair: pair[1].get('start', -1))
    groups = []
    for original_index, row in rows:
        if not isinstance(row, dict) or not _span(row) or not isinstance(row.get('text'), str):
            continue
        key = _origin_key(row, original_index)
        # Never join different source IDs or bridge another utterance in between.
        if groups and groups[-1]['originKey'] == key and row['start'] >= groups[-1]['end']-1e-6:
            group = groups[-1]
            group['rows'].append(copy.deepcopy(row)); group['indices'].append(original_index)
            group['end'] = row['end']
        else:
            groups.append({'originKey': key, 'indices': [original_index], 'rows': [copy.deepcopy(row)],
                           'start': row['start'], 'end': row['end']})
    for group in groups:
        group['text'] = ''.join(r['text'] for r in group['rows'])
        group['references'] = list(dict.fromkeys(t for r in group['rows']
            for k in ('senseVoice', 'whisperRetry')
            if isinstance((t := (r.get('asrReview') or {}).get(k)), str) and norm(t)))
    return groups


def reasons_for(group):
    reasons = []
    rows = group['rows']
    words = [w for r in rows for w in r.get('words', []) if isinstance(w, dict) and norm(w.get('text', ''))]
    try:
        validate_words(words, group['text'], group['start'], group['end'])
    except ValueError:
        reasons.append('原词时间缺失或不完整')
    if group['end'] - group['start'] > 8:
        reasons.append('长语句')
    if any(w.get('probability', 1) is not None and _finite(w.get('probability', 1))
           and w.get('probability', 1) < .65 for w in words):
        reasons.append('低置信度词')
    if any(_finite(w.get('start')) and _finite(w.get('end')) and
           w['end']-w['start'] > 1.8 and len(norm(w['text'])) <= 3 for w in words):
        reasons.append('原词时间被拉长')
    if any((r.get('asrReview') or {}).get('decision') in
           ('disagreement-kept-original', 'audio-consensus-realignment') for r in rows):
        reasons.append('已有转写分歧或时间修复待复核')
    if len(rows) > 1:
        reasons.append('原句含多个声学碎片')
    return reasons


def _model_key(path):
    root = Path(path).expanduser().resolve()
    if not root.is_dir() or not (root / 'config.json').is_file():
        raise ValueError('本地语音模型尚未安装完整：' + root.name)
    files = [(str(p.relative_to(root)), p.stat().st_size, p.stat().st_mtime_ns)
             for p in sorted(root.rglob('*')) if p.is_file()]
    if not any(name.endswith('.safetensors') for name, _, _ in files):
        raise ValueError('本地语音模型缺少权重：' + root.name)
    return hashlib.sha256(json.dumps([str(root), files], ensure_ascii=False).encode()).hexdigest()


def build_tasks(items, request, config, cache):
    model_key = [_model_key(config[k]) for k in ('asrModel', 'alignerModel')]
    tasks, mapping, skipped = [], {}, []
    for item in items:
        if not item.get('audio') or not item.get('segments'):
            continue
        # Include script-audio candidates even when a coarse visual label is wrong,
        # but don't process arbitrary ambient/B-roll chatter without voice evidence.
        if not item.get('voiceGuard'):
            continue
        duration = item.get('usableDuration', item.get('duration', 0))
        if not _finite(duration) or duration <= 0:
            continue
        source = Path(item['source']); stat = source.stat()
        groups = groups_for(item)
        for index, group in enumerate(groups):
            reasons = reasons_for(group)
            if request.get('preciseAlignmentAll') is not True and not reasons:
                continue
            if not norm(group['text']):
                continue
            # Whole utterances only. No invented split point inside a long sentence.
            if group['end']-group['start'] > 90 or group['end'] > duration+1e-6:
                skipped.append({'assetId': item['assetId'], 'start': group['start'], 'end': group['end'],
                                'reason': '原句过长或越界，未强行切开对齐', 'text': group['text']})
                continue
            left = max(0., group['start']-.12)
            right = min(duration, group['end']+.12)
            if index:
                left = max(left, groups[index-1]['end'])
            if index+1 < len(groups):
                right = min(right, groups[index+1]['start'])
            if left > group['start']+1e-6 or right < group['end']-1e-6:
                skipped.append({'assetId': item['assetId'], 'start': group['start'], 'end': group['end'],
                                'reason': '原语句与邻句重叠，保留待听审', 'text': group['text']})
                continue
            key = hashlib.sha256(json.dumps([REVISION, str(source.resolve()), stat.st_size,
                stat.st_mtime_ns, item.get('fingerprint'), group, left, right, model_key],
                sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            task = {'key': key, 'assetId': item['assetId'], 'source': str(source),
                    'start': left, 'end': right, 'speechStart': group['start'], 'speechEnd': group['end'],
                    'currentText': group['text'], 'references': group['references'],
                    'cachePath': str(cache / (key + '.json')), 'reasons': reasons or ['全句对齐对照']}
            tasks.append(task); mapping[key] = group
    limit = request.get('preciseAlignmentLimit')
    if type(limit) is int and limit > 0:
        tasks = tasks[:limit]
    return tasks, mapping, skipped


class BoundaryRetention(ValueError):
    """Inference succeeded, but original editorial boundaries must be retained."""


def boundary_retention_reason(group):
    from voice_guard import COMMAND
    rows = group['rows']
    if any(r.get('voiceRole') == 'director' and r.get('voiceStable') is True for r in rows):
        return '原句已有稳定的编导候选证据，保留原分段及原删除门槛；新词时间仅供复核'
    if len(rows) < 2:
        return None
    commands = [bool(COMMAND.search(norm(r.get('text', '')))) for r in rows]
    if any(commands) and not all(commands):
        return '原句已独立分开拍摄指令/倒数与正式台词，保留原分段；不把指令合回口播'
    roles = {r.get('voiceRole', 'unknown') for r in rows}
    if len(roles) > 1:
        return '原句跨不同候选说话人边界，保留原分段及候选身份警示；不把混合身份当无风险'
    return None


def apply_result(group, task, result, protected_windows=()):
    """Return replacement only when fresh audio agrees and timing covers ALL text."""
    if result.get('key') != task['key']:
        raise ValueError('对齐缓存与当前原声不匹配')
    if result.get('status') != 'aligned':
        return None
    text, basis = choose_text(group['text'], result.get('qwenText', ''), group['references'])
    if not text:
        raise ValueError('对齐结果没有独立音频文字支持')
    words = validate_words(result.get('words'), text, task['start'], task['end'])
    boundary_reason = boundary_retention_reason(group)
    if boundary_reason:
        raise BoundaryRetention(boundary_reason)
    # A word aligner observes speech, not the preceding smile/laugh or trailing
    # non-verbal reply. Keep the part of each already-observed protection window
    # belonging to this original utterance; never extend into another utterance.
    protected = []
    for event in protected_windows:
        if not isinstance(event, (list, tuple)) or len(event) < 2:
            continue
        a, b = event[:2]
        if not _finite(a) or not _finite(b) or b <= a:
            continue
        left, right = max(group['start'], a), min(group['end'], b)
        if right > left:
            protected.append({'start': left, 'end': right,
                              'reason': str(event[2]) if len(event) > 2 else '既有反应保护'})
    envelope_start = min([words[0]['start']] + [p['start'] for p in protected])
    envelope_end = max([words[-1]['end']] + [p['end'] for p in protected])
    roles = {r.get('voiceRole', 'unknown') for r in group['rows']}
    role = next(iter(roles)) if len(roles) == 1 else 'mixed'
    base = copy.deepcopy(group['rows'][0])
    identifiers = list(dict.fromkeys(r.get('id') for r in group['rows'] if r.get('id')))
    base.update(start=envelope_start, end=envelope_end, text=text, words=words,
                voiceRole=role, voiceStable=False,
                id=identifiers[0] if len(identifiers) == 1 else 'alignment-'+task['key'][:24],
                originalText=group['text'])
    if len(group['rows']) > 1:
        base['speaker'] = 'mixed/uncertain' if role in ('unknown', 'mixed') else base.get('speaker', role)
    prior = copy.deepcopy(base.get('asrReview') or {})
    base['asrReview'] = {'original': group['text'], 'chosen': text,
        'qwen3': result['qwenText'], 'decision': 'precise-alignment-'+basis,
        'start': group['start'], 'end': group['end'], 'priorReview': prior,
        'timingModel': 'Qwen3-ForcedAligner-0.6B', 'needsListeningReview': True}
    base['preciseAlignment'] = {'revision': REVISION, 'cacheKey': task['key'], 'basis': basis,
        'sourceSegmentIds': identifiers, 'sourceSegmentIndices': group['indices'],
        'originalStart': group['start'], 'originalEnd': group['end'],
        'applicationRevision': APPLICATION_REVISION, 'protectedSpans': protected,
        'wordSpeechStart': words[0]['start'], 'wordSpeechEnd': words[-1]['end'],
        'timingIsModelEstimate': True, 'audioOnly': True, 'scriptWasModelInput': False}
    return base


def _worker(manifest, phase):
    process = subprocess.Popen([manifest['python'], str(Path(__file__).with_name('precise_alignment_worker.py')),
                                manifest['manifestPath']], stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, text=True, bufsize=1,
                               env={**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
                                    'TOKENIZERS_PARALLELISM': 'false', 'PYTHONDONTWRITEBYTECODE': '1'})
    lines = []
    try:
        for line in process.stdout:
            line = line.rstrip(); lines.append(line); lines = lines[-30:]
            if line.startswith('CJH_PROGRESS '):
                message = json.loads(line[len('CJH_PROGRESS '):]).get('message', '')
                if phase: phase(message)
                else: print(message, flush=True)
        if process.wait() != 0:
            raise RuntimeError('本地精确对齐未完成：' + '\n'.join(lines)[-3000:])
    except BaseException:
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill(); process.wait()
        raise


def run(items, request, output, phase=None, runner=None):
    """Call after refine_speech, before select; phase receives one progress string."""
    if not enabled(request):
        return items, {'revision': REVISION, 'status': 'disabled'}
    started = time.monotonic(); output = Path(output); output.mkdir(parents=True, exist_ok=True)
    config = read(CONFIG, {})
    for key in ('python', 'asrModel', 'alignerModel'):
        if not config.get(key):
            raise ValueError('精确对齐尚未配置本地运行环境：' + key)
    if not Path(config['python']).is_file():
        raise ValueError('精确对齐运行环境不存在，请修复本地模块安装')
    cache = DATA / 'precise-alignment-cache'; cache.mkdir(parents=True, exist_ok=True)
    tasks, mapping, skipped = build_tasks(items, request, config, cache)
    manifest = {k: config[k] for k in ('python', 'asrModel', 'alignerModel')}
    import imageio_ffmpeg
    manifest.update(revision=REVISION, tasks=tasks, ffmpeg=imageio_ffmpeg.get_ffmpeg_exe(),
                    manifestPath=str(output / 'precise-alignment-input.json'))
    write(Path(manifest['manifestPath']), manifest)
    cache_hits = sum(bool((cached := read(Path(t['cachePath']), {})).get('key') == t['key']
                         and cached.get('revision') == REVISION
                         and cached.get('status') in ('aligned', 'disagreement')) for t in tasks)
    if tasks:
        (runner or _worker)(manifest, phase)
    result_items = copy.deepcopy(items)
    report = {'revision': REVISION, 'applicationRevision': APPLICATION_REVISION, 'status': 'complete', 'audioOnly': True, 'scriptWasModelInput': False,
              'total': len(tasks), 'aligned': 0, 'disagreements': 0, 'failed': 0, 'boundaryRetained': 0, 'cacheHits': cache_hits,
              'skipped': skipped, 'rows': [], 'warnings': [],
              'policy': '仅对独立音频转写一致的完整语句更新词时间；分歧保留原句；未自动确认角色或删声音。'}
    by_asset = {i['assetId']: i for i in result_items}
    replacements = {}
    for task in tasks:
        group = mapping[task['key']]
        result = read(Path(task['cachePath']), {})
        row = {'assetId': task['assetId'], 'source': task['source'],
               'start': group['start'], 'end': group['end'], 'originalText': group['text'],
               'qwenText': result.get('qwenText', ''), 'reasons': task['reasons'], 'cacheKey': task['key']}
        try:
            replacement = apply_result(group, task, result,
                by_asset[task['assetId']].get('voiceGuard', {}).get('protected', []))
            row.update(status=result.get('status', 'failed'), reason=result.get('reason', '对齐结果缺失'))
            if replacement:
                report['aligned'] += 1
                row.update(status='aligned', alignedStart=replacement['start'], alignedEnd=replacement['end'],
                           wordCount=len(replacement['words']), basis=replacement['preciseAlignment']['basis'],
                           wordSpeechStart=replacement['preciseAlignment']['wordSpeechStart'],
                           wordSpeechEnd=replacement['preciseAlignment']['wordSpeechEnd'],
                           protectedSpans=replacement['preciseAlignment']['protectedSpans'])
                replacements.setdefault(task['assetId'], []).append((group['indices'], replacement))
            elif result.get('status') == 'disagreement':
                report['disagreements'] += 1
            else:
                report['failed'] += 1
        except BoundaryRetention as error:
            row.update(status='boundary-retained', reason=str(error))
            report['boundaryRetained'] += 1
        except (ValueError, TypeError, KeyError) as error:
            row.update(status='failed', reason=str(error)); report['failed'] += 1
        report['rows'].append(row)
    for asset_id, groups in replacements.items():
        item = by_asset[asset_id]
        item['segmentsBeforePreciseAlignment'] = copy.deepcopy(item['segments'])
        removed = {index for indices, _ in groups for index in indices}
        updated = [copy.deepcopy(s) for index, s in enumerate(item['segments']) if index not in removed]
        updated.extend(row for _, row in groups); updated.sort(key=lambda s:s['start'])
        if any(a['end'] > b['start']+1e-6 for a,b in zip(updated, updated[1:])):
            # Whole-file rollback is safer than trimming a word to make rows fit.
            report['warnings'].append(Path(item['source']).name+'：对齐后邻句重叠，已保留该素材原分段')
            for row in report['rows']:
                if row['assetId'] == asset_id and row['status'] == 'aligned':
                    row.update(status='rolled-back', reason='邻句重叠，保留整份原分段')
                    report['aligned'] -= 1; report['failed'] += 1
            continue
        item['segments'] = updated
        guard = item.get('voiceGuard', {})
        old_utterances = guard.get('utterances', [])
        guard['utterances'] = [u for u in old_utterances
            if not any(abs(u['start']-old['start']) < .001 and abs(u['end']-old['end']) < .001
                       for index, old in enumerate(item['segmentsBeforePreciseAlignment']) if index in removed)]
        guard['utterances'].extend({'start': row['start'], 'end': row['end'], 'role': row.get('voiceRole', 'unknown'),
            'similarities': row.get('similarities', {}), 'stable': False} for _, row in groups)
        guard['requireStable'] = True
        item['preciseAlignment'] = {'revision': REVISION, 'alignedGroups': len(groups),
                                    'report': str(output/'precise-alignment-report.json')}
    report['elapsedSeconds'] = round(time.monotonic()-started, 3)
    if report['aligned'] == 0 and tasks:
        report['status'] = 'review-only' if report['disagreements'] or report['boundaryRetained'] else 'failed'
    elif report['failed'] or report['disagreements'] or report['boundaryRetained'] or skipped:
        report['status'] = 'partial'
    if report['boundaryRetained']:
        report['warnings'].append(f"{report['boundaryRetained']} 句保留原拍摄指令或候选说话人边界；新对齐结果只供复核，未合并原句。")
    if report['failed'] or report['disagreements'] or skipped:
        report['warnings'].append(f"精确对齐有 {report['disagreements']} 句转写分歧、{report['failed']} 句失败、{len(skipped)} 句未处理；均保留原句待听审。")
    write(output / 'precise-aligned-items.json', {'items': result_items})
    write(output / 'precise-alignment-report.json', report)
    return result_items, report
