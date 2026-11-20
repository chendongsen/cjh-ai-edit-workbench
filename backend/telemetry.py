"""Local, opt-in-to-share beta measurements. No paths or dialogue in events."""
import contextlib
import functools
import json
import os
import threading
import time
from pathlib import Path

_lock = threading.Lock()
_stage = None
_start = None


def event(kind, name, seconds=None, count=None):
    target = os.environ.get('CJH_TIMING_FILE')
    if not target:
        return
    row = {'kind': kind, 'name': name, 'at': time.time()}
    if seconds is not None:
        row['seconds'] = round(max(0, seconds), 4)
    if count is not None:
        row['count'] = count
    with _lock, open(target, 'a', encoding='utf-8') as file:
        file.write(json.dumps(row) + '\n')


def phase(name):
    global _stage, _start
    now = time.perf_counter()
    if _stage is not None:
        event('phase', _stage, now - _start)
    _stage, _start = (None, None) if name == 'done' else (name, now)


@contextlib.contextmanager
def measure(name):
    start = time.perf_counter()
    try:
        yield
    finally:
        event('model_load', name, time.perf_counter() - start)


def measured(name):
    def decorate(fn):
        @functools.wraps(fn)
        def call(*args, **kwargs):
            with measure(name):
                return fn(*args, **kwargs)
        return call
    return decorate


def ollama(body):
    # Ollama reports load_duration in nanoseconds. This is already INCLUDED in
    # phase wall time; never add it a second time to total elapsed time.
    if isinstance(body.get('load_duration'), (float, int)):
        event('model_load', 'ollama', body['load_duration'] / 1e9)
    return body


def workflow(fn):
    @functools.wraps(fn)
    def call(source):
        old = os.environ.get('CJH_TIMING_FILE')
        os.environ['CJH_TIMING_FILE'] = str(Path(source).parent / 'timing.jsonl')
        event('attempt', 'start')
        try:
            return fn(source)
        finally:
            phase('done')
            event('attempt', 'end')
            if old is None:
                os.environ.pop('CJH_TIMING_FILE', None)
            else:
                os.environ['CJH_TIMING_FILE'] = old
    return call


def summary(path):
    phases, loads, counts = {}, {}, {}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            try:
                row = json.loads(line)
                bucket = {'phase': phases, 'model_load': loads, 'cache': counts}.get(row['kind'])
                if bucket is not None:
                    bucket[row['name']] = round(bucket.get(row['name'], 0) + row.get('seconds', row.get('count', 0)), 4)
            except (ValueError, KeyError):
                pass  # A killed worker may leave one incomplete last line.
    return {'phaseSeconds': phases, 'modelLoadSecondsIncluded': loads, 'cacheCounts': counts,
            'note': '阶段耗时包含模型加载；不可重复相加。累计所有尝试，缓存命中不代表重新推理。旧任务没有埋点时不推算耗时。'}
