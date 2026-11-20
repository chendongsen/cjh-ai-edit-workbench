"""Launch editors without force quitting, project switching or changing preferences."""
import json
import subprocess
import time
from pathlib import Path
from common import RESOLVE, ROOT
import jianying

RESOLVE_APP = Path(RESOLVE).parents[2]


def probe(timeout=6):
    try:
        result=subprocess.run([RESOLVE,str(ROOT/'backend/resolve_bridge.py'),'status'],
                              capture_output=True,text=True,timeout=timeout)
        if result.returncode:
            return {'connected':False,'error':result.stderr.strip()[-500:]}
        return json.loads(result.stdout.strip().splitlines()[-1])
    except Exception as error:
        return {'connected':False,'error':str(error)}


def ensure_resolve(report=print, timeout=90):
    current=probe()
    if current.get('connected'):
        report('达芬奇脚本接口已连接；无需重新启动应用')
        return current
    if not RESOLVE_APP.is_dir() or not Path(RESOLVE).is_file():
        raise RuntimeError('未找到达芬奇或 ResolvePython，请安装支持脚本接口的版本。')
    report('正在自动启动达芬奇；等待脚本接口就绪，不创建或切换项目')
    subprocess.run(['/usr/bin/open','-g','-a',str(RESOLVE_APP)],check=True,timeout=15)
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        current=probe(timeout=max(1,min(6,deadline-time.monotonic())))
        if current.get('connected'):
            report('达芬奇已启动，脚本接口连接成功')
            return current
        remaining=deadline-time.monotonic()
        if remaining<=0:break
        report(f'正在等待达芬奇脚本接口，最多还等 {int(remaining)} 秒；若有授权或首次启动提示，请在应用中处理')
        time.sleep(min(2,remaining))
    raise RuntimeError('已请求打开达芬奇，但脚本接口仍未连接。请处理启动/授权窗口，并检查偏好设置 → 系统 → 常规 → 外部脚本使用 Local；免费版或不支持接口的版本不能靠自动启动解决。修复后点「继续本次任务」。')


def prepare(report=print):
    status=jianying.status()
    if not status.get('available') or not status.get('app'):
        raise RuntimeError('未找到剪映应用或本机草稿转换器，请先完成安装。')
    if not status.get('draftRootExists'):
        report('正在启动剪映以检查首次初始化；不更改草稿目录')
        jianying.launch_app()
        for _ in range(10):
            if jianying.draft_root().is_dir():break
            time.sleep(1)
        else:
            raise RuntimeError('剪映已请求启动，但默认草稿目录尚未建立。首次使用请在剪映创建并保存一个空白草稿，再点继续；后续无需提前打开剪映。')
    return ensure_resolve(report)
