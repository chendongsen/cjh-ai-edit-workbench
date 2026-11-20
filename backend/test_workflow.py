import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from common import read, write
from workflow import automatic_plan, run
from planner import build
from analyze import run as analyze_run


def item(name='take2.mov', kind='人物', identifier='a'):
    return {'source': name, 'assetId': identifier, 'video': True, 'duration': 10, 'usableDuration': 10,
            'vision': {'kind': kind, 'dishCandidates': ['虾饺'] if kind == '食物' else []},
            'segments': [{'start': 1, 'end': 5, 'text': '这个虾饺很好吃'}, {'start': 7, 'end': 9.95, 'text': '第二段'}]}


class AutomaticPlanTests(unittest.TestCase):
    def test_analysis_resume_uses_only_unchanged_completed_item(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            media = root / 'media.bin'
            media.write_bytes(b'test')
            request = {'assets': [{'id': 'one', 'path': str(media), 'name': media.name}],
                       'resume': True, 'model': 'small', 'transcribe': False}
            with patch('analyze.probe', return_value={'video': False, 'audio': False}) as probe:
                analyze_run(request, root)
                self.assertEqual(probe.call_count, 1)
                analyze_run(request, root)
                self.assertEqual(probe.call_count, 1)
                request['model'] = 'base'
                analyze_run(request, root)
                self.assertEqual(probe.call_count, 2)
                media.write_bytes(b'changed')
                analyze_run(request, root)
                self.assertEqual(probe.call_count, 3)

    def test_natural_order_and_unknown_role_preserved(self):
        plan = automatic_plan([item('take10.mov'), item('take2.mov')], 'test')
        self.assertEqual(plan['selectedSpeech'][0]['source'], 'take2.mov')
        self.assertTrue(all(s['role'] == '待确认' for s in plan['selectedSpeech']))
        self.assertTrue(all(s['end'] <= 10 for s in plan['selectedSpeech']))
        self.assertEqual(plan['packaging'], [])

    def test_exact_food_match_and_no_reuse(self):
        plan = automatic_plan([item(), item('food.mov', '食物', 'food')], 'test')
        self.assertEqual(len(plan['foodPlacements']), 1)
        food = plan['foodPlacements'][0]
        self.assertFalse(food['confirmed'])
        self.assertLessEqual(food['at'] + food['end'] - food['start'], plan['duration'])

    def test_mismatch_not_forced(self):
        food = item('food.mov', '食物', 'food')
        food['vision']['dishCandidates'] = ['烧鹅']
        self.assertEqual(automatic_plan([item(), food], 'test')['foodPlacements'], [])

    def test_filename_hint_recovers_accent_mismatch(self):
        person = item('/batch/4/吃虾饺.mov')
        person['segments'][0]['text'] = '这个下脚很好吃'
        food = item('/batch/4/菜品/01.mov', '食物', 'food')
        plan = automatic_plan([person, food], 'test')
        self.assertEqual(len(plan['foodPlacements']), 1)
        self.assertIn('文件名', plan['foodPlacements'][0]['matchBasis'])

    def test_other_restaurant_not_mixed(self):
        person = item('/batch/4/人物.mov')
        food = item('/batch/5/菜品/01.mov', '食物', 'food')
        self.assertEqual(automatic_plan([person, food], 'test')['foodPlacements'], [])

    def test_no_human_candidate_fails(self):
        with self.assertRaisesRegex(ValueError, '没有找到'):
            automatic_plan([item(kind='环境')], 'test')

    def test_short_gaps_merge_but_long_gaps_trim(self):
        data = item()
        data['segments'][1]['start'] = 5.5
        self.assertEqual(len(automatic_plan([data], 'test')['selectedSpeech']), 1)
        data['segments'][1]['start'] = 7
        self.assertEqual(len(automatic_plan([data], 'test')['selectedSpeech']), 2)

    def test_phone_container_padding_does_not_exceed_media_stream(self):
        data = item()
        data['usableDuration'] = 9.86
        plan = automatic_plan([data], 'test')
        self.assertLessEqual(plan['selectedSpeech'][-1]['end'], 9.86)
        self.assertAlmostEqual(plan['selectedSpeech'][-1]['end'] * 30, round(9.86 * 30 - .5))

    def test_manual_gate_not_weakened_by_auto_mode(self):
        with tempfile.TemporaryDirectory() as folder:
            plan = automatic_plan([item()], 'test')
            with patch('planner.asset_for', return_value={'path': 'x', 'name': 'x'}), patch('planner.probe', return_value={'duration': 10, 'video': True}):
                with self.assertRaisesRegex(ValueError, '未确认角色'):
                    build({'plan': plan}, {}, Path(folder), 'test')
                result = build({'plan': plan, 'automaticDraft': True}, {}, Path(folder), 'test')
                self.assertTrue(any('自动初剪' in w for w in result['warnings']))

    def test_analysis_failure_stops_all_editor_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder)/'input.json'
            write(source, {'assets': []})
            def failed_analysis(request, output):
                write(output/'result.json', {'items': [{'source': 'x', 'error': 'bad'}]})
            with patch('workflow.analyze', side_effect=failed_analysis), patch('workflow.subprocess.run') as command, patch('workflow.jianying.run') as export:
                with self.assertRaisesRegex(ValueError, '已停止下游'):
                    run(source)
                command.assert_not_called()
                export.assert_not_called()

    def test_indeterminate_resolve_write_not_repeated(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write(root/'input.json', {'assets': []})
            (root/'resolve').mkdir()
            write(root/'resolve/started.json', {'started': True})
            def analysis(request, output):
                write(output/'result.json', {'items': [item()]})
            with patch('workflow.analyze', side_effect=analysis), patch('workflow.subprocess.run') as command:
                with self.assertRaisesRegex(ValueError, '不能盲目重试'):
                    run(root/'input.json')
                command.assert_not_called()

    def test_success_receipts_resume_without_duplicate_editor_writes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            write(root/'input.json', {'assets': []})
            for name in ('resolve', 'jianying'):
                (root/name).mkdir()
                write(root/name/'result.json', {'opened': True, 'warnings': []})
            def analysis(request, output):
                write(output/'result.json', {'items': [item()]})
            with patch('workflow.analyze', side_effect=analysis), patch('workflow.subprocess.run') as command, patch('workflow.jianying.run') as export:
                run(root/'input.json')
                command.assert_not_called()
                export.assert_not_called()
                self.assertEqual(read(root/'progress.json')['stage'], 'done')
                self.assertEqual(read(root/'result.json')['speechClips'], 2)


if __name__ == '__main__':
    unittest.main()
