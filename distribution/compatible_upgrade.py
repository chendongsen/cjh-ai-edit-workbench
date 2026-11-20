"""Verified code-only repair for an existing, working per-user CJH installation.

Never copies or alters native runtime, models, task data, permissions, or launchers.
The colleague must quit the idle CJH background service before applying the patch.
"""
import argparse
import ast
import datetime
import fcntl
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import uuid
from contextlib import contextmanager
from pathlib import Path, PurePosixPath

VERSION = '0.1.5-beta.20261105'
SUPPORTED = {'0.1.2-beta.20261021', '0.1.3-beta.20261021', '0.1.4-beta.20261023'}
BASE = Path(__file__).resolve().parent
IMPORTS = ('numpy', 'torch', 'torchaudio', 'av', 'faster_whisper', 'transformers',
           'mediapipe', 'sherpa_onnx', 'pyJianYingDraft', 'pypinyin', 'PIL', 'imageio_ffmpeg')


def digest(path):
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def read_json(path):
    return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def version_from_file(path):
    tree = ast.parse(path.read_text(encoding='utf-8'))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'VERSION' for t in node.targets):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    raise ValueError('无法读取已安装版本：' + str(path))


def verify_package(base=BASE):
    base = Path(base)
    code = base / 'code'
    if code.is_symlink() or not code.is_dir():
        raise ValueError('缺少补丁 code 目录，或目录是符号链接。')
    manifest = read_json(base / 'upgrade-manifest.json')
    if manifest.get('version') != VERSION or not isinstance(manifest.get('files'), dict):
        raise ValueError('升级脚本与补丁清单版本不一致。')
    files = manifest['files']
    if 'backend/beta.py' not in files:
        raise ValueError('补丁缺少版本文件。')
    for name, entry in files.items():
        relative = PurePosixPath(name)
        if (not isinstance(name, str) or '\\' in name or len(relative.parts) != 2
                or relative.parts[0] not in ('backend', 'dist')
                or '..' in relative.parts or relative.is_absolute()
                or (relative.parts[0] == 'backend' and relative.suffix != '.py')
                or (relative.parts[0] == 'dist' and relative.suffix not in ('.html', '.css', '.js'))):
            raise ValueError('补丁包含不允许更新的路径：' + str(name))
        path = code / name
        if path.is_symlink() or path.parent.is_symlink() or not path.is_file():
            raise ValueError('补丁文件缺失或包含符号链接：' + name)
        if not isinstance(entry, dict) or path.stat().st_size != entry.get('size') or digest(path) != entry.get('sha256'):
            raise ValueError('补丁校验失败，请重新获取：' + name)
        if relative.suffix == '.py':
            ast.parse(path.read_text(encoding='utf-8'), filename=name)
    actual = set()
    for path in code.rglob('*'):
        if path.is_symlink():
            raise ValueError('补丁目录含有符号链接。')
        if path.is_file():
            actual.add(path.relative_to(code).as_posix())
    if actual != set(files):
        raise ValueError('补丁目录文件与校验清单不一致。')
    if version_from_file(code / 'backend/beta.py') != VERSION:
        raise ValueError('补丁内部版本不一致。')
    return manifest


def native_platform():
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('仅支持原生 M 系列 Mac；请勿通过 Rosetta 执行。')
    if int(platform.mac_ver()[0].split('.')[0]) < 14:
        raise ValueError('需要 macOS 14 或更新版本。')


def validate_target(target):
    raw = Path(target).expanduser().absolute()
    if raw.is_symlink():
        raise ValueError('请指定真实安装目录，不要指定桌面快捷方式或符号链接。')
    root = raw.resolve()
    try:
        root.relative_to(Path.home().resolve())
    except ValueError:
        raise ValueError('仅可更新当前用户主目录内的安装。')
    if not root.is_dir() or root.stat().st_uid != os.getuid():
        raise ValueError('安装目录不存在或不属于当前用户。')
    for name in ('backend', 'dist', 'runtime', '.local', 'CJH剪辑工作台.app'):
        item = root / name
        if item.is_symlink() or not item.is_dir():
            raise ValueError('不是支持的完整旧版安装结构：' + name)
    runtime = root / 'runtime/bin/python3'
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        raise ValueError('旧安装缺少可执行的 Python 运行环境。')
    try:
        runtime.resolve().relative_to(root / 'runtime')
    except ValueError:
        raise ValueError('运行环境链接到了安装目录外，不能自动更新。')
    version_file = root / 'backend/beta.py'
    if version_file.is_symlink():
        raise ValueError('已安装版本文件不能是符号链接。')
    version = version_from_file(version_file)
    if version not in SUPPORTED | {VERSION}:
        raise ValueError('此补丁只支持已有 0.1.2／0.1.3／0.1.4 完整安装；当前为 ' + version)
    return root, version


