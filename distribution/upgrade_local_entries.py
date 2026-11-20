#!/usr/bin/env python3
"""One-off, reversible local 0.1.4 entry switch; dry-run unless --execute.

Stop the old launcher and its owned services BEFORE using this script. This
script deliberately does not inspect/stop processes, start apps, or edit bundles.
It preserves installations and data, moving only the two launcher entries and
an existing, recognized desktop symlink into a new rollback directory.
"""
import argparse
import json
import os
from pathlib import Path
import plistlib
import stat
import sys
from datetime import datetime, timezone


APP_NAME = 'CJH剪辑工作台.app'
COMMAND_NAME = '启动剪辑工作台.command'
RECEIPT_NAME = 'entry-switch-receipt.json'
BUNDLE_ID = 'local.cjh.workbench.launcher'


class EntrySwitchError(RuntimeError):
    pass


def _exists(path):
    return os.path.lexists(path)


def _identity(path):
    value = path.lstat()
    return [value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode)]


def _path(value):
    # Keep the final entry lexical: resolving an existing App symlink would hide
    # an already-switched or unfamiliar entry from the validation below.
    return Path(os.path.abspath(os.path.expanduser(os.fspath(value))))


def _inside(path, parent):
    return path == parent or parent in path.parents


def _validate_root(root):
    if root.is_symlink() or not root.is_dir() or root.resolve() != root:
        raise ValueError(f'安装根目录必须是实际目录，不可经符号链接访问：{root}')
    app, command = root / APP_NAME, root / COMMAND_NAME
    if app.is_symlink() or not app.is_dir():
        raise ValueError(f'App 不存在、已切换或不是原始目录：{app}')
    plist = app / 'Contents/Info.plist'
    if plist.is_symlink() or not plist.is_file():
        raise ValueError(f'App 缺少原始 Info.plist：{app}')
    with plist.open('rb') as stream:
        info = plistlib.load(stream)
    if (info.get('CFBundleIdentifier') != BUNDLE_ID
            or info.get('CFBundleExecutable') != 'CJHLauncher'):
        raise ValueError(f'拒绝切换未识别的 App：{app}')
    launcher = app / 'Contents/MacOS/CJHLauncher'
    if launcher.is_symlink() or not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise ValueError(f'App 启动器不可执行：{launcher}')
    if command.is_symlink() or not command.is_file() or not os.access(command, os.X_OK):
        raise ValueError(f'启动命令不存在、已切换或不可执行：{command}')
    # Validate shape, not a particular version's bytes. No executable is run.
    command_text = command.read_text(encoding='utf-8')
    if 'runtime/bin/python3' not in command_text or 'backend/launch.py' not in command_text:
        raise ValueError(f'拒绝切换未识别的启动命令：{command}')
    return app, command


def plan_entries(target, old_roots, desktop, backup_dir):
    target, desktop, backup = map(_path, (target, desktop, backup_dir))
    roots = [_path(root) for root in old_roots]
    if not roots or len(roots) != len(set(roots)) or target in roots:
        raise ValueError('至少需要一个不同于新版的旧安装根目录，且不可重复。')
    if len({root.name for root in roots}) != len(roots):
        raise ValueError('旧安装目录名称重复，无法建立无冲突的备份。')
    all_roots = [target, *roots]
    if any(_inside(a, b) for a in all_roots for b in all_roots if a != b):
        raise ValueError('新版和旧版安装根目录不可互相包含。')
    if _exists(backup) or not backup.parent.is_dir() or backup.parent.resolve() != backup.parent:
        raise ValueError(f'备份目录必须不存在，且父目录须为已存在的实际目录：{backup}')
    if any(_inside(backup, root) or _inside(root, backup) for root in all_roots):
        raise ValueError('备份目录须位于安装目录之外。')
    if (desktop.name != APP_NAME or not desktop.parent.is_dir()
            or desktop.parent.resolve() != desktop.parent
            or any(_inside(desktop, root) for root in [backup, *all_roots])):
        raise ValueError('桌面入口须使用标准 App 名称，且位于安装和备份目录之外。')
    target_app, target_command = _validate_root(target)
    runtime, backend = target / 'runtime/bin/python3', target / 'backend/launch.py'
    if not runtime.is_file() or not os.access(runtime, os.X_OK) or not backend.is_file():
        raise ValueError('新版安装缺少可执行的 Python runtime 或 backend/launch.py。')
    entries = []
    for root in roots:
        for source, destination in zip(_validate_root(root), (target_app, target_command)):
            entries.append({'source': str(source), 'target': str(destination),
                            'backup': str(backup / root.name / (source.name + '.backup')),
                            'identity': _identity(source)})
    desktop_exists = _exists(desktop)
    if desktop_exists:
        if not desktop.is_symlink() or desktop.resolve() not in {root / APP_NAME for root in roots}:
            raise ValueError(f'拒绝覆盖不是已知旧版快捷方式的桌面入口：{desktop}')
    entries.append({'source': str(desktop), 'target': str(target_app),
                    'backup': str(backup / 'desktop' / (APP_NAME + '.symlink.backup'))
                    if desktop_exists else None,
                    'identity': _identity(desktop) if desktop_exists else None})
    # Reserve the desktop backup directory name as well.
    if any(root.name == 'desktop' for root in roots):
        raise ValueError('旧版目录名称 desktop 与备份保留目录冲突。')
    return {'format': 1, 'status': 'dry-run', 'target': str(target),
            'backup_dir': str(backup), 'entries': entries, 'journal': [],
            'created_at': datetime.now(timezone.utc).isoformat()}


