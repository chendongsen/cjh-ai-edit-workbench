"""Build the local opt-in helper. Does not grant permissions or enable automation."""
import shutil
import subprocess
from common import ROOT, DATA, BUNDLE_APP

if BUNDLE_APP:
    raise RuntimeError('安装版助手已随 App 封装，请更新完整安装包，不在已签名 App 中重新构建。')

app=DATA/'CJH草稿助手.app'
executable=app/'Contents/MacOS/CJHOpenDraft'
executable.parent.mkdir(parents=True,exist_ok=True)
shutil.copy2(ROOT/'backend/CJHOpenDraft-Info.plist', app/'Contents/Info.plist')
subprocess.run(['/usr/bin/swiftc',str(ROOT/'backend/CJHOpenDraft.swift'),'-o',str(executable)],check=True)
subprocess.run(['/usr/bin/codesign','--force','--sign','-','--identifier','local.cjh.draft-handoff',str(app)],check=True)
print('助手已构建；请由用户在 macOS 辅助功能中授权：'+str(app))
