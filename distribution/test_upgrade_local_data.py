import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

spec = importlib.util.spec_from_file_location('upgrade_local_data', Path(__file__).with_name('upgrade_local_data.py'))
upgrade = importlib.util.module_from_spec(spec)
spec.loader.exec_module(upgrade)


class LocalDataUpgradeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name).resolve()
        self.source, self.target, self.report = [self.base / name for name in ('旧安装', '新安装', '迁移报告')]
        for root in (self.source, self.target):
            (root / '.local').mkdir(parents=True)
        (self.target / 'runtime/bin').mkdir(parents=True)
        (self.target / 'runtime/bin/python3').write_text('runtime')
        (self.target / 'runtime/bin/python3').chmod(0o755)
        (self.target / 'backend').mkdir()
        for name in upgrade.COMMANDS.values():
            (self.target / 'backend' / name).write_text('# placeholder\n')
        self.media = self.source / '.local/imports/batch/files/原素材.mov'
        self.media.parent.mkdir(parents=True)
        self.media.write_bytes(b'original-media')
        self.store('assets.json', {'a': {'id': 'a', 'path': str(self.media)}})
        self.job = {'id': 'j1', 'projectId': 'preserve-project-id', 'kind': 'resolve', 'status': 'failed',
                    'command': ['/old/python', '/old/execute.py'], 'result': {'draftPath': '/keep/draft'}}
        self.store('jobs/j1/status.json', self.job)
        self.store('jobs/j1/input.json', {'source': str(self.media), 'untouched': True})
        (self.source / '.local/jobs/j1/result.json').write_bytes(b'')
        (self.source / '.local/jobs/empty').mkdir()
        self.store('handoff.json', {'enabled': True, 'permissionGrantedByUserRequired': True})
        self.default = b'{"enabled": false, "permissionGrantedByUserRequired": true}\n'
        (self.target / '.local/handoff.json').write_bytes(self.default)
        (self.target / '.local/models').mkdir()
        (self.target / '.local/models/new-model').write_bytes(b'new-model')

    def store(self, name, value):
        path = self.source / '.local' / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def copy(self, source, target):
        if source.is_dir():
            shutil.copytree(source, target, copy_function=shutil.copy2)
        else:
            shutil.copy2(source, target)
        return 'test-copy'

    def run_upgrade(self, execute=True):
        with patch.object(upgrade, 'copy_entry', side_effect=self.copy):
            return upgrade.migrate(self.source, self.target, self.report, execute)

    def test_plan_is_read_only(self):
        result = self.run_upgrade(False)
        self.assertEqual(result['phase'], 'planned')
        self.assertEqual((result['assetCount'], result['jobCount'], result['jobDirectories']), (1, 1, 2))
        self.assertFalse(self.report.exists())
        self.assertFalse((self.target / '.local/assets.json').exists())

    def test_preserves_source_ids_paths_bad_result_and_defaults(self):
        original = (self.source / '.local/jobs/j1/status.json').read_bytes()
        result = self.run_upgrade()
        self.assertEqual(result['phase'], 'complete')
        self.assertTrue(result['sourceUnchanged'])
        self.assertEqual(len(result['existingInvalidJson']), 1)
        self.assertEqual((self.source / '.local/jobs/j1/status.json').read_bytes(), original)
        self.assertEqual((self.report / 'original-status/j1/status.json').read_bytes(), original)
        self.assertEqual((self.report / 'new-defaults/handoff.json').read_bytes(), self.default)
        for name in ('assets.json', 'jobs/j1/input.json', 'jobs/j1/result.json', 'handoff.json'):
            self.assertEqual((self.source / '.local' / name).read_bytes(), (self.target / '.local' / name).read_bytes())
        copied = json.loads((self.target / '.local/jobs/j1/status.json').read_text())
        self.assertEqual(copied, dict(self.job, command=[str(self.target / 'runtime/bin/python3'), str(self.target / 'backend/execute.py')]))
        self.assertNotEqual(self.media.stat().st_ino, (self.target / '.local/imports/batch/files/原素材.mov').stat().st_ino)
        self.assertEqual((self.target / '.local/models/new-model').read_bytes(), b'new-model')

    def test_dead_active_job_becomes_interrupted_not_queued(self):
        self.store('jobs/j1/status.json', dict(self.job, kind='workflow', status='queued', workerPid=987654))
        with patch.object(upgrade, 'alive', return_value=False):
            self.run_upgrade()
        copied = json.loads((self.target / '.local/jobs/j1/status.json').read_text())
        self.assertEqual(copied['status'], 'interrupted')
        self.assertNotIn('workerPid', copied)
        self.assertTrue(copied['command'][1].endswith('/backend/workflow.py'))

    def test_live_worker_refused_before_copy(self):
        self.store('jobs/j1/status.json', dict(self.job, workerPid=123))
        with patch.object(upgrade, 'alive', return_value=True), self.assertRaisesRegex(ValueError, '存活任务'):
            self.run_upgrade()
        self.assertFalse(self.report.exists())

    def test_target_collision_never_overwritten(self):
        (self.target / '.local/assets.json').write_bytes(b'keep')
        with self.assertRaises(FileExistsError):
            self.run_upgrade()
        self.assertEqual((self.target / '.local/assets.json').read_bytes(), b'keep')

    def test_unknown_job_kind_is_refused(self):
        self.store('jobs/j1/status.json', dict(self.job, kind='unknown'))
        with self.assertRaisesRegex(ValueError, '未知任务'):
            self.run_upgrade()

    def test_symlinks_are_refused(self):
        (self.source / '.local/imports/escape').symlink_to(self.target)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            self.run_upgrade()

    def test_corrupt_copy_fails_before_commit(self):
        original_copy = self.copy
        def corrupt(source, target):
            result = original_copy(source, target)
            if source.name == 'assets.json':
                target.write_bytes(b'wrong')
            return result
        with patch.object(upgrade, 'copy_entry', side_effect=corrupt), self.assertRaisesRegex(ValueError, '副本校验'):
            upgrade.migrate(self.source, self.target, self.report, True)
        self.assertFalse((self.target / '.local/jobs').exists())
        self.assertEqual((self.target / '.local/handoff.json').read_bytes(), self.default)
        self.assertEqual(json.loads((self.report / 'migration.json').read_text())['phase'], 'failed-no-data-committed')

    def test_hardlinks_are_refused_even_when_bytes_match(self):
        def linked(source, target):
            if source.is_dir():
                shutil.copytree(source, target, copy_function=os.link)
            else:
                os.link(source, target)
            return 'invalid-hardlink'
        with patch.object(upgrade, 'copy_entry', side_effect=linked), self.assertRaisesRegex(ValueError, '硬链接'):
            upgrade.migrate(self.source, self.target, self.report, True)
        self.assertFalse((self.target / '.local/assets.json').exists())

    def test_missing_original_is_reported_not_hidden(self):
        self.store('assets.json', {'a': {'id': 'a', 'path': '/missing/original.mov'}})
        self.assertEqual(self.run_upgrade(False)['missingAssetIds'], ['a'])

    def test_mid_commit_failure_rolls_back_data_and_restores_defaults(self):
        self.store('vision-combo.json', {'enabled': True})
        vision_default = b'{"enabled": false}\n'
        (self.target / '.local/vision-combo.json').write_bytes(vision_default)
        original_replace = os.replace

        def fail_commit(source, destination):
            if (Path(source).parent.name.startswith('.upgrade-data-')
                    and Path(destination) == self.target / '.local/vision-combo.json'):
                raise OSError('simulated commit failure')
            return original_replace(source, destination)

        with patch.object(upgrade.os, 'replace', side_effect=fail_commit):
            with self.assertRaisesRegex(OSError, 'simulated commit failure'):
                self.run_upgrade()
        self.assertFalse((self.target / '.local/assets.json').exists())
        self.assertFalse((self.target / '.local/jobs').exists())
        self.assertFalse((self.target / '.local/imports').exists())
        self.assertEqual((self.target / '.local/handoff.json').read_bytes(), self.default)
        self.assertEqual((self.target / '.local/vision-combo.json').read_bytes(), vision_default)
        report = json.loads((self.report / 'migration.json').read_text())
        self.assertEqual(report['phase'], 'failed-no-data-committed')
        self.assertTrue((Path(report['staging']) / 'jobs/j1/status.json').is_file())
        self.assertEqual(json.loads((self.source / '.local/jobs/j1/status.json').read_text()), self.job)


if __name__ == '__main__':
    unittest.main()
