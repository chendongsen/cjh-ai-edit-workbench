import hashlib
import importlib.util
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('compatible_upgrade', Path(__file__).with_name('compatible_upgrade.py'))
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


class CompatibleUpgrade(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.package = self.root / '补丁'
        self.package.mkdir()
        self.target = self.make_install('CJH剪辑工作台-0.1.4-beta.20261023')
        self.make_package()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.object(upgrade, 'native_platform'))
        self.stack.enter_context(patch.object(upgrade.Path, 'home', return_value=self.root))
        self.listener = self.stack.enter_context(patch.object(upgrade, 'target_listener_pids', return_value=[]))
        self.probe = self.stack.enter_context(patch.object(upgrade, 'probe_runtime', return_value={'ready': True, 'checks': {'numpy': {'ok': True}}}))

    def make_install(self, name, version='0.1.4-beta.20261023'):
        root = self.root / 'Applications' / name
        for name in ('backend', 'dist', 'runtime/bin', '.local/jobs/task-one', '.local/models', 'CJH剪辑工作台.app/Contents/MacOS'):
            (root / name).mkdir(parents=True, exist_ok=True)
        (root / 'runtime/bin/python3').write_bytes(b'old healthy runtime')
        (root / 'runtime/bin/python3').chmod(0o755)
        (root / 'backend/beta.py').write_text('VERSION = ' + repr(version) + '\n')
        (root / 'backend/server.py').write_text('answer = "old server"\n')
        (root / 'dist/index.html').write_text('old UI')
        (root / '.local/jobs/task-one/status.json').write_text(json.dumps({'id': 'task-one', 'status': 'failed'}))
        (root / '.local/jobs/task-one/input.json').write_text('{"originalMedia":"keep"}')
        (root / '.local/assets.json').write_text('{"asset":"original path"}')
        (root / '.local/models/weights.bin').write_bytes(b'old model')
        (root / 'CJH剪辑工作台.app/Contents/MacOS/Launcher').write_bytes(b'original desktop launcher')
        return root

    def make_package(self):
        files = {
            'backend/beta.py': 'VERSION = ' + repr(upgrade.VERSION) + '\n',
            'backend/server.py': 'answer = "fixed server"\n',
            'backend/new_helper.py': 'answer = "new helper"\n',
            'dist/index.html': 'new UI',
        }
        manifest = {'version': upgrade.VERSION, 'files': {}}
        for name, text in files.items():
            path = self.package / 'code' / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            manifest['files'][name] = {'size': path.stat().st_size, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
        (self.package / 'upgrade-manifest.json').write_text(json.dumps(manifest))

    def run_upgrade(self, target=None):
        return upgrade.upgrade(target or self.target, base=self.package, reporter=lambda _: None)

    def protected_bytes(self):
        protected = ['runtime/bin/python3', '.local/jobs/task-one/status.json',
                     '.local/jobs/task-one/input.json', '.local/assets.json', '.local/models/weights.bin',
                     'CJH剪辑工作台.app/Contents/MacOS/Launcher']
        return {name: (self.target / name).read_bytes() for name in protected}

    def test_updates_only_code_and_preserves_runtime_models_tasks_and_launcher(self):
        before = self.protected_bytes()
        original = (self.target / 'backend/server.py').read_bytes()
        result = self.run_upgrade()
        self.assertEqual(result['status'], 'upgraded')
        self.assertEqual(upgrade.version_from_file(self.target / 'backend/beta.py'), upgrade.VERSION)
        self.assertEqual((self.target / 'dist/index.html').read_text(), 'new UI')
        self.assertEqual(self.protected_bytes(), before)
        self.assertEqual((Path(result['backup']) / 'files/backend/server.py').read_bytes(), original)
        self.assertEqual(self.probe.call_count, 2)

    def test_tampered_package_rejected_before_any_installed_file_changes(self):
        before = self.protected_bytes()
        (self.package / 'code/backend/server.py').write_text('tampered = True\n')
        with self.assertRaisesRegex(ValueError, '校验失败'):
            self.run_upgrade()
        self.assertEqual((self.target / 'backend/server.py').read_text(), 'answer = "old server"\n')
        self.assertFalse((self.target / '.upgrade-backups').exists())
        self.assertEqual(self.protected_bytes(), before)

    def test_manifest_cannot_write_data_or_native_payload(self):
        for path in ('.local/assets.json', 'runtime/bin/python3', 'backend/tool.so', 'backend/../dist/index.html'):
            with self.subTest(path=path):
                manifest = json.loads((self.package / 'upgrade-manifest.json').read_text())
                manifest['files'][path] = {'size': 0, 'sha256': hashlib.sha256(b'').hexdigest()}
                (self.package / 'upgrade-manifest.json').write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, '不允许更新'):
                    self.run_upgrade()
                del manifest['files'][path]
                (self.package / 'upgrade-manifest.json').write_text(json.dumps(manifest))
        self.assertFalse((self.target / '.upgrade-backups').exists())

    def test_old_incompatible_runtime_version_is_not_upgraded(self):
        (self.target / 'backend/beta.py').write_text("VERSION = '0.1.1-beta.20261021'\n")
        with self.assertRaisesRegex(ValueError, '只支持'):
            self.run_upgrade()
        self.probe.assert_not_called()

    def test_active_task_refuses_without_touching_data(self):
        job = self.target / '.local/jobs/task-one/status.json'
        job.write_text('{"status":"running"}')
        with self.assertRaisesRegex(ValueError, '粗剪任务'):
            self.run_upgrade()
        self.assertEqual(job.read_text(), '{"status":"running"}')
        self.assertFalse((self.target / '.upgrade-backups').exists())

    def test_running_service_refuses_and_never_terminates_it(self):
        self.listener.return_value = [123]
        with patch.object(upgrade.os, 'kill') as kill:
            with self.assertRaisesRegex(ValueError, '退出后台服务'):
                self.run_upgrade()
            kill.assert_not_called()
        self.probe.assert_not_called()

    def test_unhealthy_old_runtime_leaves_code_untouched_and_writes_diagnosis(self):
        self.probe.return_value = {'ready': False, 'stderr': 'NumPy: library load disallowed by system policy'}
        with self.assertRaisesRegex(ValueError, '自检失败'):
            self.run_upgrade()
        reports = list(self.package.glob('升级前自检失败-*.json'))
        self.assertEqual(len(reports), 1)
        self.assertIn('library load disallowed', reports[0].read_text())
        self.assertEqual(upgrade.version_from_file(self.target / 'backend/beta.py'), '0.1.4-beta.20261023')
        self.assertFalse((self.target / '.upgrade-backups').exists())

    def test_interrupted_copy_rolls_back_existing_files_and_removes_added_files(self):
        before = self.protected_bytes()
        original = upgrade.atomic_copy
        failed = False

        def fault(source, target):
            nonlocal failed
            if target == self.target.resolve() / 'dist/index.html' and not failed:
                failed = True
                raise OSError('simulated interrupted write')
            return original(source, target)

        with patch.object(upgrade, 'atomic_copy', side_effect=fault):
            with self.assertRaisesRegex(RuntimeError, '已恢复原代码'):
                self.run_upgrade()
        self.assertEqual(upgrade.version_from_file(self.target / 'backend/beta.py'), '0.1.4-beta.20261023')
        self.assertEqual((self.target / 'backend/server.py').read_text(), 'answer = "old server"\n')
        self.assertFalse((self.target / 'backend/new_helper.py').exists())
        self.assertEqual(self.protected_bytes(), before)
        report = json.loads(next((self.target / '.upgrade-backups').glob('*/upgrade-report.json')).read_text())
        self.assertEqual(report['status'], 'rolled-back')

    def test_postflight_failure_rolls_back(self):
        self.probe.side_effect = [{'ready': True}, {'ready': False, 'stderr': 'probe failed'}]
        with self.assertRaisesRegex(RuntimeError, '已恢复原代码'):
            self.run_upgrade()
        self.assertEqual(upgrade.version_from_file(self.target / 'backend/beta.py'), '0.1.4-beta.20261023')
        self.assertFalse((self.target / 'backend/new_helper.py').exists())

    def test_repeating_identical_upgrade_does_not_make_another_backup(self):
        self.run_upgrade()
        backups = list((self.target / '.upgrade-backups').iterdir())
        result = self.run_upgrade()
        self.assertEqual(result['status'], 'already-installed')
        self.assertEqual(list((self.target / '.upgrade-backups').iterdir()), backups)

    def test_concurrent_upgrade_refuses_without_writing_code(self):
        with upgrade.upgrade_lock(self.target):
            with self.assertRaisesRegex(ValueError, '另一个升级程序'):
                self.run_upgrade()
        self.assertEqual(upgrade.version_from_file(self.target / 'backend/beta.py'), '0.1.4-beta.20261023')
        self.assertFalse((self.target / '.upgrade-backups').exists())

    def test_same_version_with_unknown_code_is_not_overwritten(self):
        self.run_upgrade()
        (self.target / 'backend/server.py').write_text('custom_change = True\n')
        with self.assertRaisesRegex(ValueError, '未知修改'):
            self.run_upgrade()

    def test_ambiguous_same_version_requires_explicit_target(self):
        self.make_install('CJH剪辑工作台-另一份0.1.4')
        with self.assertRaisesRegex(ValueError, '多个同版本'):
            upgrade.choose_target(reporter=lambda _: None)

    def test_auto_detection_chooses_unique_latest_compatible_version(self):
        self.make_install('CJH剪辑工作台-0.1.3-beta.20261021', '0.1.3-beta.20261021')
        root, version = upgrade.choose_target(reporter=lambda _: None)
        self.assertEqual(root, self.target.resolve())
        self.assertEqual(version, '0.1.4-beta.20261023')

    def test_symlinked_patch_and_installed_code_are_rejected(self):
        (self.package / 'code/backend/server.py').unlink()
        (self.package / 'code/backend/server.py').symlink_to(self.target / 'backend/server.py')
        with self.assertRaisesRegex(ValueError, '符号链接'):
            self.run_upgrade()
        (self.package / 'code/backend/server.py').unlink()
        self.make_package()
        (self.target / 'backend/server.py').unlink()
        (self.target / 'backend/server.py').symlink_to(self.target / '.local/assets.json')
        with self.assertRaisesRegex(ValueError, '符号链接'):
            self.run_upgrade()


if __name__ == '__main__':
    unittest.main()
