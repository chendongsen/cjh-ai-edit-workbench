"""Discover installed models and repair a stopped loopback service, without downloads."""
import fcntl
import json
import os
import socket
import subprocess
import time
import urllib.parse
import urllib.request
import urllib.error
from common import DATA, BUNDLE_APP, OLLAMA_EXECUTABLE, OLLAMA_URL, local_urlopen

DEFAULT = 'qwen3-vl:2b-instruct'


def speech_models():
    required = ('model.bin', 'config.json', 'tokenizer.json', 'vocabulary.txt')
    return [name for name in ('tiny', 'base', 'small')
            if any(all((snapshot/file).is_file() for file in required)
                   for snapshot in (DATA/'models'/('models--Systran--faster-whisper-'+name)/'snapshots').glob('*'))]


def live_models(errors=None):
    try:
        with local_urlopen(OLLAMA_URL + '/api/tags', timeout=2) as response:
            body = json.load(response)
            if not isinstance(body, dict) or not isinstance(body.get('models'), list):
                raise ValueError('模型接口没有返回有效模型列表')
            models = body['models']
            if any(not isinstance(m, dict) or not isinstance(m.get('name'), str) for m in models):
                raise ValueError('模型列表格式不正确')
            return [m['name'] for m in models
                    if 'cloud' not in m['name'].lower() and not m.get('remote_host')]
    except Exception as error:
        if errors is not None:
            if isinstance(error, urllib.error.HTTPError):
                errors.append(f'模型接口返回 HTTP {error.code}')
            elif isinstance(error, (TimeoutError, socket.timeout)) or isinstance(getattr(error, 'reason', None), TimeoutError):
                errors.append('模型接口响应超时')
            elif isinstance(error, (ValueError, TypeError, KeyError)):
                errors.append('模型接口返回内容格式不正确')
            else:
                errors.append('无法连接本机模型接口')
        return None


def installed_models():
    root = DATA / 'vision-models'
    manifest = root / 'manifests/registry.ollama.ai/library/qwen3-vl/2b-instruct'
    try:
        data = json.loads(manifest.read_text())
        for layer in [data['config'], *data['layers']]:
            blob = root / 'blobs' / layer['digest'].replace(':', '-')
            if not blob.is_file() or blob.stat().st_size != layer['size']:
                return []
        return [DEFAULT]
    except (OSError, KeyError, ValueError):
        return []


def choose(requested, available, preferred):
    if requested in available:
        return requested
    for name in preferred:
        if name in available:
            return name
    return available[0] if available else None


def ensure(requested=None, report=print, timeout=60):
    url = urllib.parse.urlparse(OLLAMA_URL)
    if url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost') or url.username:
        raise RuntimeError('自动启动只允许本机回环模型服务，不向外部发送素材。')
    port = url.port or 80
    with (DATA / 'model-startup.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        errors = []
        models = live_models(errors)
        if models is None:
            with socket.socket() as sock:
                listening = sock.connect_ex(('127.0.0.1', port)) == 0
            if listening:
                report('本地模型接口暂未正常响应，正在复查')
                models = live_models(errors)
                if models is None:
                    detail = errors[-1] if errors else '模型接口没有正常响应'
                    raise RuntimeError(f'本机模型连接失败：{detail}。{port} 端口有程序监听，但暂时无法读取模型列表；已绕过代理直连，未关闭任何进程。请稍后重试，仍失败时查看模型日志。')
        if models is None:
            executable = (OLLAMA_EXECUTABLE if BUNDLE_APP else
                          DATA / 'ollama-runtime/Ollama.app/Contents/Resources/ollama')
            if not installed_models() or not executable.is_file():
                raise RuntimeError('本机视觉模型文件或运行环境确实缺失；请修复完整安装包。没有自动下载或调用付费服务。')
            report('模型已安装，正在自动启动本地视觉服务，无需重新安装')
            env = dict(os.environ, OLLAMA_HOST=f'127.0.0.1:{port}', OLLAMA_NO_CLOUD='1',
                       OLLAMA_MODELS=str(DATA / 'vision-models'), OLLAMA_CONTEXT_LENGTH='8192',
                       OLLAMA_MAX_LOADED_MODELS='1')
            with (DATA / 'ollama.log').open('a') as log:
                process = subprocess.Popen([str(executable), 'serve'], env=env, stdout=log,
                                           stderr=log, start_new_session=True)
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                models = live_models()
                if models is not None:
                    break
                if process.poll() is not None:
                    raise RuntimeError('本地模型服务启动失败，请查看 .local/ollama.log。')
                time.sleep(.5)
            if models is None:
                raise RuntimeError('本地模型服务启动超时；没有把任务误报为完成。可稍后继续本次任务。')
        selected = choose(requested, models, [DEFAULT])
        if not selected:
            raise RuntimeError('本机模型服务已启动，但没有可用本地模型；请检查模型目录。')
        report('本地视觉服务就绪：' + selected)
        return selected
