import importlib.util
import json
import os
import signal
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('install_diagnostics', Path(__file__).with_name('install_diagnostics.py'))
diagnostics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(diagnostics)


class InstallDiagnostics(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)/'安装 目录'
        self.runtime = self.root/'runtime/bin/python3'
        self.runtime.parent.mkdir(parents=True)
        self.runtime.write_text('runtime fixture; never executed')
        self.report = Path(self.temp.name)/'reports'/'自检.json'

    def success(self, command, **kwargs):
        runtime = Path(command[0])
        runtime_root = runtime.parent.parent
        if command[-1] in diagnostics.MODULES:
            module = command[-1]
            payload = {'module': module, 'file': str(runtime_root/'lib'/module/'__init__.py'), 'version': '1.0'}
        else:
            payload = {'executable': str(runtime), 'prefix': str(runtime_root),
                       'version': '3.12.15', 'machine': 'arm64', 'system': 'Darwin',
                       'macos': '14.7', 'bits': 64}
        return subprocess.CompletedProcess(command, 0, diagnostics._MARKER + json.dumps(payload) + '\n', '')

    def bundle_runtime(self, container='Frameworks'):
        runtime_root = self.root/'CJH剪辑工作台.app/Contents'/container/'runtime'
        runtime_root.parent.mkdir(parents=True)
        (self.root/'runtime').rename(runtime_root)
        return runtime_root

    def run_with_failure(self, error, module='numpy'):
        def run(command, **kwargs):
            if command[-1] == module:
                if isinstance(error, BaseException):
                    raise error
                return subprocess.CompletedProcess(command, error[0], error[1], error[2])
            return self.success(command, **kwargs)
        with patch.object(diagnostics.subprocess, 'run', side_effect=run) as child:
            result = diagnostics.run_checks(self.root, self.report, timeout=3)
        return result, child

    def test_independent_isolated_imports_and_report_do_not_modify_installation(self):
        before = {str(path.relative_to(self.root)): path.read_bytes()
                  for path in self.root.rglob('*') if path.is_file()}
        with patch.dict(os.environ, {'HF_TOKEN': 'private-token-fixture', 'SECRET_KEY': 'private-secret-fixture',
                                     'PYTHONPATH': '/private/injected/python', 'HTTPS_PROXY': 'https://private-proxy.invalid'}), patch.object(
                diagnostics.subprocess, 'run', side_effect=self.success) as child:
            result = diagnostics.run_checks(self.root, self.report, timeout=3)
        self.assertTrue(result['ready'])
        self.assertEqual(result['issues'], [])
        self.assertEqual(child.call_count, 1 + len(diagnostics.MODULES))
        imported = []
        for call in child.call_args_list:
            command = call.args[0]
            self.assertEqual(command[:4], [str(self.runtime), '-I', '-B', '-c'])
            self.assertEqual(call.kwargs['timeout'], 3)
            self.assertFalse(call.kwargs.get('shell', False))
            self.assertEqual(call.kwargs['env']['HF_HUB_OFFLINE'], '1')
            self.assertEqual(call.kwargs['stdin'], subprocess.DEVNULL)
            for key in ('HF_TOKEN', 'SECRET_KEY', 'PYTHONPATH', 'HTTPS_PROXY'):
                self.assertNotIn(key, call.kwargs['env'])
            if command[-1] in diagnostics.MODULES:
                imported.append(command[-1])
        self.assertEqual(imported, list(diagnostics.MODULES))
        self.assertEqual(json.loads(self.report.read_text()), result)
        self.assertNotIn('private-token-fixture', self.report.read_text())
        self.assertNotIn('private-secret-fixture', self.report.read_text())
        self.assertEqual({str(path.relative_to(self.root)): path.read_bytes()
                          for path in self.root.rglob('*') if path.is_file()}, before)

    def test_full_loader_traceback_is_preserved_and_specific_root_cause_wins(self):
        traceback = ('Traceback (most recent call last):\n'
                     '  File "/Applications/安装 目录/runtime/lib/numpy/__init__.py", line 23, in <module>\n'
                     '    from . import _core\n'
                     'ImportError: Library not loaded: @rpath/libopenblas.dylib\n'
                     'Reason: code signature in /Applications/安装 目录/runtime/lib/libopenblas.dylib not valid for use in process\n'
                     'The above exception was the direct cause of the following exception:\n'
                     'ImportError: Error importing numpy: you should not try to import numpy from its source directory\n')
        result, child = self.run_with_failure((1, 'import beginning\n', traceback))
        self.assertFalse(result['ready'])
        numpy = result['checks']['import:numpy']
        self.assertEqual(numpy['category'], 'code_signature_or_os_policy')
        self.assertEqual(numpy['stderr'], traceback)
        self.assertEqual(numpy['stdout'], 'import beginning\n')
        self.assertEqual(json.loads(self.report.read_text())['checks']['import:numpy']['stderr'], traceback)
        self.assertEqual(child.call_count, 1 + len(diagnostics.MODULES))
        self.assertEqual(result['checks']['import:pyJianYingDraft']['status'], 'passed')

    def test_failure_categories_are_based_on_actual_loader_evidence(self):
        cases = [
            ('ImportError: incompatible architecture (have x86_64, need arm64)', 'wrong_architecture'),
            ('ModuleNotFoundError: No module named av', 'missing_dependency'),
            ('ImportError: Library not loaded: @rpath/libomp.dylib\nReason: tried: /tmp/libomp.dylib (no such file)', 'missing_dependency'),
            ('ImportError: library load disallowed by system policy', 'code_signature_or_os_policy'),
            ('PermissionError: Operation not permitted', 'permission_denied'),
            ('ImportError: numpy.core.multiarray failed to import', 'import_or_runtime_error'),
            ('ImportError: Symbol not found: _some_symbol', 'import_or_runtime_error'),
            ('Diagnostic: code signature valid\nImportError: numpy.core.multiarray failed to import', 'import_or_runtime_error'),
        ]
        for message, category in cases:
            with self.subTest(message=message):
                result, _ = self.run_with_failure((1, '', message))
                self.assertEqual(result['checks']['import:numpy']['category'], category)

    def test_sigkill_does_not_claim_gatekeeper_cause_and_remaining_imports_continue(self):
        result, child = self.run_with_failure((-signal.SIGKILL, '', ''))
        check = result['checks']['import:numpy']
        self.assertEqual(check['status'], 'terminated')
        self.assertEqual(check['category'], 'process_terminated')
        self.assertEqual(check['signal'], 'SIGKILL')
        self.assertIn('无法确定', check['summary'])
        self.assertEqual(child.call_count, 1 + len(diagnostics.MODULES))

    def test_timeout_is_blocked_with_partial_output_and_later_checks_continue(self):
        timeout = subprocess.TimeoutExpired(['runtime'], 3, output=b'import started\n', stderr=b'partial stderr\n')
        result, child = self.run_with_failure(timeout, 'torch')
        check = result['checks']['import:torch']
        self.assertEqual(check['status'], 'blocked')
        self.assertEqual(check['category'], 'timeout')
        self.assertEqual(check['stdout'], 'import started\n')
        self.assertEqual(check['stderr'], 'partial stderr\n')
        self.assertFalse(result['ready'])
        self.assertEqual(child.call_count, 1 + len(diagnostics.MODULES))

    def test_runtime_timeout_skips_imports_instead_of_repeating_blocked_executable(self):
        with patch.object(diagnostics.subprocess, 'run', side_effect=subprocess.TimeoutExpired(['runtime'], 3)) as child:
            result = diagnostics.run_checks(self.root, self.report, timeout=3)
        self.assertEqual(result['checks']['runtime']['category'], 'timeout')
        self.assertEqual(child.call_count, 1)
        self.assertTrue(all(result['checks']['import:' + module]['category'] == 'runtime_prerequisite'
                            for module in diagnostics.MODULES))

    def test_spawn_permission_error_is_distinct_from_signature_evidence(self):
        with patch.object(diagnostics.subprocess, 'run', side_effect=PermissionError(13, 'Permission denied')):
            result = diagnostics.run_checks(self.root, self.report)
        check = result['checks']['runtime']
        self.assertEqual(check['status'], 'blocked')
        self.assertEqual(check['category'], 'permission_denied')
        self.assertIn('不能确定', check['summary'])

    def test_runtime_identity_architecture_and_os_must_match_package_requirements(self):
        cases = [({'machine': 'x86_64'}, 'wrong_architecture'),
                 ({'macos': '13.7'}, 'unsupported_platform'),
                 ({'executable': '/usr/bin/python3'}, 'runtime_identity'),
                 ({'prefix': '/usr/local'}, 'runtime_identity')]
        for change, category in cases:
            with self.subTest(change=change):
                def run(command, **kwargs):
                    process = self.success(command, **kwargs)
                    identity = json.loads(process.stdout[len(diagnostics._MARKER):])
                    identity.update(change)
                    process.stdout = diagnostics._MARKER + json.dumps(identity)
                    return process
                with patch.object(diagnostics.subprocess, 'run', side_effect=run) as child:
                    result = diagnostics.run_checks(self.root, self.report)
                self.assertFalse(result['ready'])
                self.assertEqual(result['checks']['runtime']['category'], category)
                self.assertEqual(child.call_count, 1)

    def test_runtime_link_outside_nominated_installation_is_not_bundled_readiness(self):
        outside = Path(self.temp.name)/'other-python'
        outside.write_text('unrelated runtime')
        self.runtime.unlink()
        self.runtime.symlink_to(outside)
        with patch.object(diagnostics.subprocess, 'run', side_effect=self.success):
            result = diagnostics.run_checks(self.root, self.report)
        self.assertFalse(result['ready'])
        self.assertEqual(result['checks']['runtime']['category'], 'runtime_identity')
        self.assertEqual(result['runtime_file']['resolved_path'], str(outside.resolve()))

    def test_app_framework_runtime_is_selected_for_full_bundle_install(self):
        bundled = self.bundle_runtime()/'bin/python3'
        with patch.object(diagnostics.subprocess, 'run', side_effect=self.success) as child:
            result = diagnostics.run_checks(self.root, self.report)
        self.assertTrue(result['ready'])
        self.assertEqual(result['root'], str(self.root))
        self.assertEqual(result['runtime'], str(bundled))
        self.assertEqual(child.call_count, 1 + len(diagnostics.MODULES))
        self.assertTrue(all(call.args[0][0] == str(bundled) for call in child.call_args_list))

    def test_app_resources_runtime_precedes_legacy_framework_layout(self):
        bundled = self.bundle_runtime('Resources')/'bin/python3'
        legacy = self.root/'CJH剪辑工作台.app/Contents/Frameworks/runtime/bin/python3'
        legacy.parent.mkdir(parents=True)
        legacy.write_text('legacy fallback; never executed')
        with patch.object(diagnostics.subprocess, 'run', side_effect=self.success) as child:
            result = diagnostics.run_checks(self.root, self.report)
        self.assertTrue(result['ready'])
        self.assertEqual(result['runtime'], str(bundled))
        self.assertTrue(all(call.args[0][0] == str(bundled) for call in child.call_args_list))

    def test_code_root_alias_to_resources_runtime_is_valid(self):
        bundled = self.bundle_runtime('Resources')
        code_root = self.root/'CJH剪辑工作台.app/Contents/Resources/workbench'
        code_root.mkdir()
        (code_root/'runtime').symlink_to('../runtime', target_is_directory=True)

        def run(command, **kwargs):
            process = self.success(command, **kwargs)
            if command[-1] not in diagnostics.MODULES:
                identity = json.loads(process.stdout[len(diagnostics._MARKER):])
                identity.update(executable=str(code_root/'runtime/bin/python3'), prefix=str(code_root/'runtime'))
                process.stdout = diagnostics._MARKER + json.dumps(identity)
            return process

        with patch.object(diagnostics.subprocess, 'run', side_effect=run):
            result = diagnostics.run_checks(self.root, self.report)
        self.assertTrue(result['ready'])
        self.assertEqual(result['runtime'], str(bundled/'bin/python3'))

    def test_flat_runtime_takes_precedence_when_both_layouts_exist(self):
        bundled = self.root/'CJH剪辑工作台.app/Contents/Frameworks/runtime/bin/python3'
        bundled.parent.mkdir(parents=True)
        bundled.write_text('second runtime; never executed')
        with patch.object(diagnostics.subprocess, 'run', side_effect=self.success) as child:
            result = diagnostics.run_checks(self.root, self.report)
        self.assertTrue(result['ready'])
        self.assertEqual(result['runtime'], str(self.runtime))
        self.assertTrue(all(call.args[0][0] == str(self.runtime) for call in child.call_args_list))

    def test_code_root_runtime_alias_inside_same_installation_is_valid(self):
        bundled = self.bundle_runtime()
        code_root = self.root/'CJH剪辑工作台.app/Contents/Resources/workbench'
        code_root.mkdir(parents=True)
        (code_root/'runtime').symlink_to('../../Frameworks/runtime', target_is_directory=True)

        def run(command, **kwargs):
            process = self.success(command, **kwargs)
            if command[-1] not in diagnostics.MODULES:
                identity = json.loads(process.stdout[len(diagnostics._MARKER):])
                identity.update(executable=str(code_root/'runtime/bin/python3'), prefix=str(code_root/'runtime'))
                process.stdout = diagnostics._MARKER + json.dumps(identity)
            return process

        with patch.object(diagnostics.subprocess, 'run', side_effect=run):
            result = diagnostics.run_checks(self.root, self.report)
        self.assertTrue(result['ready'])
        self.assertEqual(result['runtime'], str(bundled/'bin/python3'))

    def test_flat_runtime_link_to_app_framework_inside_installation_is_valid(self):
        bundled = self.bundle_runtime()
        (self.root/'runtime').symlink_to(bundled, target_is_directory=True)
        with patch.object(diagnostics.subprocess, 'run', side_effect=self.success):
            result = diagnostics.run_checks(self.root, self.report)
        self.assertTrue(result['ready'])
        self.assertEqual(result['runtime'], str(self.runtime))
        self.assertEqual(result['runtime_file']['resolved_path'], str((bundled/'bin/python3').resolve()))

    def test_app_framework_link_outside_installation_is_rejected(self):
        bundled = self.bundle_runtime()
        outside = Path(self.temp.name)/'external-runtime'
        bundled.rename(outside)
        bundled.symlink_to(outside, target_is_directory=True)
        with patch.object(diagnostics.subprocess, 'run', side_effect=self.success) as child:
            result = diagnostics.run_checks(self.root, self.report)
        self.assertFalse(result['ready'])
        self.assertEqual(result['checks']['runtime']['category'], 'runtime_identity')
        self.assertEqual(child.call_count, 1)

    def test_missing_runtime_writes_actionable_report_without_spawning(self):
        self.runtime.unlink()
        with patch.object(diagnostics.subprocess, 'run') as child:
            result = diagnostics.run_checks(self.root, self.report)
        child.assert_not_called()
        self.assertFalse(result['ready'])
        self.assertEqual(result['checks']['runtime']['category'], 'runtime_missing')
        self.assertEqual(result['runtime'], str(self.runtime))
        self.assertTrue(self.report.is_file())

    def test_zero_exit_without_probe_result_is_not_a_pass(self):
        result, _ = self.run_with_failure((0, 'some library warning\n', ''))
        self.assertEqual(result['checks']['import:numpy']['category'], 'probe_error')
        self.assertFalse(result['ready'])

    def test_quarantine_attribute_name_alone_is_not_a_blocking_diagnosis(self):
        with patch.object(diagnostics.os, 'listxattr', return_value=['com.apple.quarantine'], create=True), patch.object(
                diagnostics.subprocess, 'run', side_effect=self.success):
            result = diagnostics.run_checks(self.root, self.report)
        self.assertTrue(result['ready'])
        self.assertEqual(result['runtime_file']['extended_attribute_names'], ['com.apple.quarantine'])

    def test_invalid_timeout_never_executes_or_creates_report(self):
        for timeout in (0, -1, float('nan'), float('inf')):
            with self.subTest(timeout=timeout), patch.object(diagnostics.subprocess, 'run') as child:
                with self.assertRaises(ValueError):
                    diagnostics.run_checks(self.root, self.report, timeout=timeout)
                child.assert_not_called()
        self.assertFalse(self.report.exists())


if __name__ == '__main__':
    unittest.main()