def target_listener_pids(root):
    command = ['/usr/sbin/lsof', '-nP', '-a', '-u', str(os.getuid()), '-iTCP', '-sTCP:LISTEN', '-Fp']
    result = subprocess.run(command, capture_output=True, text=True, timeout=10)
    if result.returncode not in (0, 1) or (result.returncode == 1 and result.stderr.strip()):
        raise ValueError('无法检查后台服务占用，未修改安装：' + result.stderr.strip())
    pids = {int(line[1:]) for line in result.stdout.splitlines() if line.startswith('p') and line[1:].isdigit()}
    found = []
    for pid in pids:
        process = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'command='], capture_output=True, text=True, timeout=5)
        command_line = process.stdout.strip()
        if str(root) in command_line and ('backend/server.py' in command_line or 'backend/launch.py' in command_line):
            found.append(pid)
    return sorted(found)


def ensure_idle(root):
    for path in (root / '.local/jobs').glob('*/status.json'):
        job = read_json(path)
        if job.get('status') in ('running', 'queued'):
            raise ValueError('有正在运行或排队的粗剪任务，请完成后再升级；没有中断任务。')
    pids = target_listener_pids(root)
    if pids:
        raise ValueError('此工作台后台仍在运行。请保存工作，在菜单栏 CJH 选择“退出后台服务”，再重新执行补丁；不会替你终止进程。')


def probe_runtime(root):
    """Exercise the existing interpreter; no new application code is executed."""
    program = '''import importlib,json,traceback
checks={}
for name in %r:
 try:
  importlib.import_module(name)
  checks[name]={'ok':True}
 except Exception:
  checks[name]={'ok':False,'traceback':traceback.format_exc()}
try:
 import numpy as np
 checks['numpy-compute']={'ok':float(np.dot([1.,2.],[3.,4.]))==11.0}
except Exception:
 checks['numpy-compute']={'ok':False,'traceback':traceback.format_exc()}
print(json.dumps({'ready':all(v['ok'] for v in checks.values()),'checks':checks}))
''' % (IMPORTS,)
    try:
        run = subprocess.run([str(root / 'runtime/bin/python3'), '-I', '-B', '-c', program],
                             capture_output=True, text=True, timeout=180, cwd=str(root))
    except subprocess.TimeoutExpired as error:
        return {'ready': False, 'error': '旧运行环境自检超过 180 秒',
                'stdout': str(error.stdout or ''), 'stderr': str(error.stderr or '')}
    rows = run.stdout.strip().splitlines()
    try:
        report = json.loads(rows[-1])
    except (ValueError, IndexError):
        report = {'ready': False, 'error': '旧运行环境未能完成自检'}
    report.update(returncode=run.returncode, stderr=run.stderr)
    report['ready'] = report.get('ready') is True and run.returncode == 0
    return report


def choose_target(explicit=None, reporter=print):
    if explicit is not None:
        root, version = validate_target(explicit)
        ensure_idle(root)
        return root, version
    candidates = []
    for path in sorted((Path.home() / 'Applications').glob('CJH剪辑工作台-*')):
        try:
            root, version = validate_target(path)
            candidates.append((root, version))
        except (ValueError, OSError, SyntaxError):
            continue
    if not candidates:
        raise ValueError('未找到支持的旧安装。此包是代码修复补丁，不是新电脑完整安装包；可用 --target 指定已有安装目录。')
    candidates.sort(key=lambda item: item[1], reverse=True)
    newest = candidates[0][1]
    peers = [(root, version) for root, version in candidates if version == newest]
    if len(peers) != 1:
        raise ValueError('找到多个同版本安装，无法确定桌面正在使用哪一个，请使用 --target：\n' + '\n'.join(str(root) for root, _ in peers))
    root, version = peers[0]
    if len(candidates) > 1:
        reporter('检测到多个版本，将检查最新兼容安装：' + str(root))
    # Do not silently patch a different, older copy if the intended latest copy fails.
    ensure_idle(root)
    return root, version


def atomic_copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temporary = tempfile.mkstemp(prefix='.cjh-upgrade-', dir=str(target.parent))
    os.close(handle)
    temp = Path(temporary)
    try:
        shutil.copy2(source, temp)
        os.replace(temp, target)
    finally:
        if temp.exists():
            temp.unlink()


