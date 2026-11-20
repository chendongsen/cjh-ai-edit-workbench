"""Local-only task storage. Never included in the hosted static artifact."""
import json
import ipaddress
import os
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# A complete app owns its executable code; mutable jobs/models stay beside it.
# The flat layout remains supported for existing installs and development.
BUNDLE_APP = (ROOT.parents[2] if ROOT.name == 'workbench'
              and ROOT.parent.name == 'Resources'
              and ROOT.parents[1].name == 'Contents'
              and ROOT.parents[2].suffix == '.app' else None)
INSTALL_ROOT = BUNDLE_APP.parent if BUNDLE_APP else ROOT
DATA = Path(os.environ.get('CJH_DATA_DIR') or str(INSTALL_ROOT / '.local')).expanduser().resolve()
if BUNDLE_APP and DATA.is_relative_to(BUNDLE_APP):
    raise ValueError('工作台数据目录不能位于 App 内，请使用安装目录旁的 .local。')
DATA.mkdir(parents=True, exist_ok=True, mode=0o700)
OLLAMA_EXECUTABLE = ((BUNDLE_APP / 'Contents/Helpers/Ollama.app/Contents/Resources/ollama')
                     if BUNDLE_APP else DATA / 'ollama-runtime/Ollama.app/Contents/Resources/ollama')
HANDOFF_HELPER_APP = ((BUNDLE_APP / 'Contents/Helpers/CJH草稿助手.app')
                      if BUNDLE_APP else DATA / 'CJH草稿助手.app')
VENDOR_DIR = ROOT / 'vendor/3D-Speaker' if BUNDLE_APP else DATA / 'vendor/3D-Speaker'
ASSETS = DATA / 'assets.json'
RESOLVE = '/Applications/DaVinci Resolve/DaVinci Resolve.app/Contents/Applications/ResolvePython'
OLLAMA_URL = os.environ.get('CJH_OLLAMA_URL', 'http://127.0.0.1:11434')


def _require_loopback(url):
    parsed = urllib.parse.urlsplit(url)
    host = parsed.hostname
    try:
        loopback = host == 'localhost' or ipaddress.ip_address(host).is_loopback
    except ValueError:
        loopback = False
    if parsed.scheme not in ('http', 'https') or not loopback or parsed.username is not None:
        raise ValueError('本机服务请求只允许回环地址，不能发送到外部服务。')


class _LocalRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _require_loopback(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def local_urlopen(request, timeout=30):
    """Reach local services directly, without changing the system/global proxy."""
    _require_loopback(request.full_url if isinstance(request, urllib.request.Request) else request)
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _LocalRedirect())
    return opener.open(request, timeout=timeout)


def read(path, default=None):
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else default


def write(path, data):
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def asset_for(assets, identifier):
    if identifier not in assets:
        raise ValueError('素材未在本机登记，请重新关联')
    a = assets[identifier]
    if not Path(a['path']).is_file():
        raise ValueError('素材已移动：' + a['name'])
    return a
