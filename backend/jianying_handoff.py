"""Opt-in, local macOS accessibility handoff. Permissions are never auto-granted."""
import json
import subprocess
from common import DATA, HANDOFF_HELPER_APP, read

APP = HANDOFF_HELPER_APP
HELPER = APP / 'Contents/MacOS/CJHOpenDraft'


def status():
    result = {'enabled': read(DATA/'handoff.json', {}).get('enabled') is True,
              'installed': HELPER.is_file(), 'trusted': False, 'appPath': str(APP)}
    if result['installed']:
        try:
            proc = subprocess.run([str(HELPER), '--check'], capture_output=True, text=True, check=True, timeout=5)
            result['trusted'] = json.loads(proc.stdout.strip().splitlines()[-1]).get('trusted') is True
        except Exception as error:
            result['error'] = str(error)
    return result


def open_draft(path):
    config = status()
    if not config['enabled']:
        return {'draftOpened': False, 'openState': 'application-launched',
                'note': '剪映已请求启动；本机自动打开尚未启用，请在首页选择本次草稿。'}
    if not config['installed'] or not config['trusted']:
        return {'draftOpened': False, 'openState': 'permission-required', 'needsUserAction': True,
                'helperApp': str(APP), 'note': '草稿已保存。请在 macOS 系统设置 → 隐私与安全性 → 辅助功能添加并开启「CJH草稿助手.app」，再点「打开本次草稿」。不会自动授权。'}
    try:
        proc = subprocess.run([str(HELPER), '--open', str(path)], capture_output=True, text=True, check=True, timeout=70)
        result = json.loads(proc.stdout.strip().splitlines()[-1])
        result['draftOpened'] = result.get('draftOpened') is True
        result['editorVerified'] = result['draftOpened'] and result.get('editorVerified') is True
        return result
    except Exception as error:
        return {'draftOpened': False, 'openState': 'handoff-error', 'needsUserAction': True,
                'note': '草稿已保存，但自动打开未得到确认；请检查剪映，不会重复点击。' + str(error)}
