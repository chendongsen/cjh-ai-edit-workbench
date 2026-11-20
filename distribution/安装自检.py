"""Read-only offline package readiness; does not launch or edit the editors."""
import json
import platform
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
sys.path.insert(0, str(root/'backend'))
from common import DATA, INSTALL_ROOT, BUNDLE_APP, HANDOFF_HELPER_APP
import model_startup
import jianying_handoff

checks = {
    'nativeAppleSilicon': platform.system() == 'Darwin' and platform.machine() == 'arm64',
    'standalonePython': Path(sys.executable).resolve().is_relative_to(INSTALL_ROOT),
    'whisperSmallAndBase': {'small', 'base'} <= set(model_startup.speech_models()),
    'qwenInstalled': 'qwen3-vl:2b-instruct' in model_startup.installed_models(),
    'chineseClip': (DATA/'models/vision-combo/chinese-clip/pytorch_model.bin').is_file(),
    'faceLandmarker': (DATA/'models/vision-combo/face_landmarker.task').is_file(),
    'desktopLauncher': ((BUNDLE_APP or root/'CJH剪辑工作台.app')/'Contents/MacOS/CJHLauncher').is_file(),
    'handoffHelper': jianying_handoff.HELPER.is_file(),
}
for module in ('numpy', 'faster_whisper', 'torch', 'transformers', 'mediapipe', 'sherpa_onnx', 'pyJianYingDraft'):
    try:
        __import__(module)
        checks['import:'+module] = True
    except Exception as error:
        checks['import:'+module] = str(error)
print(json.dumps({'checks': checks, 'ready': all(v is True for v in checks.values()),
    'handoff': jianying_handoff.status(),
    'note': '自检仅验证安装和导入，不代表模型推理、达芬奇接口或剪映草稿实测通过。'}, ensure_ascii=False, indent=2))
sys.exit(0 if all(v is True for v in checks.values()) else 1)