def _write_receipt(backup, receipt):
    temp = backup / (RECEIPT_NAME + '.tmp')
    # The directory is newly created by this run, not an existing user directory.
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(receipt, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, backup / RECEIPT_NAME)


def _rollback(journal):
    errors = []
    for action in reversed(journal):
        source = Path(action['source'])
        try:
            if action['action'] == 'symlink':
                if not _exists(source):
                    action['rolled_back'] = True
                    continue
                if (not source.is_symlink() or _identity(source) != action['identity']
                        or os.readlink(source) != action['target']):
                    raise EntrySwitchError(f'回滚时保留已被外部修改的入口：{source}')
                source.unlink()
            else:
                saved = Path(action['backup'])
                if _exists(source):
                    raise EntrySwitchError(f'回滚时拒绝覆盖新出现的文件：{source}')
                if not _exists(saved) or _identity(saved) != action['identity']:
                    raise EntrySwitchError(f'回滚备份缺失或已被外部修改：{saved}')
                os.rename(saved, source)
            action['rolled_back'] = True
        except Exception as error:
            errors.append(str(error))
    return errors


def switch_entries(target, old_roots, desktop, backup_dir, execute=False):
    receipt = plan_entries(target, old_roots, desktop, backup_dir)
    if not execute:
        return receipt
    backup = Path(receipt['backup_dir'])
    # mkdir without exist_ok atomically refuses a concurrent or repeated run.
    backup.mkdir(mode=0o700)
    receipt['status'] = 'in-progress'
    try:
        _write_receipt(backup, receipt)
        for entry in receipt['entries']:
            source, destination = Path(entry['source']), entry['target']
            if entry['backup']:
                saved = Path(entry['backup'])
                saved.parent.mkdir(mode=0o700, exist_ok=True)
                if not _exists(source) or _identity(source) != entry['identity']:
                    raise EntrySwitchError(f'原入口在检查后发生变化，已停止：{source}')
                if _exists(saved):
                    raise EntrySwitchError(f'拒绝覆盖备份：{saved}')
                os.rename(source, saved)
                receipt['journal'].append({'action': 'rename', 'source': str(source),
                                           'backup': str(saved), 'identity': entry['identity']})
                _write_receipt(backup, receipt)
            elif _exists(source):
                raise EntrySwitchError(f'桌面入口在检查后出现，拒绝覆盖：{source}')
            os.symlink(destination, source)
            receipt['journal'].append({'action': 'symlink', 'source': str(source),
                                       'target': destination, 'identity': _identity(source)})
            _write_receipt(backup, receipt)
        receipt['status'] = 'completed'
        _write_receipt(backup, receipt)
        return receipt
    except BaseException as error:
        receipt['error'] = str(error)
        receipt['rollback_errors'] = _rollback(receipt['journal'])
        receipt['status'] = 'rollback-incomplete' if receipt['rollback_errors'] else 'rolled-back'
        try:
            _write_receipt(backup, receipt)
        except Exception as receipt_error:
            raise EntrySwitchError(f'入口切换失败，回滚状态 {receipt["status"]}；'
                                   f'收据写入也失败：{receipt_error}；备份：{backup}') from error
        raise EntrySwitchError(f'入口切换失败，回滚状态 {receipt["status"]}；'
                               f'详见 {backup / RECEIPT_NAME}：{error}') from error


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--target', required=True, type=Path)
    parser.add_argument('--old-root', required=True, action='append', type=Path)
    parser.add_argument('--desktop', required=True, type=Path)
    parser.add_argument('--backup-dir', required=True, type=Path)
    parser.add_argument('--execute', action='store_true', help='实际切换；默认仅检查并显示计划')
    args = parser.parse_args(argv)
    try:
        receipt = switch_entries(args.target, args.old_root, args.desktop, args.backup_dir, args.execute)
    except (OSError, ValueError, EntrySwitchError) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(receipt, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
