"""Install from one complete app, including when macOS translocates the source.

All executable installer code and its model seed are inside the approved app.
No security controls, download attributes or existing installations are changed.
"""
import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from install_diagnostics import run_checks
from installer import VERSION, desktop_shortcut


def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def verify_seed(app):
    seed = app/'Contents/Resources/install-seed'
    manifest = json.loads((seed/'seed-manifest.json').read_text())
    if manifest.get('version') != VERSION:
        raise ValueError('安装器与模型种子版本不一致')
    expected = set(manifest['files'])
    actual = {str(p.relative_to(seed)) for p in seed.rglob('*') if p.is_file() or p.is_symlink()} - {'seed-manifest.json'}
    if actual != expected:
        raise ValueError('安装组件清单不一致，请使用完整原始 App')
    for relative, entry in manifest['files'].items():
        path = seed/relative
        if path.is_symlink() or not path.resolve().is_relative_to(seed.resolve()):
            raise ValueError('模型种子包含外部链接')
        if path.stat().st_size != entry['size'] or digest(path) != entry['sha256']:
            raise ValueError('模型组件校验失败：'+relative)
    return seed


def install(source_app, target, desktop=None, launch=True):
    if platform.system() != 'Darwin' or platform.machine() != 'arm64' or int(platform.mac_ver()[0].split('.')[0]) < 14:
        raise ValueError('仅支持 macOS 14 或更新版本的原生 M 系列 Mac')
    app = Path(source_app).expanduser().resolve()
    code = app/'Contents/Resources/workbench'
    if Path(__file__).resolve().parent != code:
        raise ValueError('必须使用该 App 内的安装器，不能混用不同安装包')
    if not (code/'self-install.json').is_file():
        raise ValueError('不是完整自安装 App')
    target = Path(target).expanduser().absolute()
    if target.exists() or target.is_symlink():
        raise ValueError('目标目录已存在，保留原目录和记录；请使用旧版升级包或另选 --target：'+str(target))
    # This checks integrity, not Developer ID/notarization. Normal OS approval
    # still belongs to the user when opening the source app.
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    print('正在校验完整应用和离线模型…', flush=True)
    seed = verify_seed(app)
    size = sum(p.stat().st_size for p in app.rglob('*') if p.is_file() and not p.is_symlink())
    size += sum(p.stat().st_size for p in (seed/'.local').rglob('*') if p.is_file())
    parent = target.parent
    while not parent.exists():
        parent = parent.parent
    if shutil.disk_usage(parent).free < size + 20 * 1024**3:
        raise ValueError('空间不足：完整安装和缓存需约 '+str(round(size/1024**3,1))+' GB，再留至少20 GB工作空间')
    target.mkdir(parents=True)
    print('正在复制应用和初始化独立任务目录…', flush=True)
    installed_app = target/'CJH剪辑工作台.app'
    shutil.copytree(app, installed_app, symlinks=True)
    shutil.copytree(seed/'.local', target/'.local', symlinks=False)
    (target/'.local').chmod(0o700)
    for name in ('DEPENDENCIES.txt', 'THIRD_PARTY_NOTICES.txt', 'licenses'):
        source = seed/name
        if source.is_dir():
            shutil.copytree(source, target/name)
        else:
            shutil.copy2(source, target/name)
    wrapper = target/'启用剪映自动打开.command'
    wrapper.write_text('#!/bin/zsh\nset -eu\ncd "${0:A:h}"\nexec "./CJH剪辑工作台.app/Contents/Resources/workbench/启用剪映自动打开.command"\n')
    wrapper.chmod(0o755)
    print('正在逐项检查 Python、NumPy 和模型依赖…', flush=True)
    report_path = target/'.local/安装自检报告.json'
    report = run_checks(target, report_path)
    if not report['ready']:
        raise ValueError('组件复制完成，但依赖自检失败，尚不可运行。请提供完整报告：'+str(report_path))
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(installed_app)], check=True)
    link = None
    if desktop is not False:
        try:
            link = desktop_shortcut(target, Path(desktop).expanduser().absolute() if desktop else Path.home()/'Desktop')
        except (OSError, ValueError) as error:
            print('依赖就绪，但桌面图标未创建：'+str(error), flush=True)
    (target/'安装来源.json').write_text(json.dumps({'version': VERSION, 'layout': 'self-contained-app',
        'importChecksPassed': True, 'desktopShortcut': str(link) if link else None,
        'notarized': False}, ensure_ascii=False, indent=2))
    print('安装和依赖检查通过：'+str(target), flush=True)
    print('桌面图标：'+str(link or installed_app), flush=True)
    print('首次仍需本人配置达芬奇本地脚本接口，以及剪映草稿助手辅助功能权限。', flush=True)
    if launch:
        subprocess.run(['/usr/bin/open', '-n', '-a', str(installed_app)], check=True)
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source-app', type=Path, required=True)
    parser.add_argument('--target', type=Path, default=Path.home()/'Applications'/f'CJH剪辑工作台-{VERSION}')
    parser.add_argument('--desktop-dir', type=Path)
    parser.add_argument('--no-desktop', action='store_true')
    parser.add_argument('--no-open', action='store_true')
    args = parser.parse_args()
    try:
        install(args.source_app, args.target, False if args.no_desktop else args.desktop_dir, not args.no_open)
    except Exception as error:
        print('安装未完成：'+str(error), file=sys.stderr, flush=True)
        sys.exit(1)
