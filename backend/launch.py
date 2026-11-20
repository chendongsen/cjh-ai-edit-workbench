"""Loopback-only local launcher. Starts/stops only its own child services."""
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
from common import ROOT, DATA, OLLAMA_EXECUTABLE, OLLAMA_URL, local_urlopen


def fetch(url):
    try:
        with local_urlopen(url, timeout=2) as response:
            return json.loads(response.read())
    except Exception:
        return None


def occupied(port):
    with socket.socket() as sock:
        return sock.connect_ex(('127.0.0.1', port)) == 0


def main():
    port = int(os.environ.get('CJH_PORT', '8765'))
    url = f'http://127.0.0.1:{port}/'
    identity = hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]
    existing = fetch(url+'api/health')
    if occupied(port):
        if existing and existing.get('instance') == identity:
            from model_startup import ensure
            ensure()
            subprocess.run(['/usr/bin/open', url+'#engines'], check=True)
            print('本安装位置的工作台已经启动，已打开网页。')
            return
        raise RuntimeError(f'本机 {port} 端口被另一个服务占用。请先保存任务并关闭原工作台；没有终止任何其他进程。')
    model = OLLAMA_EXECUTABLE
    owned = []
    try:
        model_port = int(OLLAMA_URL.rsplit(':', 1)[-1])
        tags = fetch(OLLAMA_URL+'/api/tags')
        if tags is None:
            if occupied(model_port):
                raise RuntimeError(f'本地视觉服务端口 {model_port} 有程序监听，但模型接口未正常响应，暂未启动新服务。')
            if not model.is_file():
                raise RuntimeError('缺少本地视觉运行环境，请重新解压完整安装包。')
            env = dict(os.environ, OLLAMA_HOST=f'127.0.0.1:{model_port}', OLLAMA_NO_CLOUD='1',
                       OLLAMA_MODELS=str(DATA/'vision-models'), OLLAMA_CONTEXT_LENGTH='8192',
                       OLLAMA_MAX_LOADED_MODELS='1')
            with (DATA/'ollama.log').open('a') as log:
                owned.append(subprocess.Popen([str(model), 'serve'], env=env, stdout=log, stderr=log))
            for _ in range(120):
                tags = fetch(OLLAMA_URL+'/api/tags')
                if tags:
                    break
                if owned[0].poll() is not None:
                    raise RuntimeError('视觉服务启动失败，请查看安装目录 .local/ollama.log。')
                time.sleep(.5)
        if not tags or 'qwen3-vl:2b-instruct' not in [m['name'] for m in tags.get('models', [])]:
            raise RuntimeError('本机视觉服务没有随包提供的 qwen3-vl:2b-instruct。没有改动已有 Ollama 服务。')
        with (DATA/'server.log').open('a') as log:
            server = subprocess.Popen([sys.executable, str(ROOT/'backend/server.py')], stdout=log, stderr=log)
        owned.append(server)
        for _ in range(80):
            health = fetch(url+'api/health')
            if health and health.get('instance') == identity:
                break
            if server.poll() is not None:
                raise RuntimeError('工作台启动失败，请查看安装目录 .local/server.log。')
            time.sleep(.25)
        else:
            raise RuntimeError('工作台启动超时；未打开不可用网页。')
        if os.environ.get('CJH_NO_BROWSER') != '1':
            subprocess.run(['/usr/bin/open', url+'#engines'], check=True)
        print('工作台已启动：'+url+'#engines', flush=True)
        if os.environ.get('CJH_BACKGROUND') == '1':
            print('已在后台运行。关闭网页不会停止粗剪；任务结束后可通过菜单栏 CJH 退出后台服务。请保持接电并开盖。', flush=True)
        else:
            print('保持此窗口打开、接电并开盖。关闭窗口会终止服务；请等粗剪结束后再关闭。', flush=True)
        while server.poll() is None:
            time.sleep(1)
        raise RuntimeError('工作台进程已退出，请检查 server.log。')
    finally:
        for process in reversed(owned):
            if process.poll() is None:
                process.terminate()
        for process in owned:
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print('本次启动的服务已停止；原始素材和草稿未删除。')
    except Exception as error:
        print('无法启动：'+str(error), file=sys.stderr)
        sys.exit(1)
