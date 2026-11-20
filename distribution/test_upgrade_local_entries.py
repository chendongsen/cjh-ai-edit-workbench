import hashlib
import importlib.util
import json
import os
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('upgrade_local_entries',
                                            Path(__file__).with_name('upgrade_local_entries.py'))
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


class EntrySwitchSafety(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='入口 中文 空格 ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.target = self.install('新版 0.1.4')
        self.olds = [self.install('旧版 0.1.2'), self.install('旧版 0.1.3')]
        self.desktop = self.root / '桌面 有空格' / upgrade.APP_NAME
        self.desktop.parent.mkdir()
        self.desktop.symlink_to(self.olds[0] / upgrade.APP_NAME)
        self.backup = self.root / '升级 回退备份'

    def install(self, name):
        root = self.root / name
        app = root / upgrade.APP_NAME
        binary = app / 'Contents/MacOS/CJHLauncher'
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b'fixture signed launcher ' + name.encode())
        binary.chmod(0o755)
        with (app / 'Contents/Info.plist').open('wb') as stream:
            plistlib.dump({'CFBundleIdentifier': upgrade.BUNDLE_ID,
                          'CFBundleExecutable': 'CJHLauncher'}, stream)
        signature = app / 'Contents/_CodeSignature/CodeResources'
        signature.parent.mkdir()
        signature.write_bytes(b'unchanged signature fixture')
        command = root / upgrade.COMMAND_NAME
        command.write_text('#!/bin/zsh\nexec ./runtime/bin/python3 backend/launch.py\n')
        command.chmod(0o755)
        runtime = root / 'runtime/bin/python3'
        runtime.parent.mkdir(parents=True)
        runtime.write_bytes(b'fixture runtime')
        runtime.chmod(0o755)
        backend = root / 'backend/launch.py'
        backend.parent.mkdir()
        backend.write_text('# old/new implementation must stay untouched\n')
        data = root / '.local/jobs/历史任务/status.json'
        data.parent.mkdir(parents=True)
        data.write_text('{"status":"completed","素材":"不要修改"}')
        return root

    def snapshot(self, path):
        return {str(file.relative_to(path)): (hashlib.sha256(file.read_bytes()).hexdigest(),
                                               file.stat().st_mode)
                for file in path.rglob('*') if file.is_file()}

    def run_switch(self, execute=True, backup=None):
        return upgrade.switch_entries(self.target, self.olds, self.desktop,
                                      backup or self.backup, execute=execute)

    def test_dry_run_does_not_write(self):
        before = self.snapshot(self.root)
        result = self.run_switch(execute=False)
        self.assertEqual(result['status'], 'dry-run')
        self.assertFalse(self.backup.exists())
        self.assertEqual(before, self.snapshot(self.root))
        self.assertFalse((self.olds[0] / upgrade.APP_NAME).is_symlink())

    def test_unicode_spaces_original_content_and_bundles_preserved(self):
        target_before = self.snapshot(self.target)
        old_bundles = [self.snapshot(root / upgrade.APP_NAME) for root in self.olds]
        old_commands = [(root / upgrade.COMMAND_NAME).read_bytes() for root in self.olds]
        data_before = [self.snapshot(root / '.local') for root in self.olds]
        desktop_original = os.readlink(self.desktop)
        result = self.run_switch()
        self.assertEqual(result['status'], 'completed')
        self.assertEqual(target_before, self.snapshot(self.target))
        for i, root in enumerate(self.olds):
            for name in (upgrade.APP_NAME, upgrade.COMMAND_NAME):
                self.assertTrue((root / name).is_symlink())
                self.assertEqual((root / name).resolve(), self.target / name)
            saved_app = self.backup / root.name / (upgrade.APP_NAME + '.backup')
            self.assertEqual(saved_app.suffix, '.backup')
            self.assertEqual(old_bundles[i], self.snapshot(saved_app))
            self.assertEqual(old_commands[i],
                             (self.backup / root.name / (upgrade.COMMAND_NAME + '.backup')).read_bytes())
            self.assertEqual(data_before[i], self.snapshot(root / '.local'))
        self.assertEqual(self.desktop.resolve(), self.target / upgrade.APP_NAME)
        saved_desktop = self.backup / 'desktop' / (upgrade.APP_NAME + '.symlink.backup')
        self.assertEqual(os.readlink(saved_desktop), desktop_original)
        self.assertEqual(json.loads((self.backup / upgrade.RECEIPT_NAME).read_text())['status'], 'completed')

    def test_repeat_execution_rejected_without_changes(self):
        self.run_switch()
        before = self.snapshot(self.root)
        with self.assertRaises(ValueError):
            self.run_switch()
        with self.assertRaises(ValueError):
            self.run_switch(backup=self.root / '第二备份')
        self.assertEqual(before, self.snapshot(self.root))

    def test_unknown_desktop_entry_never_overwritten(self):
        self.desktop.unlink()
        self.desktop.write_text('user file')
        with self.assertRaises(ValueError):
            self.run_switch()
        self.assertEqual(self.desktop.read_text(), 'user file')
        self.assertFalse(self.backup.exists())
        self.desktop.unlink()
        self.desktop.symlink_to(self.root / 'unknown missing app')
        with self.assertRaises(ValueError):
            self.run_switch()
        self.assertTrue(self.desktop.is_symlink())
        self.assertFalse(self.backup.exists())

    def test_failure_restores_all_moved_entries_and_desktop_link(self):
        before = [self.snapshot(root) for root in [self.target, *self.olds]]
        desktop_original = os.readlink(self.desktop)
        real_symlink = os.symlink
        calls = []

        def fail_on_desktop(destination, source):
            calls.append(source)
            if Path(source) == self.desktop:
                raise OSError('injected link failure after desktop move')
            return real_symlink(destination, source)

        with patch.object(upgrade.os, 'symlink', side_effect=fail_on_desktop):
            with self.assertRaises(upgrade.EntrySwitchError):
                self.run_switch()
        self.assertEqual(len(calls), 5)
        self.assertEqual(before, [self.snapshot(root) for root in [self.target, *self.olds]])
        self.assertEqual(os.readlink(self.desktop), desktop_original)
        result = json.loads((self.backup / upgrade.RECEIPT_NAME).read_text())
        self.assertEqual(result['status'], 'rolled-back')
        self.assertTrue(all(action['rolled_back'] for action in result['journal']))

    def test_rollback_does_not_clobber_external_change(self):
        source = self.olds[0] / upgrade.APP_NAME

        def conflict_then_fail(destination, path):
            Path(path).write_text('external file must survive')
            raise OSError('injected concurrent change')

        with patch.object(upgrade.os, 'symlink', side_effect=conflict_then_fail):
            with self.assertRaises(upgrade.EntrySwitchError):
                self.run_switch()
        self.assertEqual(source.read_text(), 'external file must survive')
        self.assertTrue((self.backup / self.olds[0].name / (upgrade.APP_NAME + '.backup')).is_dir())
        result = json.loads((self.backup / upgrade.RECEIPT_NAME).read_text())
        self.assertEqual(result['status'], 'rollback-incomplete')
        self.assertTrue(result['rollback_errors'])

    def test_target_readiness_and_unknown_bundle_rejected(self):
        runtime = self.target / 'runtime/bin/python3'
        runtime.chmod(0o644)
        with self.assertRaises(ValueError):
            self.run_switch()
        runtime.chmod(0o755)
        info = self.olds[0] / upgrade.APP_NAME / 'Contents/Info.plist'
        with info.open('wb') as stream:
            plistlib.dump({'CFBundleIdentifier': 'unrelated.app',
                          'CFBundleExecutable': 'CJHLauncher'}, stream)
        with self.assertRaises(ValueError):
            self.run_switch()
        self.assertFalse(self.backup.exists())

    def test_missing_desktop_supported(self):
        self.desktop.unlink()
        result = self.run_switch()
        self.assertIsNone(result['entries'][-1]['backup'])
        self.assertEqual(self.desktop.resolve(), self.target / upgrade.APP_NAME)

    def test_backup_symlink_and_overlapping_roots_rejected(self):
        self.backup.symlink_to(self.root / 'missing')
        with self.assertRaises(ValueError):
            self.run_switch()
        self.backup.unlink()
        with self.assertRaises(ValueError):
            upgrade.plan_entries(self.target, [self.target / 'nested'], self.desktop, self.backup)


if __name__ == '__main__':
    unittest.main()