@contextmanager
def upgrade_lock(root):
    path = root / '.code-upgrade.lock'
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as handle:
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('已有另一个升级程序正在处理此安装，请等待其完成。')
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def upgrade(target=None, base=BASE, reporter=print):
    native_platform()
    base = Path(base)
    manifest = verify_package(base)
    root, _ = choose_target(target, reporter)
    with upgrade_lock(root):
        return apply_upgrade(root, base, manifest, reporter)


def apply_upgrade(root, base, manifest, reporter):
    root, previous = validate_target(root)
    code = base / 'code'
    files = manifest['files']
    for name in files:
        destination = root / name
        if destination.is_symlink() or destination.parent.is_symlink():
            raise ValueError('已安装代码包含符号链接，未修改：' + name)
    matches = all((root / name).is_file() and digest(root / name) == entry['sha256'] for name, entry in files.items())
    if previous == VERSION:
        if not matches:
            raise ValueError('已标记为当前版本，但代码与补丁不同；请先检查，不能覆盖未知修改。')
        return {'status': 'already-installed', 'version': VERSION, 'target': str(root), 'changedFiles': 0}
    reporter('正在确认旧安装的 Python、NumPy 和模型依赖可用…')
    preflight = probe_runtime(root)
    if not preflight['ready']:
        report = base / ('升级前自检失败-' + datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:6] + '.json')
        write_json(report, {'target': str(root), 'stage': 'before-upgrade', 'runtime': preflight, 'changedFiles': 0})
        raise ValueError('旧运行环境自检失败，代码未更新。此补丁不替换被系统拦截的原生组件。完整报错已保存：' + str(report))
    ensure_idle(root)
    verify_package(base)
    stamp = datetime.datetime.now().strftime('%Y%m%d-%H%M%S') + '-' + uuid.uuid4().hex[:8]
    backup = root / '.upgrade-backups' / stamp
    if backup.parent.is_symlink():
        raise ValueError('备份目录不能是符号链接。')
    backup.mkdir(parents=True, exist_ok=False)
    originals = {}
    for name in files:
        path = root / name
        if path.exists():
            if not path.is_file():
                raise ValueError('目标代码路径不是常规文件：' + name)
            saved = backup / 'files' / name
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, saved)
            originals[name] = {'exists': True, 'sha256': digest(path)}
        else:
            originals[name] = {'exists': False}
    report = {'status': 'applying', 'target': str(root), 'previousVersion': previous,
              'version': VERSION, 'backup': str(backup), 'originalFiles': originals,
              'runtimeBefore': preflight, 'runtimePreserved': True, 'modelsPreserved': True,
              'taskDataPreserved': True, 'desktopAndNativeAppsPreserved': True}
    write_json(backup / 'upgrade-report.json', report)
    touched = []
    try:
        ensure_idle(root)
        for name, entry in files.items():
            path = root / name
            if originals[name]['exists'] and digest(path) != originals[name]['sha256']:
                raise ValueError('升级期间代码被其他程序改动：' + name)
            touched.append(name)
            atomic_copy(code / name, path)
            if digest(path) != entry['sha256']:
                raise ValueError('写入后校验失败：' + name)
        postflight = probe_runtime(root)
        report['runtimeAfter'] = postflight
        if not postflight['ready']:
            raise ValueError('升级后运行环境自检失败')
        if version_from_file(root / 'backend/beta.py') != VERSION:
            raise ValueError('升级后版本验证失败')
        report.update(status='upgraded', changedFiles=len(touched))
        write_json(backup / 'upgrade-report.json', report)
    except BaseException as error:
        rollback_errors = []
        for name in reversed(touched):
            try:
                if originals[name]['exists']:
                    atomic_copy(backup / 'files' / name, root / name)
                elif (root / name).exists():
                    (root / name).unlink()
            except Exception as rollback_error:
                rollback_errors.append(name + ': ' + str(rollback_error))
        report.update(status='rollback-incomplete' if rollback_errors else 'rolled-back',
                      error=str(error), rollbackErrors=rollback_errors)
        write_json(backup / 'upgrade-report.json', report)
        raise RuntimeError(('升级失败，部分文件需按备份恢复：' if rollback_errors else '升级失败，已恢复原代码：') + str(error) + '\n报告：' + str(backup / 'upgrade-report.json')) from error
    reporter('代码修复完成。旧运行环境、素材、任务和桌面图标保留。请用原桌面图标重新打开工作台。')
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', type=Path, help='已有工作台的真实安装目录；默认检查当前用户 Applications')
    args = parser.parse_args()
    try:
        result = upgrade(args.target)
        print(json.dumps({k: result.get(k) for k in ('status', 'version', 'target', 'backup', 'changedFiles')}, ensure_ascii=False, indent=2))
    except Exception as error:
        print('未完成更新：' + str(error), file=sys.stderr)
        sys.exit(1)
