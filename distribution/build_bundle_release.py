"""Build a full application and a code-only upgrade from a verified offline seed.

This is an internal, ad-hoc-signed build, not a Developer ID/notarized release.
No live installation, quarantine attributes, or system security settings change.
"""
import argparse
import hashlib
import json
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import build

ROOT = build.ROOT
VERSION = build.VERSION
MAGIC = {b'\xcf\xfa\xed\xfe', b'\xce\xfa\xed\xfe', b'\xfe\xed\xfa\xcf', b'\xfe\xed\xfa\xce', b'\xca\xfe\xba\xbe', b'\xbe\xba\xfe\xca'}


def copy(source, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        shutil.copytree(source, target, symlinks=True, ignore=build.IGNORE)
    else:
        shutil.copy2(source, target)


def verify_seed(seed):
    manifest = json.loads((seed/'manifest.json').read_text())
    actual = {str(p.relative_to(seed/'payload')) for p in (seed/'payload').rglob('*')
              if (p.is_file() or p.is_symlink()) and '__pycache__' not in p.parts and p.suffix != '.pyc'}
    expected = {rel for rel in manifest['files'] if '__pycache__' not in Path(rel).parts and Path(rel).suffix != '.pyc'}
    if actual != expected:
        raise ValueError('seed manifest inventory mismatch: '+str(sorted(actual ^ expected)[:10]))
    for rel, entry in manifest['files'].items():
        if rel not in expected:
            continue  # Discarded interpreter caches are not copied into this build.
        file = seed/'payload'/rel
        if not file.resolve().is_relative_to((seed/'payload').resolve()):
            raise ValueError('seed contains external link: '+rel)
        if 'link' in entry:
            assert file.is_symlink() and str(file.readlink()) == entry['link'], rel
        else:
            assert file.is_file() and file.stat().st_size == entry['size'] and build.sha(file) == entry['sha256'], rel
    print('Verified original offline payload', flush=True)


def source_code(target):
    for name in build.BACKEND:
        copy(ROOT/'backend'/name, target/'backend'/name)
    for source in (ROOT/'dist').iterdir():
        if source.suffix in ('.html', '.js', '.css') and not source.name.startswith('speaker.'):
            copy(source, target/'dist'/source.name)
    index = target/'dist/index.html'
    index.write_text(index.read_text().replace('<link rel="stylesheet" href="speaker.css"><script src="speaker.js"></script>', ''))


def sign_app(app, report):
    """Seal derived build output from the inside out. Preserve valid signatures."""
    changes = []
    for file in app.rglob('*'):
        if file.is_symlink() or not file.is_file():
            continue
        with file.open('rb') as stream:
            native = stream.read(4) in MAGIC
        if not native:
            continue
        # Signing a bundle's main executable signs the whole bundle. Defer it
        # until its nested code/resources have been finalized.
        if file.parent.name == 'MacOS' and file.parent.parent.name == 'Contents':
            continue
        checked = subprocess.run(['/usr/bin/codesign', '--verify', '--strict', str(file)], capture_output=True, text=True)
        if checked.returncode:
            before = build.sha(file)
            subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', str(file)], check=True, capture_output=True)
            changes.append({'path': str(file.relative_to(app)), 'before': before, 'after': build.sha(file),
                            'previousVerification': checked.stderr.strip(), 'signature': 'ad-hoc-internal-build'})
    for nested in sorted(app.rglob('*.app'), key=lambda p: len(p.parts), reverse=True):
        checked = subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(nested)], capture_output=True, text=True)
        if checked.returncode:
            subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', str(nested)], check=True, capture_output=True)
            changes.append({'path': str(nested.relative_to(app)), 'signature': 'ad-hoc-internal-build',
                            'previousVerification': checked.stderr.strip()})
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--timestamp=none', str(app)], check=True, capture_output=True)
    subprocess.run(['/usr/bin/codesign', '--verify', '--deep', '--strict', str(app)], check=True)
    report.write_text(json.dumps({'notarized': False, 'developerID': False, 'derivedNativeSignatures': changes}, ensure_ascii=False, indent=2))


