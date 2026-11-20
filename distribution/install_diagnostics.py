"""Read-only, offline runtime/import checks driven by a working host Python.

No models or editors are started. This module never changes quarantine attributes,
code signatures, Gatekeeper, TCC, or the nominated installation's files.
"""
import argparse
import datetime
import json
import math
import os
import platform
import re
import signal
import subprocess
import sys
from pathlib import Path


MODULES = ('numpy', 'torch', 'faster_whisper', 'av', 'mediapipe',
           'sherpa_onnx', 'transformers', 'pyJianYingDraft')
_MARKER = 'CJH_DIAGNOSTIC_JSON:'
_RUNTIME_PROBE = '''import json, platform, struct, sys
print(%r + json.dumps({"executable": sys.executable, "prefix": sys.prefix,
    "version": sys.version, "machine": platform.machine(),
    "system": platform.system(), "macos": platform.mac_ver()[0],
    "bits": struct.calcsize("P") * 8}))
''' % _MARKER
_IMPORT_PROBE = '''import importlib, json, sys
module = importlib.import_module(sys.argv[1])
print(%r + json.dumps({"module": sys.argv[1],
    "file": getattr(module, "__file__", None),
    "version": str(getattr(module, "__version__", "unknown"))}))
''' % _MARKER


def _text(value):
    return value.decode('utf-8', errors='replace') if isinstance(value, bytes) else (value or '')


def _failure_details(text):
    """Classify only evidence in the actual loader/OS error, not package names."""
    lower = text.lower()
    if re.search(r'code signature[^\n]*(?:invalid|not valid|missing|rejected|failed)', lower) or any(value in lower for value in (
        'code object is not signed at all', 'not valid for use in process using library validation',
        'library load disallowed by system policy', 'not allowed by system policy',
        'mapped file has no cdhash', 'developer cannot be verified',
        'apple could not verify', 'errseccs',
    )):
        return 'code_signature_or_os_policy', '错误明确涉及代码签名或系统加载策略；请按报告中的具体错误处理。'
    if any(value in lower for value in (
        'incompatible architecture', 'bad cpu type in executable',
        'mach-o, but wrong architecture', 'wrong elf class',
    )):
        return 'wrong_architecture', '错误明确提示二进制架构不兼容。'
    if any(value in lower for value in (
        'no module named', 'library not loaded:', 'image not found',
        'cannot open shared object file: no such file',
        'the specified module could not be found',
    )):
        return 'missing_dependency', '错误提示缺少 Python 模块或动态库；完整路径和加载原因见报告。'
    if 'permission denied' in lower or 'operation not permitted' in lower:
        return 'permission_denied', '系统拒绝执行或访问；仅凭权限错误不能确定是 Gatekeeper。'
    return 'import_or_runtime_error', '运行或导入失败，尚不能确定原因；请查看完整错误输出。'


