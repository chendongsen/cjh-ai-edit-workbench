"""Opt-in same-Mac data migration; never modifies the source or starts services.

Original media paths and history IDs stay unchanged. The old data root must stay
available. Only copied status.command values are rebound to the new installation.
"""
import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

DATA_ENTRIES = ('assets.json', 'jobs', 'imports', 'speaker-tests', 'food-reviews.json',
    'food-index', 'broll-cache', 'reaction-cache', 'voice-cache', 'speech-refine',
    'script-shot-cache', 'clip-cache', 'face-motion-cache', 'test-media', 'legacy-test-run', 'intake-qa')
CONFIG_ENTRIES = ('handoff.json', 'vision-combo.json')
COMMANDS = {'workflow': 'workflow.py', 'analyze': 'analyze.py', 'resolve': 'execute.py',
    'jianying': 'jianying.py', 'speaker': 'speaker.py'}
METADATA_SUFFIXES = {'.json', '.jsonl', '.txt', '.md', '.csv', '.srt', '.vtt', '.otio', '.html', '.xml', '.py'}
ACTIVE = {'queued', 'running'}


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_json(path, value):
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)


def alive(pid):
    if type(pid) is not int or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def inventory(data, entries):
    files, directories = {}, []
    for name in entries:
        entry = data / name
        if entry.is_symlink():
            raise ValueError('拒绝迁移符号链接：' + name)
        for path in [entry, *sorted(entry.rglob('*'))] if entry.is_dir() else [entry]:
            info = path.lstat()
            relative = str(path.relative_to(data))
            if stat.S_ISLNK(info.st_mode):
                raise ValueError('拒绝迁移符号链接：' + relative)
            if stat.S_ISDIR(info.st_mode):
                directories.append(relative)
            elif stat.S_ISREG(info.st_mode):
                files[relative] = {'size': info.st_size, 'mtime_ns': info.st_mtime_ns,
                    'sha256': sha(path) if path.suffix.lower() in METADATA_SUFFIXES else None}
            else:
                raise ValueError('拒绝迁移非常规文件：' + relative)
    return {'files': files, 'directories': sorted(directories)}


def id_digest(ids):
    return hashlib.sha256('\0'.join(sorted(ids)).encode()).hexdigest()


def read_jobs(data):
    jobs = {}
    for path in sorted((data / 'jobs').glob('*/status.json')):
        job = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(job, dict) or job.get('id') != path.parent.name:
            raise ValueError('任务 ID 与目录不一致：' + str(path.relative_to(data)))
        if job.get('kind') not in COMMANDS:
            raise ValueError('未知任务种类，未迁移任何数据：' + str(path.relative_to(data)))
        if alive(job.get('workerPid')):
            raise ValueError('源库仍有存活任务进程；请先安全结束：' + job['id'])
        jobs[job['id']] = job
    return jobs


