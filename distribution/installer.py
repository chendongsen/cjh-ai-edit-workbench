"""Offline, per-user installation. Never edits existing installations or editors."""
import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

BASE = Path(__file__).resolve().parent
VERSION = '0.1.5-beta.20261105'


def desktop_shortcut(target, desktop):
    """Never overwrite existing files, apps, or broken links on the Desktop."""
    desktop.mkdir(parents=True, exist_ok=True)
    app = target/'CJH剪辑工作台.app'
    if not app.is_dir():
        raise ValueError('安装中缺少桌面启动 App')
    for name in ('CJH剪辑工作台.app', f'CJH剪辑工作台-{VERSION}.app'):
        link = desktop/name
        if link.is_symlink() and link.resolve() == app.resolve():
            return link
        try:
            link.symlink_to(app, target_is_directory=True)
            return link
        except FileExistsError:
            continue
    raise FileExistsError('桌面同名图标已存在，保留原图标；可从新安装目录打开 App')


def digest(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def verify(source):
    manifest = json.loads((BASE/'manifest.json').read_text())
    for number, (rel, info) in enumerate(manifest['files'].items()):
        path = source/rel
        if not path.resolve().is_relative_to(source.resolve()):
            raise ValueError('安装包文件缺失或包含异常链接：'+rel)
        if 'link' in info:
            if not path.is_symlink() or str(path.readlink()) != info['link']:
                raise ValueError('安装包链接校验失败：'+rel)
        elif not path.is_file() or path.is_symlink() or path.stat().st_size != info['size'] or digest(path) != info['sha256']:
            raise ValueError('安装包文件校验失败，请重新复制：'+rel)
        if number % 2000 == 0:
            print(f'校验运行环境和模型：{number}/{len(manifest["files"])}', flush=True)
    expected = set(manifest['files'])
    actual = {str(p.relative_to(source)) for p in source.rglob('*') if p.is_file() or p.is_symlink()}
    extra = {x for x in actual-expected if '__pycache__' not in Path(x).parts}
    if extra or expected-actual:
        raise ValueError('安装包出现清单以外文件，请使用原始压缩包。')
    return manifest


def install(target, launch=False, desktop=None):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64':
        raise ValueError('此安装包仅支持原生 Apple Silicon Mac，不支持 Intel 或 Rosetta。')
    if int(platform.mac_ver()[0].split('.')[0]) < 14:
        raise ValueError('本内测包要求 macOS 14 或更新版本。')
    target = target.expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise ValueError('目标已存在，不会覆盖旧安装或测试记录：'+str(target))
    source = BASE/'payload'
    manifest = verify(source)
    if manifest.get('version') != VERSION:
        raise ValueError('安装器与清单版本不一致')
    size = sum(x.get('size', 0) for x in manifest['files'].values())
    ancestor = target.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    if shutil.disk_usage(ancestor).free < size + 20*1024**3:
        raise ValueError('请至少保留安装体积加 20 GB 的可用空间，方便生成草稿。')
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, symlinks=True, ignore=shutil.ignore_patterns('__pycache__', '*.pyc', '.cjh-install-package'))
    (target/'.local').chmod(0o700)
    # A successful copy is not a usable installation. Verify native imports
    # before creating the desktop entry or reporting success.
    from install_diagnostics import run_checks
    diagnostic = target/'.local/安装自检报告.json'
    check = run_checks(target, diagnostic)
    if not check['ready']:
        raise ValueError('文件已安装，但运行环境自检未通过，尚不可运行。完整原因：'+str(diagnostic))
    link = None
    if desktop is not False:
        try:
            link = desktop_shortcut(target, Path(desktop).expanduser().absolute() if desktop is not None else Path.home()/'Desktop')
        except (OSError, ValueError) as error:
            print('安装完成，但桌面图标未创建：'+str(error), flush=True)
    (target/'安装来源.json').write_text(json.dumps({'version': VERSION, 'verifiedFiles': len(manifest['files']),
        'desktopShortcut': str(link) if link else None}, ensure_ascii=False, indent=2))
    print('\n安装完成：'+str(target), flush=True)
    print('桌面图标：'+(str(link) if link else '未创建；请打开安装目录内 CJH剪辑工作台.app'), flush=True)
    print('以后双击「CJH剪辑工作台」即可后台启动，只打开网页、不弹终端。任务结束后可从菜单栏 CJH 退出。', flush=True)
    print('首次请运行「启用剪映自动打开.command」，由本人确认并手动授权辅助功能。', flush=True)
    print('不需要管理员密码。不会安装登录启动项、不会更改达芬奇或剪映设置。', flush=True)
    if launch:
        subprocess.run(['/usr/bin/open', '-n', '-a', str(target/'CJH剪辑工作台.app')], check=True)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--package-root', type=Path, help='完整安装包目录（由包内 App 传入）')
    parser.add_argument('--target', type=Path, default=Path.home()/'Applications'/f'CJH剪辑工作台-{VERSION}')
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--desktop-dir', type=Path, help='桌面目录；默认当前用户 Desktop')
    parser.add_argument('--no-desktop', action='store_true')
    args = parser.parse_args()
    if args.package_root:
        BASE = args.package_root.expanduser().resolve()
    try:
        install(args.target, not args.no_open, False if args.no_desktop else args.desktop_dir)
    except Exception as e:
        print('安装未完成：'+str(e), file=sys.stderr)
        sys.exit(1)