def _child_environment():
    # Do not forward credentials, proxy settings, Python path overrides or tokens.
    environment = {key: os.environ[key] for key in
                   ('HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'LC_CTYPE') if key in os.environ}
    environment.update({
        'PATH': '/usr/bin:/bin:/usr/sbin:/sbin',
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1',
        'HF_HUB_DISABLE_TELEMETRY': '1', 'DO_NOT_TRACK': '1',
    })
    return environment


def _run_child(command, root, timeout):
    result = {'command': command, 'timeout_seconds': timeout, 'stdout': '', 'stderr': ''}
    try:
        process = subprocess.run(command, cwd=str(root), env=_child_environment(),
                                 stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True, encoding='utf-8',
                                 errors='replace', timeout=timeout, check=False)
    except subprocess.TimeoutExpired as error:
        result.update(status='blocked', category='timeout',
                      summary='检查超过 %s 秒，子进程已停止；结果未完成，不能视为通过。' % timeout,
                      stdout=_text(error.stdout), stderr=_text(error.stderr))
        return result
    except OSError as error:
        category, summary = _failure_details(str(error))
        if isinstance(error, FileNotFoundError):
            category, summary = 'runtime_unavailable', '无法执行指定运行时；文件或其系统加载器可能缺失。'
        result.update(status='blocked', category=category, summary=summary,
                      stderr=str(error), errno=error.errno)
        return result

    result.update(returncode=process.returncode, stdout=_text(process.stdout), stderr=_text(process.stderr))
    if process.returncode:
        category, summary = _failure_details(result['stderr'] + '\n' + result['stdout'])
        status = 'failed'
        if process.returncode < 0:
            status = 'terminated'
            try:
                signal_name = signal.Signals(-process.returncode).name
            except ValueError:
                signal_name = 'signal %s' % -process.returncode
            result['signal'] = signal_name
            if category == 'import_or_runtime_error':
                category = 'process_terminated'
                summary = '进程被 %s 终止；仅凭信号无法确定是否由 macOS 安全策略引起。' % signal_name
            else:
                summary = '进程被 %s 终止。%s' % (signal_name, summary)
        result.update(status=status, category=category, summary=summary)
        return result

    for line in reversed(result['stdout'].splitlines()):
        if line.startswith(_MARKER):
            try:
                identity = json.loads(line[len(_MARKER):])
            except (TypeError, ValueError):
                break
            if isinstance(identity, dict):
                result.update(status='passed', category='ok', summary='检查通过。', identity=identity)
                return result
    result.update(status='failed', category='probe_error', summary='进程正常退出但未返回完整检查结果，不能视为通过。')
    return result


def _runtime_identity(result, runtime, install_root):
    identity = result.get('identity', {})
    required = ('executable', 'prefix', 'version', 'machine', 'system', 'macos', 'bits')
    if not all(name in identity for name in required):
        result.update(status='failed', category='probe_error', summary='运行时返回的身份信息不完整。')
        return
    try:
        canonical_runtime_root = runtime.parent.parent.resolve()
        canonical_runtime_root.relative_to(install_root.resolve())
        runtime.resolve().relative_to(install_root.resolve())
        Path(identity['executable']).resolve().relative_to(canonical_runtime_root)
        Path(identity['prefix']).resolve().relative_to(canonical_runtime_root)
    except (TypeError, ValueError):
        result.update(status='failed', category='runtime_identity', summary='运行时位于指定安装目录之外，或实际解释器、安装前缀不属于选定 runtime。')
        return
    if identity['machine'] != 'arm64' or identity['bits'] != 64:
        result.update(status='failed', category='wrong_architecture',
                      summary='实际运行时架构为 %s / %s 位；本安装包要求原生 arm64 / 64 位。' %
                      (identity['machine'], identity['bits']))
    elif identity['system'] != 'Darwin':
        result.update(status='failed', category='unsupported_platform', summary='实际运行时不是 macOS；本安装包要求 macOS 14 或更新版本。')
    else:
        try:
            major = int(identity['macos'].split('.')[0])
        except (AttributeError, TypeError, ValueError):
            result.update(status='failed', category='probe_error', summary='无法确定实际运行时的 macOS 版本。')
            return
        if major < 14:
            result.update(status='failed', category='unsupported_platform', summary='实际运行时系统低于本安装包要求的 macOS 14。')


def _metadata(path):
    metadata = {'path': str(path), 'resolved_path': str(path.resolve()), 'exists': path.exists()}
    if hasattr(os, 'listxattr') and path.exists():
        try:
            metadata['extended_attribute_names'] = sorted(os.listxattr(path))
        except OSError as error:
            metadata['metadata_error'] = str(error)
    return metadata


def run_checks(root, report_path, timeout=60):
    """Return readiness plus detailed checks and write only the chosen JSON report.

    Root is the installation directory, not the App's internal code directory.
    Flat runtimes and App Resources runtimes are supported; Frameworks remains
    a fallback for earlier bundle layouts.
    Each import has its own timeout and isolated interpreter. Passing these checks
    establishes import readiness, not model inference or editor integration.
    """
    timeout = float(timeout)
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('timeout 必须是大于 0 的有限秒数')
    root = Path(root).expanduser().absolute()
    report_path = Path(report_path).expanduser().absolute()
    runtime_candidates = (root/'runtime/bin/python3',
                          root/'CJH剪辑工作台.app/Contents/Resources/runtime/bin/python3',
                          root/'CJH剪辑工作台.app/Contents/Frameworks/runtime/bin/python3')
    runtime = next((path for path in runtime_candidates if path.is_file()), runtime_candidates[0])
    checks = {}
    report = {
        'schema_version': 1,
        'created_at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'root': str(root), 'runtime': str(runtime), 'report_path': str(report_path),
        'runtime_candidates': [str(path) for path in runtime_candidates],
        'host': {'system': platform.system(), 'machine': platform.machine(),
                 'macos': platform.mac_ver()[0], 'diagnostic_python': sys.executable},
        'runtime_file': _metadata(runtime), 'checks': checks,
        'note': '仅检查运行时和依赖导入，不代表模型推理或剪辑软件联动通过。隔离属性是否存在不能单独证明系统阻止了加载。',
    }
    if not runtime.is_file():
        checks['runtime'] = {'status': 'blocked', 'category': 'runtime_missing',
                             'summary': '未找到平铺或 App 内 runtime 中的 bin/python3；检查路径见报告。'}
    else:
        checks['runtime'] = _run_child([str(runtime), '-I', '-B', '-c', _RUNTIME_PROBE], root, timeout)
        if checks['runtime']['status'] == 'passed':
            _runtime_identity(checks['runtime'], runtime, root)

    for module in MODULES:
        key = 'import:' + module
        if checks['runtime']['status'] != 'passed':
            checks[key] = {'status': 'blocked', 'category': 'runtime_prerequisite',
                           'summary': '运行时检查未通过，未尝试此项导入。'}
            continue
        result = _run_child([str(runtime), '-I', '-B', '-c', _IMPORT_PROBE, module], root, timeout)
        if result['status'] == 'passed' and result['identity'].get('module') != module:
            result.update(status='failed', category='probe_error', summary='子进程未确认预期依赖已导入。')
        checks[key] = result

    report['issues'] = [{'check': name, 'category': check['category'], 'summary': check['summary']}
                        for name, check in checks.items() if check['status'] != 'passed']
    report['ready'] = not report['issues']
    report['summary'] = ('运行时和全部 %s 项依赖导入检查通过。' % len(MODULES) if report['ready'] else
                         '自检未通过；%s 项失败或未完成。' % len(report['issues']))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    # Restrict a newly created report; do not change permissions on an existing file.
    descriptor = os.open(str(report_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as output:
        json.dump(report, output, ensure_ascii=False, indent=2)
        output.write('\n')
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description='只读检查指定安装的 Python 运行时和依赖，保存完整错误报告。')
    parser.add_argument('root', type=Path, help='包含 runtime 目录或 CJH剪辑工作台.app 的安装根目录')
    parser.add_argument('--report', type=Path, required=True, help='本地 JSON 报告保存位置')
    parser.add_argument('--timeout', type=float, default=60, help='每个子进程的超时秒数（默认 60）')
    args = parser.parse_args(argv)
    try:
        report = run_checks(args.root, args.report, args.timeout)
    except (OSError, ValueError) as error:
        print('无法完成自检或保存报告：' + str(error), file=sys.stderr)
        return 2
    print(report['summary'])
    for issue in report['issues']:
        print('%s [%s]：%s' % (issue['check'], issue['category'], issue['summary']))
    print('完整报告：' + report['report_path'])
    print(report['note'])
    return 0 if report['ready'] else 1


if __name__ == '__main__':
    sys.exit(main())