def copy_entry(source, target):
    """Use APFS copy-on-write, never hard links; fallback owns only a fresh target."""
    if target.exists() or target.is_symlink():
        raise FileExistsError(target)
    try:
        subprocess.run(['/bin/cp', '-cRp', str(source), str(target)], check=True,
                       stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        return 'apfs-clone'
    except (subprocess.CalledProcessError, FileNotFoundError):
        # target was absent at entry and was created only by this copy attempt.
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            shutil.rmtree(target)
        if source.is_dir():
            shutil.copytree(source, target, copy_function=shutil.copy2, symlinks=True)
        else:
            shutil.copy2(source, target, follow_symlinks=False)
        return 'ordinary-copy'


def compare_copy(source, stage, entries, expected):
    actual = inventory(stage, entries)
    if set(actual['files']) != set(expected['files']) or actual['directories'] != expected['directories']:
        raise ValueError('迁移副本文件/目录集合不一致')
    for relative, old in expected['files'].items():
        new = actual['files'][relative]
        if new['size'] != old['size'] or new['sha256'] != old['sha256']:
            raise ValueError('迁移副本校验失败：' + relative)
        a, b = (source / relative).stat(), (stage / relative).stat()
        if (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino):
            raise ValueError('迁移副本必须有独立 inode，不能使用硬链接：' + relative)


def migrate(source, target, report_dir, execute=False):
    source, target, report_dir = (Path(p).expanduser().resolve() for p in (source, target, report_dir))
    if source == target or source.is_relative_to(target) or target.is_relative_to(source):
        raise ValueError('源目录与目标目录必须相互独立')
    if report_dir.is_relative_to(source) or report_dir.is_relative_to(target):
        raise ValueError('备份报告必须位于源安装和目标安装之外')
    old, new = source / '.local', target / '.local'
    if old.is_symlink() or new.is_symlink() or not old.is_dir() or not new.is_dir():
        raise ValueError('源数据目录或新安装数据目录不存在')
    entries = [name for name in (*DATA_ENTRIES, *CONFIG_ENTRIES) if (old / name).exists() or (old / name).is_symlink()]
    for name in DATA_ENTRIES:
        if name in entries and ((new / name).exists() or (new / name).is_symlink()):
            raise FileExistsError('新安装已有数据，不覆盖：' + name)
    jobs = read_jobs(old)
    runtime = target / 'runtime/bin/python3'
    if not runtime.is_file() or not os.access(runtime, os.X_OK):
        raise ValueError('新安装缺少可执行的独立 Python')
    for script in {COMMANDS[job['kind']] for job in jobs.values()}:
        if not (target / 'backend' / script).is_file():
            raise ValueError('新安装缺少任务入口：' + script)
    baseline = inventory(old, entries)
    assets = json.loads((old / 'assets.json').read_text()) if 'assets.json' in entries else {}
    if not isinstance(assets, dict) or any(not isinstance(a, dict) for a in assets.values()):
        raise ValueError('素材登记格式无效')
    missing = [key for key, value in assets.items() if not isinstance(value.get('path'), str)
               or not Path(value['path']).is_file()]
    defaults = {}
    for name in CONFIG_ENTRIES:
        if name in entries:
            if not isinstance(json.loads((old / name).read_text()), dict):
                raise ValueError('源配置格式无效：' + name)
            if (new / name).is_symlink() or ((new / name).exists() and not (new / name).is_file()):
                raise ValueError('目标配置不是普通文件：' + name)
            defaults[name] = sha(new / name) if (new / name).exists() else None
    invalid = []
    for relative, info in baseline['files'].items():
        if Path(relative).suffix == '.json':
            try:
                json.loads((old / relative).read_text())
            except (ValueError, UnicodeError):
                invalid.append({'file': relative, 'size': info['size'], 'preservedUnchanged': True})
    report = {'schema': 'cjh-local-data-upgrade-1', 'phase': 'planned', 'source': str(source), 'target': str(target),
        'entries': entries, 'files': len(baseline['files']), 'bytes': sum(x['size'] for x in baseline['files'].values()),
        'assetCount': len(assets), 'assetIdsSha256': id_digest(assets), 'missingAssetIds': missing,
        'jobCount': len(jobs), 'jobIdsSha256': id_digest(jobs), 'jobIds': sorted(jobs),
        'jobDirectories': len([p for p in (old / 'jobs').iterdir() if p.is_dir()]) if (old / 'jobs').exists() else 0,
        'existingInvalidJson': invalid, 'statusChanges': [], 'configurationDefaults': defaults,
        'note': '原素材绝对路径保持不变，旧源目录必须保留；未复制模型、App、系统权限或局域网账号。'}
    if not execute:
        return report
    if report_dir.exists():
        raise FileExistsError('报告目录已存在，避免覆盖备份：' + str(report_dir))
    report_dir.mkdir(parents=True, mode=0o700)
    write_json(report_dir / 'source-inventory.json', baseline)
    stage = Path(tempfile.mkdtemp(prefix='.upgrade-data-', dir=new))
    report['staging'] = str(stage)
    report['phase'] = 'copying'
    write_json(report_dir / 'migration.json', report)
    committed = []
    try:
        for name, digest in defaults.items():
            if digest is not None:
                (report_dir / 'new-defaults').mkdir(exist_ok=True)
                shutil.copy2(new / name, report_dir / 'new-defaults' / name)
                if sha(report_dir / 'new-defaults' / name) != digest:
                    raise ValueError('新配置在备份期间发生变化：' + name)
        report['copyMethods'] = {name: copy_entry(old / name, stage / name) for name in entries}
        compare_copy(old, stage, entries, baseline)
        if inventory(old, entries) != baseline:
            raise ValueError('源库在迁移期间发生变化，未安装副本')
        # Recheck liveness after copying, before enabling copied retry entries.
        read_jobs(old)
        for identifier, original in jobs.items():
            relative = Path('jobs') / identifier / 'status.json'
            backup = report_dir / 'original-status' / identifier / 'status.json'
            backup.parent.mkdir(parents=True)
            shutil.copy2(old / relative, backup)
            if (sha(backup) != baseline['files'][str(relative)]['sha256']
                    or json.loads(backup.read_text(encoding='utf-8')) != original):
                raise ValueError('源任务状态在读取或备份期间发生变化：' + identifier)
            migrated = dict(original)
            migrated['command'] = [str(runtime), str(target / 'backend' / COMMANDS[original['kind']])]
            if original.get('status') in ACTIVE:
                migrated['status'] = 'interrupted'
                migrated['message'] = '升级迁移：原排队或运行任务已暂停，未自动重跑；请核对后手动继续。'
                migrated.pop('workerPid', None)
            write_json(stage / relative, migrated)
            report['statusChanges'].append({'id': identifier, 'originalSha256': sha(backup),
                'commandBefore': original.get('command'), 'commandAfter': migrated['command'],
                'statusBefore': original.get('status'), 'statusAfter': migrated.get('status')})
        # All copy checks precede any changes to active data in the destination.
        for name in entries:
            exists = (new / name).exists() or (new / name).is_symlink()
            if name in defaults:
                if ((new / name).is_symlink() or exists != (defaults[name] is not None)
                        or (exists and (not (new / name).is_file() or sha(new / name) != defaults[name]))):
                    raise ValueError('目标配置在迁移期间发生变化：' + name)
            elif exists:
                raise FileExistsError('目标数据在迁移期间出现，不覆盖：' + name)
        report['phase'] = 'verified-before-commit'
        write_json(report_dir / 'migration.json', report)
        for name in entries:
            os.replace(stage / name, new / name)
            committed.append(name)
        stage.rmdir()
        report['phase'] = 'complete'
        report['sourceUnchanged'] = inventory(old, entries) == baseline
        if not report['sourceUnchanged']:
            raise ValueError('迁移完成后源库又有变化，请停止切换并核对')
        report['installedInventory'] = 'installed-inventory.json'
        write_json(report_dir / report['installedInventory'], inventory(new, entries))
        write_json(report_dir / 'migration.json', report)
        return report
    except Exception as error:
        # Roll back only the entries moved by this invocation. Keep staging and backups.
        if committed:
            stage.mkdir(exist_ok=True)
        for name in reversed(committed):
            os.replace(new / name, stage / name)
            if defaults.get(name) is not None:
                shutil.copy2(report_dir / 'new-defaults' / name, new / name)
        report['phase'] = 'failed-no-data-committed'
        report['error'] = str(error)
        write_json(report_dir / 'migration.json', report)
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--target', required=True, type=Path)
    parser.add_argument('--report-dir', required=True, type=Path)
    parser.add_argument('--execute', action='store_true')
    args = parser.parse_args()
    report = migrate(args.source, args.target, args.report_dir, args.execute)
    print(json.dumps({key: report[key] for key in ('phase', 'files', 'bytes', 'assetCount', 'jobCount',
        'assetIdsSha256', 'jobIdsSha256')}, ensure_ascii=False, indent=2))
    print('缺失素材数：' + str(len(report['missingAssetIds'])) + '；保留的既存无效 JSON：' + str(len(report['existingInvalidJson'])))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print('迁移未完成：' + str(error), file=sys.stderr)
        sys.exit(1)
