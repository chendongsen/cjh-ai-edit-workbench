"""Isolated MLX audio worker. Local model paths and observed transcript only."""
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import threading
import wave

os.environ['HF_HUB_OFFLINE'] = '1'
os.environ['TRANSFORMERS_OFFLINE'] = '1'
os.environ['TOKENIZERS_PARALLELISM'] = 'false'
from precise_alignment import REVISION, choose_text, validate_words, parse_raw_timestamps


def progress(message):
    print('CJH_PROGRESS '+json.dumps({'message': message}, ensure_ascii=False), flush=True)


def save(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp'); temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(path)


def run(manifest):
    # Validate all local paths BEFORE importing or loading a model. Never fall
    # back to a Hub identifier or silently download after the local task starts.
    for key in ('asrModel', 'alignerModel'):
        path = Path(manifest[key])
        if not path.is_absolute() or not path.is_dir() or not (path/'config.json').is_file():
            raise ValueError('必须配置已安装的本地模型目录：'+key)
    tasks = manifest['tasks']
    pending = []
    for task in tasks:
        path = Path(task['cachePath'])
        existing = json.loads(path.read_text(encoding='utf-8')) if path.is_file() else {}
        if (existing.get('key') == task['key'] and existing.get('revision') == REVISION
                and existing.get('status') in ('aligned', 'disagreement')):
            continue
        pending.append(task)
    progress(f'台词精确对齐：{len(tasks)-len(pending)}/{len(tasks)} 句复用已核对缓存')
    if not pending:
        return
    from mlx_audio.stt import load
    import mlx.core as mx
    progress('正在加载本地 Qwen3-ASR；脚本不参与语音识别')
    waiting = threading.Event()
    state = {'label': '加载本地语音模型'}
    def heartbeat():
        while not waiting.wait(15):
            progress('台词精确对齐仍在处理：'+state['label'])
    threading.Thread(target=heartbeat, daemon=True).start()
    asr = load(manifest['asrModel'])
    aligner = None
    for number, task in enumerate(pending, 1):
        started = time.monotonic()
        title = Path(task['source']).name
        state['label'] = f'{number}/{len(pending)} '+title
        progress(f'台词精确对齐 {number}/{len(pending)}：{title} {task["speechStart"]:.2f}–{task["speechEnd"]:.2f} 秒')
        result = {'revision': REVISION, 'key': task['key'], 'status': 'failed',
                  'audioOnly': True, 'scriptWasModelInput': False,
                  'sourceStart': task['start'], 'sourceEnd': task['end']}
        try:
            with tempfile.TemporaryDirectory(prefix='cjh-align-', dir=Path(task['cachePath']).parent) as temp:
                audio = Path(temp)/'audio.wav'
                subprocess.run([manifest['ffmpeg'], '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                    '-ss', str(task['start']), '-i', task['source'], '-t', str(task['end']-task['start']),
                    '-vn', '-ac', '1', '-ar', '16000', '-c:a', 'pcm_s16le', str(audio)],
                    check=True, capture_output=True, text=True, timeout=120)
                with wave.open(str(audio), 'rb') as wav:
                    duration = wav.getnframes()/wav.getframerate()
                if duration <= 0 or abs(duration-(task['end']-task['start'])) > .08:
                    raise ValueError('实际音频长度与对齐窗口不符，未生成猜测时间')
                budget = min(2048, max(128, math.ceil(duration*18)+96))
                observed = asr.generate(str(audio), language='Chinese', max_tokens=budget,
                                        temperature=0.0, verbose=False, batch_size=1)
                result['qwenText'] = observed.text.strip()
                if getattr(observed, 'generation_tokens', 0) >= budget:
                    raise ValueError('新转写达到输出上限，不能当作完整句')
                text, basis = choose_text(task['currentText'], result['qwenText'], task['references'])
                if text is None:
                    result.update(status='disagreement', reason=basis)
                else:
                    if aligner is None:
                        progress('正在加载本地 Qwen3-ForcedAligner，定位实际说出的字词')
                        aligner = load(manifest['alignerModel'])
                        # The upstream helper interpolates non-monotonic classes.
                        # Editing evidence must expose those failures instead.
                        aligner.aligner_processor.parse_timestamp = parse_raw_timestamps
                    aligned = aligner.generate(audio=str(audio), text=text, language='Chinese')
                    words = [{'text': item.text,
                              'start': float(item.start_time)+task['start'],
                              'end': float(item.end_time)+task['start']} for item in aligned]
                    result['rawAlignmentWords'] = words
                    result['timestampResolutionMs'] = aligner.config.timestamp_segment_time
                    result['upstreamTimestampRepairDisabled'] = True
                    words = validate_words(words, text, task['start'], min(task['end'], task['start']+duration))
                    result.update(status='aligned', chosenText=text, basis=basis, words=words,
                                  reason='完整音频文字一致且所有词时间合法；仍需听审实际切点')
        except Exception as error:
            result.update(status='failed', reason=str(error)[-1600:])
        result['elapsedSeconds'] = round(time.monotonic()-started, 3)
        save(task['cachePath'], result)
        progress(f'台词复核结果：{result["status"]}；{number}/{len(pending)}')
        mx.clear_cache()
    waiting.set()


if __name__ == '__main__':
    run(json.loads(Path(sys.argv[1]).read_text(encoding='utf-8')))
