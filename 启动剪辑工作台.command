#!/bin/zsh
cd "${0:A:h}"
if [[ ! -x .venv/bin/python ]]; then
  echo '本机执行环境未安装，请按 README 配置。'
  read
  exit 1
fi
exec .venv/bin/python backend/launch.py
