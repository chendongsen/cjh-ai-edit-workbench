#!/bin/zsh
set -eu
cd "${0:A:h}"
echo "CJH 本地内测安装器 · 仅 M 系列 Mac"
./payload/runtime/bin/python3 -I -B installer.py "$@"
echo
read "answer?按回车关闭窗口。"
