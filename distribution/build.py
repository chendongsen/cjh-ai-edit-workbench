"""Allowlist-only offline beta builder. Never copies a working .local wholesale."""
import argparse
import hashlib
import importlib.metadata
import json
import os
import plistlib
import shutil
import subprocess
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VERSION = '0.1.5-beta.20261105'
PYTHON_SHA = '9866e11cf2ae9caa1e671383786f248859a52dd8bfb702fb4c0dc2c65f1d36de'
PYTHON_URL = 'https://github.com/astral-sh/python-build-standalone/releases/download/20261013/cpython-3.12.15%2B20261013-aarch64-apple-darwin-install_only_stripped.tar.gz'
BACKEND = '''analyze.py common.py editorial.py execute.py food_index.py jianying.py
launch.py night_edit.py planner.py reaction_review.py resolve_bridge.py resolve_snapshot.py
resolve_transcribe.py server.py speaker.py speech_refine.py voice_guard.py workflow.py
telemetry.py beta.py intake.py editor_startup.py model_startup.py jianying_handoff.py broll.py vision_combo.py
CJHOpenDraft.swift CJHOpenDraft-Info.plist build_handoff.py edit_policy.py script_normalize.py script_shots.py script_coverage.py
project_naming.py resolve_lifecycle.py speech_sentences.py resolve_frames.py resolve_source.py
precise_alignment.py precise_alignment_worker.py video_retrieval.py video_retrieval_worker.py'''.split()
IGNORE = shutil.ignore_patterns('__pycache__', '*.pyc', '.git', '.DS_Store', '*.incomplete', '.locks')


def sha(path):
    with path.open('rb') as file:
        return hashlib.file_digest(file, 'sha256').hexdigest()


def copy(source, destination, links=False):
    if source.is_dir():
        shutil.copytree(source, destination, symlinks=links, ignore=IGNORE)
    else:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)


def refresh_code(destination):
    raise ValueError('本版包含新运行依赖、模型和原生 App，必须使用新目录完整构建，不能仅刷新源码')


def seal(destination):
    payload = destination/'payload'
    files = {}
    for path in sorted(payload.rglob('*')):
        rel = str(path.relative_to(payload))
        if path.is_symlink():
            if not path.resolve().is_relative_to(payload.resolve()):
                raise ValueError('外部绝对链接不能分发：'+rel)
            files[rel] = {'link': str(path.readlink())}
        elif path.is_file():
            files[rel] = {'size': path.stat().st_size, 'sha256': sha(path)}
    (destination/'manifest.json').write_text(json.dumps({'version': VERSION, 'pythonSource': PYTHON_URL, 'files': files}, ensure_ascii=False, indent=2))
    print(json.dumps({'package': str(destination), 'files': len(files), 'bytes': sum(x.get('size', 0) for x in files.values())}, ensure_ascii=False), flush=True)