def build_bundle(seed, destination, ollama=None):
    if destination.exists():
        raise ValueError('New output directory required')
    verify_seed(seed)
    payload = destination/'payload'
    payload.mkdir(parents=True)
    app = payload/'CJH剪辑工作台.app'
    code = app/'Contents/Resources/workbench'
    helpers = app/'Contents/Helpers'
    seed_data = app/'Contents/Resources/install-seed'
    source_code(code)
    print('Copying offline runtime and selected models', flush=True)
    # A Python distribution also contains text scripts and package data; it is
    # not a Mach-O framework. Keep it as sealed resources inside the app.
    copy(seed/'payload/runtime', app/'Contents/Resources/runtime')
    (code/'runtime').symlink_to('../runtime', target_is_directory=True)
    copy(seed/'payload/.local/vendor/3D-Speaker', code/'vendor/3D-Speaker')
    for name in ('models', 'vision-models', 'vision-combo.json', 'handoff.json'):
        copy(seed/'payload/.local'/name, seed_data/'.local'/name)
    copy(ollama or seed/'payload/.local/ollama-runtime/Ollama.app', helpers/'Ollama.app')
    for name in ('installer.py', 'self_installer.py', 'install_diagnostics.py', '启动剪辑工作台.command', '启用剪映自动打开.command', '安装自检.py'):
        copy(ROOT/'distribution'/name, code/name)
    for script in code.glob('*.command'):
        script.chmod(0o755)
    handoff = helpers/'CJH草稿助手.app'
    module_cache = destination.parent/'构建缓存/SwiftModules'
    module_cache.mkdir(parents=True, exist_ok=True)
    swift = ['/usr/bin/swiftc', '-module-cache-path', str(module_cache), '-target', 'arm64-apple-macos14.0']
    binary = handoff/'Contents/MacOS/CJHOpenDraft'
    binary.parent.mkdir(parents=True)
    copy(ROOT/'backend/CJHOpenDraft-Info.plist', handoff/'Contents/Info.plist')
    subprocess.run([*swift, str(ROOT/'backend/CJHOpenDraft.swift'), '-o', str(binary)], check=True)
    launcher = app/'Contents/MacOS/CJHLauncher'
    launcher.parent.mkdir(parents=True)
    subprocess.run([*swift, str(ROOT/'distribution/DesktopLauncher.swift'), '-o', str(launcher)], check=True)
    copy(seed/'payload/CJH剪辑工作台.app/Contents/Resources/Workbench.icns', app/'Contents/Resources/Workbench.icns')
    (app/'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': 'local.cjh.workbench.launcher', 'CFBundleName': 'CJH剪辑工作台',
        'CFBundleDisplayName': 'CJH剪辑工作台', 'CFBundleExecutable': 'CJHLauncher',
        'CFBundlePackageType': 'APPL', 'CFBundleVersion': '6', 'CFBundleShortVersionString': '0.1.5',
        'CFBundleIconFile': 'Workbench.icns', 'LSMinimumSystemVersion': '14.0', 'LSUIElement': True}))
    for name in ('DEPENDENCIES.txt', 'THIRD_PARTY_NOTICES.txt', 'licenses'):
        copy(seed/'payload'/name, seed_data/name)
    seed_files = {str(p.relative_to(seed_data)): {'size': p.stat().st_size, 'sha256': build.sha(p)}
                  for p in seed_data.rglob('*') if p.is_file()}
    (seed_data/'seed-manifest.json').write_text(json.dumps({'version': VERSION, 'files': seed_files}, ensure_ascii=False, indent=2))
    (code/'self-install.json').write_text(json.dumps({'version': VERSION, 'layout': 'self-contained-app'}))
    (destination/'打开安装器.command').write_text('#!/bin/zsh\nset -eu\ncd "${0:A:h}"\nexec /usr/bin/open -n -a "$PWD/payload/CJH剪辑工作台.app"\n')
    (destination/'打开安装器.command').chmod(0o755)
    copy(ROOT/'distribution/install_diagnostics.py', destination/'install_diagnostics.py')
    print('Sealing the complete app; internal ad-hoc signatures, no Apple notarization', flush=True)
    sign_app(app, destination/'内部签名构建记录.json')
    build.seal(destination)
    (destination/'先读我.txt').write_text(f'''CJH 工作台 {VERSION} · M 系列完整安装包

解压完整文件夹，打开 payload 内的「CJH剪辑工作台.app」。它会安装到你自己的
~/Applications/CJH剪辑工作台-{VERSION}，自检通过后创建桌面图标并打开工作台。
首次可能需要你在 macOS「系统设置 → 隐私与安全性」确认来源后点「仍要打开」。
此包是内部测试版，没有 Apple Developer ID 或公证，不声称能自动越过系统限制。
如果系统提示损坏/恶意软件，或依赖自检失败，请保留日志给负责人，不关闭系统防护。

已有正常工作的 0.1.2/0.1.3/0.1.4 同事优先用小升级包，可保留任务及原有环境。
新安装不会覆盖已有目录和任务；同名桌面图标保留，另建带版本号的图标。
启动前请从旧工作台菜单栏 CJH 退出后台，避免两版争用端口。运行中的任务应先完成。
最低 macOS 14，原生 M 系列；安装需约包内数据体积再加 20GB 可用空间。
达芬奇和剪映单独安装；首次草稿助手辅助功能必须本人手动授权。

本版修复本机请求经过代理而返回502的问题，新增依赖导入自检，采用完整App布局。
模型及任务写在安装目录旁的 .local；不往已封装 App 里写任务或缓存。
本包不含个人素材、任务记录、账号、声纹参考或访问令牌。
电脑本机迁移检查不能替代同事另一台 Mac 的最终验收。
''')
    (destination/'给Codex的安装说明.txt').write_text(f'''请安装本目录的 CJH 工作台 {VERSION}，不要修改系统安全设置。
1. 核对外部 .zip.sha256；确认 M 系列、macOS14+、可用磁盘。
2. 已有能用的0.1.2/0.1.3/0.1.4优先选小升级包。不要终止在跑的任务。
3. 全新安装先打开 payload/CJH剪辑工作台.app，由用户按macOS正常提示允许。
   这是完整App，运行时已在包内；不要直接拷贝桌面图标或运行旧版安装命令。
   如需指定测试目录，获用户正常系统允许后，可调用包内
   payload/CJH剪辑工作台.app/Contents/Resources/runtime/bin/python3 -I -B
   payload/CJH剪辑工作台.app/Contents/Resources/workbench/self_installer.py
   --source-app payload/CJH剪辑工作台.app --target 目标目录 --no-open。
4. 查看 .local/安装自检报告.json；依赖失败不能说安装成功。原始stderr应保留。
5. 正常桌面启动后核对 /api/health 的版本与路径身份，模型GET/视觉POST需实际验证。
   只复制成功、codesign通过不等于公证或剪辑验收通过。
6. 本包没有DeveloperID公证。系统阻止时先看完整日志，不重复索要不存在的公证包，
   不删除quarantine、不修改Gatekeeper/TCC、不代替用户批准辅助功能。
7. 保留旧安装、素材、任务；不自动提交粗剪或切换用户正在编辑的项目。
''')
    return destination


