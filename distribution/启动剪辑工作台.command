#!/bin/zsh
set -eu
cd "${0:A:h}"
export PYTHONNOUSERSITE=1
export PYTHONDONTWRITEBYTECODE=1
export HF_HUB_OFFLINE=1
export HF_HUB_DISABLE_TELEMETRY=1
export DO_NOT_TRACK=1
export CJH_OLLAMA_URL="${CJH_OLLAMA_URL:-http://127.0.0.1:11435}"
if [[ -z "${CJH_DATA_DIR:-}" ]]; then
  if [[ "$PWD" == *.app/Contents/Resources/workbench ]]; then
    export CJH_DATA_DIR="${PWD:h:h:h:h}/.local"
  else
    export CJH_DATA_DIR="$PWD/.local"
  fi
fi
export HF_HOME="$CJH_DATA_DIR/hf"
unset PYTHONPATH PYTHONHOME
if [[ "${CJH_BACKGROUND:-0}" == "1" ]]; then
  exec ./runtime/bin/python3 backend/launch.py
fi
./runtime/bin/python3 backend/launch.py || {
  echo "启动未完成，以上信息可提供给负责人；请勿发送访问码或私密路径。"
  read "answer?按回车关闭窗口。"
}
