import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from rebuild_coverage_report import rebuild_job_reports
from script_coverage import build_coverage


class RebuildCoverageReportTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        shot = {'id': 'shot-17', 'text': '放一些爬坡的片段做加速', 'sourceLine': 6,
                'scene': '坡道', 'sourceColumn': '文案动作'}
        self.write('status.json', {'status': 'succeeded', 'id': 'test-job', 'message': '执行完成',
                                  'result': {'old': True}})
        self.write('script-preparation.json', {'targets': [{'text': '没有匹配的台词'}],
                                              'shotRequirements': [shot]})
        self.write('script-shots-report.json', {'requirements': [dict(shot, status='covered-main',
            reason='画面匹配', evidence={'frames': [0, 1, 2]}, placement={'track': 1, 'at': 2})],
            'summary': {'newInsertCount': 0}})
        self.write('refined-items.json', {'items': []})
        self.write('auto-plan.json', {'selectedSpeech': [], 'foodPlacements': [], 'duration': 20,
            'scriptCoverage': {'old': True}, 'other': {'untouched': True}})
        self.write('review-report.json', {'review': [{'text': '保留旧审片标记'}], 'scriptCoverage': {},
            'warnings': ['原有警告', '0 项脚本镜头未解决；旧消息']})
        self.write('result.json', {'duration': 20, 'brollClips': 0, 'scriptShotInserted': 0,
            'scriptShotUnresolved': 0, 'missingScriptCount': 0, 'scriptCoverage': {},
            'resolve': {'projectId': 'keep'}, 'draft': {'draftPath': '/not-touched'},
            'warnings': ['原有警告', '0 项脚本镜头未解决；旧消息']})
        self.write('script-coverage.json', {'old': True})
        (self.root / '脚本覆盖与缺项清单.txt').write_text('旧报告', encoding='utf-8')
        for name in ('resolve/result.json', 'jianying/result.json', 'input.json'):
            self.write(name, {'doNotChange': name})

    def write(self, name, value):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value, ensure_ascii=False), encoding='utf-8')

    def read(self, name):
        return json.loads((self.root / name).read_text(encoding='utf-8'))

    def snapshot(self):
        return {str(path.relative_to(self.root)): path.read_bytes()
                for path in self.root.rglob('*') if path.is_file()}

    def test_default_preview_is_read_only(self):
        before = self.snapshot()
        result = rebuild_job_reports(self.root)
        self.assertFalse(result['applied'])
        self.assertEqual(result['shots']['coveredMain'], 0)
        self.assertEqual(result['shots']['unresolved'], 1)
        self.assertEqual(result['dialogue']['missing'], 1)
        self.assertEqual(self.snapshot(), before)

    def test_apply_updates_all_report_copies_and_only_report_metadata(self):
        before = self.snapshot()
        old_plan = self.read('auto-plan.json')
        old_result = self.read('result.json')
        update = rebuild_job_reports(self.root, apply=True)
        coverage = self.read('script-coverage.json')
        for name in ('auto-plan.json', 'review-report.json'):
            self.assertEqual(self.read(name)['scriptCoverage'], coverage)
        result = self.read('result.json')
        self.assertEqual(self.read('status.json')['result'], result)
        self.assertEqual(result['scriptShotUnresolved'], coverage['shots']['unresolved'])
        self.assertEqual(result['scriptShotInserted'], coverage['shots']['inserted'])
        self.assertEqual(result['missingScriptCount'], 1)
        self.assertEqual(result['scriptCoverage']['shots']['physicalInserts'], 0)
        self.assertEqual(result['resolve'], old_result['resolve'])
        self.assertEqual(result['draft'], old_result['draft'])
        plan = self.read('auto-plan.json')
        self.assertEqual({k: v for k, v in plan.items() if k != 'scriptCoverage'},
                         {k: v for k, v in old_plan.items() if k != 'scriptCoverage'})
        self.assertEqual(self.read('review-report.json')['review'], [{'text': '保留旧审片标记'}])
        self.assertIn('待完成／核验后期：加速', (self.root / '脚本覆盖与缺项清单.txt').read_text())
        for name in ('script-preparation.json', 'script-shots-report.json', 'refined-items.json',
                     'resolve/result.json', 'jianying/result.json', 'input.json'):
            self.assertEqual((self.root / name).read_bytes(), before[name])
        backup = Path(update['backup'])
        self.assertTrue(backup.is_dir())
        for name in update['files']:
            self.assertEqual((backup / name).read_bytes(), before[name])
        self.assertFalse(update['nleChanged'])
        self.assertTrue(update['serverReloadRequiredAfterApply'])

    def test_running_job_and_live_worker_are_refused(self):
        for status in ({'status': 'running'}, {'status': 'succeeded', 'workerPid': os.getpid()}):
            with self.subTest(status=status):
                self.write('status.json', status)
                before = self.snapshot()
                with self.assertRaises(ValueError):
                    rebuild_job_reports(self.root, apply=True)
                self.assertEqual(self.snapshot(), before)

    def test_missing_original_asr_refuses_fallback(self):
        self.write('refined-items.json', {'summary': 'not original ASR'})
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, '缺少原始'):
            rebuild_job_reports(self.root, apply=True)
        self.assertEqual(self.snapshot(), before)

    def test_repeat_apply_does_not_accumulate_warnings_or_obligations(self):
        first = rebuild_job_reports(self.root, apply=True)
        expected = {name: (self.root / name).read_bytes() for name in first['files']}
        rebuild_job_reports(self.root, apply=True)
        self.assertEqual(expected, {name: (self.root / name).read_bytes() for name in first['files']})

    def test_changed_input_rejects_write_before_backup(self):
        before = self.snapshot()
        def changed(*args, **kwargs):
            self.write('script-preparation.json', {'changedByAnotherProcess': True})
            return build_coverage(*args, **kwargs)
        with patch('rebuild_coverage_report.build_coverage', side_effect=changed):
            with self.assertRaisesRegex(ValueError, '变化'):
                rebuild_job_reports(self.root, apply=True)
        after = self.snapshot()
        self.assertNotEqual(before.pop('script-preparation.json'), after.pop('script-preparation.json'))
        self.assertEqual(before, after)

    def write_manual(self, **changes):
        review={'requirementId':'shot-17','status':'uncertain','reason':'未确认助理身份',
                'source':'13.mov 的三张抽帧','reviewer':'Codex 抽帧复核'}
        review.update(changes)
        self.write('manual-shot-review.json',{'revision':'manual-shot-review-1','reviews':[review]})
        return self.root/'manual-shot-review.json'

    def test_explicit_sidecar_updates_reports_and_preserves_source(self):
        sidecar=self.write_manual()
        before=sidecar.read_bytes()
        preview=rebuild_job_reports(self.root,manual_review=sidecar)
        self.assertFalse(preview['applied'])
        self.assertEqual(preview['manualReviewCount'],1)
        update=rebuild_job_reports(self.root,apply=True,manual_review=sidecar)
        report=self.read('script-coverage.json')
        row=report['shots']['items'][0]
        self.assertEqual(row['status'],'uncertain')
        self.assertEqual(row['visualCoverage']['status'],'covered-main')
        self.assertEqual(report['manualReviewSource']['path'],str(sidecar.resolve()))
        self.assertEqual(len(report['manualReviewSource']['sha256']),64)
        self.assertEqual(sidecar.read_bytes(),before)
        self.assertEqual(self.read('result.json')['scriptCoverage']['manualReviewSource'],report['manualReviewSource'])
        self.assertTrue(update['applied'])

    def test_invalid_sidecar_is_rejected_without_writing_or_backing_up(self):
        for changes in ({'status':'covered-main'},{'requirementId':'not-in-script'},
                        {'status':'inserted'},{'start':0}):
            with self.subTest(changes=changes):
                sidecar=self.write_manual(**changes)
                before=self.snapshot()
                with self.assertRaises(ValueError):
                    rebuild_job_reports(self.root,apply=True,manual_review=sidecar)
                self.assertEqual(self.snapshot(),before)

    def test_subsequent_rebuild_cannot_silently_drop_prior_downgrade(self):
        sidecar=self.write_manual()
        rebuild_job_reports(self.root,apply=True,manual_review=sidecar)
        first=self.read('script-coverage.json')
        # Omitting the CLI option retains the stored review, not the raw model's badge.
        rebuild_job_reports(self.root,apply=True)
        self.assertEqual(self.read('script-coverage.json'),first)
        # An explicit replacement file cannot omit a prior downgrade either.
        self.write('manual-shot-review.json',{'revision':'manual-shot-review-1','reviews':[]})
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'遗漏此前降级项'):
            rebuild_job_reports(self.root,apply=True,manual_review=sidecar)
        self.assertEqual(self.snapshot(),before)


if __name__ == '__main__':
    unittest.main()
