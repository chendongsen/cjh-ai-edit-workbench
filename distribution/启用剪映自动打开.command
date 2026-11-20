#!/bin/zsh
set -eu
cd "${0:A:h}"
echo "此功能会在粗剪完成后操作剪映首页，进入本次生成的草稿。"
echo "只在本机运行，不上传素材；不会强退正在编辑的项目。"
echo "辅助功能权限必须由你在系统设置中手动开启。"
read "answer?是否启用？输入 y 后回车（其他输入取消）："
if [[ "$answer" == "y" || "$answer" == "Y" ]]; then
  helper_path=$(./runtime/bin/python3 -I -B -c 'import sys,json; from pathlib import Path; sys.path.insert(0,str(Path.cwd()/"backend")); from common import DATA,HANDOFF_HELPER_APP; (DATA/"handoff.json").write_text(json.dumps({"enabled":True,"method":"macOS-accessibility","permissionGrantedByUserRequired":True})); print(HANDOFF_HELPER_APP)')
  /usr/bin/open -R "$helper_path"
  /usr/bin/open "x-apple.systempreferences:com.apple.preference.security?Privacy_Accessibility"
  echo "请添加 Finder 中选中的 CJH草稿助手.app 并开启开关。不要添加旧安装位置的助手。"
fi
read "answer?按回车关闭窗口。"