def build(destination):
    if destination.exists():
        raise ValueError('输出目录已存在；请使用新的构建目录，不覆盖旧包')
    archive = ROOT/'.local/beta-build/downloads/python.tar.gz'
    if sha(archive) != PYTHON_SHA:
        raise ValueError('官方 Python 下载包校验失败')
    destination.mkdir(parents=True)
    payload = destination/'payload'
    payload.mkdir()
    temp = destination/'python-unpack'
    temp.mkdir()
    with tarfile.open(archive) as bundle:
        bundle.extractall(temp, filter='data')
    (temp/'python').rename(payload/'runtime')
    temp.rmdir()
    # Fresh official interpreter, with the application's validated wheel installs,
    # not the host's venv or Codex runtime. Preserve wheel metadata and licenses.
    target_site = payload/'runtime/lib/python3.12/site-packages'
    source_site = ROOT/'.venv/lib/python3.12/site-packages'
    for child in source_site.iterdir():
        if child.name in ('__pycache__',) or child.name.endswith('.pyc'):
            continue
        if child.name.startswith(('pip', 'setuptools', '_distutils', 'distutils-precedence')):
            continue
        copy(child, target_site/child.name, links=True)
    for name in BACKEND:
        copy(ROOT/'backend'/name, payload/'backend'/name)
    for child in (ROOT/'dist').iterdir():
        if child.suffix in ('.html', '.js', '.css') and not child.name.startswith('speaker.'):
            copy(child, payload/'dist'/child.name)
    index = payload/'dist/index.html'
    index.write_text(index.read_text().replace('<link rel="stylesheet" href="speaker.css"><script src="speaker.js"></script>', ''))
    for name in ('安装.command', 'installer.py', '先读我.txt', '给Codex的安装说明.txt', '版本更新说明.txt'):
        copy(ROOT/'distribution'/name, destination/name)
    for name in ('启动剪辑工作台.command', '先读我.txt', '版本更新说明.txt', '启用剪映自动打开.command', '安装自检.py'):
        copy(ROOT/'distribution'/name, payload/name)
    for script in (destination/'安装.command', payload/'启动剪辑工作台.command', payload/'启用剪映自动打开.command'):
        script.chmod(0o755)
    data = payload/'.local'
    (data/'models').mkdir(parents=True, mode=0o700)
    for name in ('3d-speaker', 'sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'):
        copy(ROOT/'.local/models'/name, data/'models'/name)
    for name in ('small', 'base'):
        cache_name = 'models--Systran--faster-whisper-'+name
        for part in ('refs', 'snapshots'):
            # Dereference only the selected model snapshots, never include the
            # download cache's unrelated blobs, credentials or incomplete files.
            copy(ROOT/'.local/models'/cache_name/part, data/'models'/cache_name/part)
    copy(ROOT/'.local/vendor/3D-Speaker', data/'vendor/3D-Speaker')
    copy(ROOT/'.local/ollama-runtime/Ollama.app', data/'ollama-runtime/Ollama.app', links=True)
    # Only the chosen Qwen manifest and its referenced content, never arbitrary
    # Ollama models added by the operator later.
    manifest_rel = Path('manifests/registry.ollama.ai/library/qwen3-vl/2b-instruct')
    manifest_source = ROOT/'.local/vision-models'/manifest_rel
    copy(manifest_source, data/'vision-models'/manifest_rel)
    manifest = json.loads(manifest_source.read_text())
    for part in [manifest['config'], *manifest['layers']]:
        digest = part['digest']
        if not digest.startswith('sha256:') or len(digest) != 71 or any(c not in '0123456789abcdef' for c in digest[7:]):
            raise ValueError('Ollama manifest digest 无效')
        name = digest.replace(':', '-')
        source = ROOT/'.local/vision-models/blobs'/name
        if source.stat().st_size != part['size'] or sha(source) != digest[7:]:
            raise ValueError('Ollama 模型校验失败：'+name)
        copy(source, data/'vision-models/blobs'/name)
    combo = data/'models/vision-combo'
    for name in ('config.json', 'preprocessor_config.json', 'vocab.txt', 'pytorch_model.bin', 'README.md'):
        copy(ROOT/'.local/models/vision-combo/chinese-clip'/name, combo/'chinese-clip'/name)
    copy(ROOT/'.local/models/vision-combo/face_landmarker.task', combo/'face_landmarker.task')
    if sha(combo/'chinese-clip/pytorch_model.bin') != '7b7b583c210c867410bc6bdb8a55fe14eec62999e0a9ea31ff222dc501f9cfbe':
        raise ValueError('Chinese-CLIP 权重校验失败')
    if sha(combo/'face_landmarker.task') != '64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff':
        raise ValueError('Face Landmarker 权重校验失败')
    copy(ROOT/'distribution/licenses', payload/'licenses')
    (data/'vision-combo.json').write_text(json.dumps({'enabled': True, 'mode': 'local-conservative-ensemble'}))
    # Explicit local opt-in is required on every colleague's Mac. Never copy
    # the developer's permission/preferences or any personal validation data.
    (data/'handoff.json').write_text(json.dumps({'enabled': False, 'permissionGrantedByUserRequired': True}))
    app = data/'CJH草稿助手.app'
    binary = app/'Contents/MacOS/CJHOpenDraft'
    binary.parent.mkdir(parents=True)
    copy(ROOT/'backend/CJHOpenDraft-Info.plist', app/'Contents/Info.plist')
    subprocess.run(['/usr/bin/swiftc', '-target', 'arm64-apple-macos14.0', str(ROOT/'backend/CJHOpenDraft.swift'), '-o', str(binary)], check=True)
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', '--identifier', 'local.cjh.draft-handoff', str(app)], check=True)
    launcher = payload/'CJH剪辑工作台.app'
    executable = launcher/'Contents/MacOS/CJHLauncher'
    executable.parent.mkdir(parents=True)
    resources = launcher/'Contents/Resources'
    resources.mkdir()
    (launcher/'Contents/Info.plist').write_bytes(plistlib.dumps({
        'CFBundleIdentifier': 'local.cjh.workbench.launcher', 'CFBundleName': 'CJH剪辑工作台',
        'CFBundleDisplayName': 'CJH剪辑工作台', 'CFBundleExecutable': 'CJHLauncher',
        'CFBundlePackageType': 'APPL', 'CFBundleVersion': '5', 'CFBundleShortVersionString': '0.1.4',
        'CFBundleIconFile': 'Workbench.icns', 'LSMinimumSystemVersion': '14.0', 'LSUIElement': True}))
    subprocess.run(['/usr/bin/swiftc', '-target', 'arm64-apple-macos14.0', str(ROOT/'distribution/DesktopLauncher.swift'), '-o', str(executable)], check=True)
    iconset = destination/'Workbench.iconset'
    subprocess.run(['/usr/bin/swift', str(ROOT/'distribution/make_icon.swift'), str(iconset)], check=True)
    subprocess.run(['/usr/bin/iconutil', '-c', 'icns', str(iconset), '-o', str(resources/'Workbench.icns')], check=True)
    shutil.rmtree(iconset)  # This exact, fresh build-only directory was created above.
    subprocess.run(['/usr/bin/codesign', '--force', '--sign', '-', str(launcher)], check=True)
    versions = sorted(f'{d.metadata["Name"]}=={d.version}' for d in importlib.metadata.distributions(path=[str(source_site)]))
    (destination/'DEPENDENCIES.txt').write_text('\n'.join(versions)+'\n')
    copy(destination/'DEPENDENCIES.txt', payload/'DEPENDENCIES.txt')
    notices = (
        'CJH internal beta third-party notices\n'
        'Python standalone: '+PYTHON_URL+'\nSHA256: '+PYTHON_SHA+'\n'
        'Python: https://docs.python.org/3/license.html (runtime/LICENSE*, licenses retained)\n'
        'Ollama: https://github.com/ollama/ollama (MIT; app resources retained)\n'
        'Qwen3-VL-2B-Instruct: https://huggingface.co/Qwen/Qwen3-VL-2B-Instruct (Apache-2.0; license blob retained)\n'
        'faster-whisper weights: https://huggingface.co/Systran/faster-whisper-small (MIT)\n'
        'https://huggingface.co/Systran/faster-whisper-base (MIT)\n'
        '3D-Speaker CAM++: https://github.com/modelscope/3D-Speaker (Apache-2.0; vendor/LICENSE retained)\n'
        'SenseVoice: https://github.com/k2-fsa/sherpa-onnx (model LICENSE retained)\n'
        'Chinese-CLIP ViT-B/16: https://github.com/OFA-Sys/Chinese-CLIP (MIT; licenses/Chinese-CLIP-LICENSE.txt)\n'
        'Weights: OFA-Sys/chinese-clip-vit-base-patch16, revision 36e679e65c2a2fead755ae21162091293ad37834\n'
        'MediaPipe Face Landmarker: Apache-2.0; licenses/MediaPipe-LICENSE.txt and model cards retained.\n'
        'All Python wheel distribution metadata/licenses remain in runtime/lib/python3.12/site-packages.\n'
        'FFmpeg redistribution notices are in imageio_ffmpeg; sources https://ffmpeg.org/ and https://github.com/imageio/imageio-ffmpeg.\n'
        'No personal media, confirmed speaker embeddings, access tokens or portal accounts are bundled.\n')
    (payload/'THIRD_PARTY_NOTICES.txt').write_text(notices)
    # Positive top-level allowlist protects the four independent users' first run.
    assert {p.name for p in data.iterdir()} == {'models', 'vendor', 'ollama-runtime', 'vision-models',
        'vision-combo.json', 'handoff.json', 'CJH草稿助手.app'}
    seal(destination)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('destination', type=Path)
    parser.add_argument('--zip', action='store_true')
    parser.add_argument('--refresh-code', action='store_true')
    args = parser.parse_args()
    if args.refresh_code:
        refresh_code(args.destination)
    else:
        build(args.destination)
    if args.zip:
        target = args.destination.parent/(args.destination.name+'.zip')
        if target.exists():
            raise ValueError('压缩包已存在，不覆盖')
        subprocess.run(['/usr/bin/ditto', '-c', '-k', '--norsrc', '--keepParent', str(args.destination), str(target)], check=True)
        target.with_suffix('.zip.sha256').write_text(sha(target)+'  '+target.name+'\n')
        print('ZIP: '+str(target), flush=True)