def build_upgrade(destination):
    if destination.exists():
        raise ValueError('New output directory required')
    destination.mkdir(parents=True)
    code = destination/'code'
    source_code(code)
    for file in (code/'backend').iterdir():
        if file.suffix != '.py':
            file.unlink()
    files = {str(p.relative_to(code)): {'size': p.stat().st_size, 'sha256': build.sha(p)}
             for p in code.rglob('*') if p.is_file()}
    (destination/'upgrade-manifest.json').write_text(json.dumps({'version': VERSION, 'files': files}, ensure_ascii=False, indent=2))
    for name in ('compatible_upgrade.py', 'install_diagnostics.py'):
        copy(ROOT/'distribution'/name, destination/name)
    (destination/'升级工作台.command').write_text('''#!/bin/zsh
set -eu
cd "${0:A:h}"
for interpreter in "$HOME"/Applications/CJH剪辑工作台-*/runtime/bin/python3(N) /usr/bin/python3; do
  if [[ -x "$interpreter" ]] && "$interpreter" -I -B -c 'import sys' >/dev/null 2>&1; then
    "$interpreter" -I -B compatible_upgrade.py "$@" || { echo "升级未完成，请保留上面的错误或报告。"; read "answer?按回车关闭："; exit 1; }
    echo "升级完成，请使用原桌面图标打开工作台。"
    read "answer?按回车关闭："
    exit 0
  fi
done
echo "没有找到能启动的本机 Python。请使用完整安装包或把本目录交给 Codex 检查。"
exit 1
''')
    (destination/'升级工作台.command').chmod(0o755)
    (destination/'先读我.txt').write_text(f'''CJH 工作台 {VERSION} · 保留环境的小升级包

适用于已安装且依赖可正常加载的 0.1.2 / 0.1.3 / 0.1.4。
先等任务完成，在菜单栏 CJH 退出后台；解压后双击「升级工作台.command」。
也可将这个文件夹交给 Codex，要求运行 compatible_upgrade.py；多个安装可用 --target 指定。
升级只替换经清单校验的 backend/dist 代码，不更换 Python、NumPy、模型或原生桌面 App。
素材、脚本、任务、剪映草稿保持原位置。原代码自动备份，更新/自检失败自动回退。
升级后仍用原桌面图标，页面版本显示0.1.5；原安装文件夹名不变，这是保留旧环境的正常结果。
若目标0.1.4已被系统拦住而旧0.1.3正常，请让Codex检查报告并用 --target 明确指定正常旧安装；
不要盲目重装或覆盖旧目录。本包不适用于全新电脑或0.1.0/0.1.1。
本版包含本机模型直连代理修复；运行中的工作台会拒绝升级，避免中断任务和端口冲突。
若依赖自检失败，会留下完整JSON报错，不能当作升级成功。
''')
    return destination


def archive(folder):
    target = folder.parent/(folder.name+'.zip')
    if target.exists():
        raise ValueError('Archive already exists')
    subprocess.run(['/usr/bin/ditto', '-c', '-k', '--norsrc', '--keepParent', str(folder), str(target)], check=True)
    target.with_suffix('.zip.sha256').write_text(build.sha(target)+'  '+target.name+'\n')
    print('ZIP '+str(target), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    parser.add_argument('--seed', type=Path)
    parser.add_argument('--ollama', type=Path)
    parser.add_argument('--upgrade-only', action='store_true')
    parser.add_argument('--zip', action='store_true')
    args = parser.parse_args()
    if args.upgrade_only:
        result = build_upgrade(args.destination)
    else:
        if not args.seed:
            parser.error('--seed required for full bundle')
        result = build_bundle(args.seed, args.destination, args.ollama)
    if args.zip:
        archive(result)
