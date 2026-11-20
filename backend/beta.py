"""Sanitized, local-only beta diagnostics and user-exported feedback."""
import math
import os
import platform
import plistlib
import shutil
import subprocess
import time
import uuid
from pathlib import Path
from common import ROOT, DATA, read, write
from telemetry import summary

VERSION = '0.1.5-beta.20261105'
ISSUES = ['编导未去掉', '误删达人', '表情笑声被截断', '闺蜜接话丢失', '食物匹配错误',
          '转写错误', '草稿打不开', '音画不同步', '运行慢或卡住', '其他']


def cmd(args):
    try:
        return subprocess.check_output(args, text=True, timeout=3, stderr=subprocess.DEVNULL).strip()
    except Exception:
        return 'unknown'


def version(app):
    try:
        return plistlib.loads((Path(app)/'Contents/Info.plist').read_bytes()).get('CFBundleShortVersionString', 'unknown')
    except Exception:
        return None


def machine():
    memory = cmd(['/usr/sbin/sysctl', '-n', 'hw.memsize'])
    return {'architecture': platform.machine(), 'macOS': platform.mac_ver()[0],
            'chip': cmd(['/usr/sbin/sysctl', '-n', 'machdep.cpu.brand_string']),
            'memoryGB': round(int(memory)/1024**3) if memory.isdigit() else None,
            'python': platform.python_version()}


def environment(caps):
    hardware = machine()
    free = round(shutil.disk_usage(ROOT).free / 1024**3, 1)
    checks = [
        ('Apple Silicon', hardware['architecture'] == 'arm64', '仅支持 M 系列，避免 Rosetta 下启动'),
        ('可用磁盘 ≥ 20 GB', free >= 20, f'剩余 {free} GB；草稿、抽帧和音频还会继续占用空间'),
        ('达芬奇脚本连接', caps['resolve']['connected'], '先保存并打开达芬奇，启用外部脚本 Local；免费版/旧版本可能不支持'),
        ('剪映已初始化', bool(caps['jianying']['app'] and caps['jianying']['draftRootExists']), '先打开剪映并手动新建一次草稿；默认草稿路径才受支持'),
        ('剪映转换器', caps['jianying']['available'], '仅迁移基础音视频轨道与切点，不保证跨版本兼容'),
        ('Whisper small', 'small' in caps['modelCache'], '安装包已含模型，不需积分或在线下载'),
        ('本地视觉模型', 'qwen3-vl:2b-instruct' in caps['localModels'], '本地视觉服务必须启动，不使用 cloud 模型'),
        ('声纹与笑声引擎', caps['voiceGuard']['speakerReady'] and caps['voiceGuard']['senseVoiceReady'], '没有该批次确认声纹时保守保留未知人声'),
    ]
    return {'version': VERSION, 'hardware': hardware, 'checks': [
        {'name': n, 'ok': bool(ok), 'help': hint} for n, ok, hint in checks],
        'ready': all(ok for _, ok, _ in checks),
        'recommendedMemoryGB': 16, 'freeGB': free,
        'warning': '测试版：仅检测连接，不代表识别准确或剪映草稿已通过审片。建议先跑 10～20 条。反馈不会自动发送。'}


def feedback(job, body):
    issues = body.get('issues', [])
    note = body.get('note', '')
    if not isinstance(issues, list) or any(x not in ISSUES for x in issues):
        raise ValueError('问题类别无效')
    if not isinstance(note, str) or len(note) > 2000:
        raise ValueError('问题描述最多 2000 字')
    metrics = {}
    for key in ('manualBaselineMinutes', 'reviewMinutes'):
        value = body.get(key)
        if value not in (None, ''):
            value = float(value)
            if not math.isfinite(value) or not 0 <= value <= 100000:
                raise ValueError('人工耗时必须为有效的非负分钟数')
            metrics[key] = value
    opened = body.get('draftOpened', 'untested')
    if opened not in ('yes', 'no', 'untested'):
        raise ValueError('草稿打开状态无效')
    record = {'savedAt': time.time(), 'issues': sorted(set(issues)), 'note': note,
              'draftOpened': opened, **metrics}
    write(DATA/'jobs'/job['id']/'beta-feedback.json', record)
    return record


def report(job):
    root = DATA/'jobs'/job['id']
    request = read(root/'input.json', {})
    analysis = read(root/'analysis/result.json', {}).get('items', [])
    result = job.get('result') or {}
    feedback_data = read(root/'beta-feedback.json', {})
    timing = summary(root/'timing.jsonl')
    history = job.get('attemptHistory', [])
    finished = job.get('finished') if job.get('status') not in ('queued', 'running') else None
    active = sum(h['seconds'] for h in history)
    # Do not infer durations for legacy jobs that have no measurement.
    timing.update(measured=bool(history or timing['phaseSeconds']),
        attempts=history,
        activeSeconds=round(active, 3) if history else None,
        elapsedSeconds=round(finished-job['created'], 3) if finished and history else None)
    jy = __import__('jianying').find_app()
    return {'schema': 'cjh-beta-feedback-1', 'version': VERSION, 'exportedAt': time.time(),
        'testId': job['id'], 'status': job['status'], 'hardware': machine(),
        'apps': {'resolve': version('/Applications/DaVinci Resolve/DaVinci Resolve.app'),
                 'jianying': jy['version'] if jy else None},
        'settings': {k: request.get(k) for k in ('model', 'visionModel', 'fps', 'vertical', 'unattended', 'targetSeconds', 'editMode')},
        'input': {'files': len(request.get('assets', [])),
                  'bytes': sum(a.get('size', 0) for a in request.get('assets', [])),
                  'analyzedSeconds': round(sum(x.get('duration', 0) for x in analysis), 2),
                  'hasScript': bool(request.get('editingScript') or request.get('script')),
                  'hasConfirmedVoiceProfile': bool(request.get('voiceProfile'))},
        'timing': timing,
        'output': {k: result.get(k) for k in ('duration', 'speechClips', 'foodClips', 'removedCommands',
                  'morningReviewCount', 'missingScriptCount', 'asrCorrections', 'asrDisagreements',
                  'boundaryReviewedClips', 'boundaryFrames', 'reactionExtendedClips')},
        'feedback': feedback_data,
        'privacy': '未包含源文件路径、文件名、台词、声纹、日志、项目名称或访问码。问题描述由测试者填写，发送前请检查；不会自动上传。',
        'limitations': '耗时不是准确率；人工基准和返工时间需自行填写。无参考声纹时未知声音保留。未测试的草稿打开状态不得视为成功。'}
