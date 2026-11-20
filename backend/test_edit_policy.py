"""Synthetic regressions for editorial degree; never invokes models or editors."""
import copy
import json
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from edit_policy import policy, cap_visual_extension
from night_edit import merge, select
from reaction_review import safe_extension
import reaction_review
from voice_guard import guarded_speech


MODES = ('script', 'strict', 'balanced', 'expressive')
FPS = 30


def fixture(text='这个虾饺真好吃', role='talent', protected=None):
    return {
        'assetId': 'a', 'source': '/synthetic/a.mov', 'video': True, 'audio': True,
        'duration': 20, 'usableDuration': 20, 'vision': {'kind': '人物'},
        'segments': [{'start': 2, 'end': 4, 'text': text, 'words': []}],
        'voiceGuard': {'windows': [{'start': 0, 'end': 20, 'role': role}],
                       'protected': protected or []},
    }


def length(rows):
    return sum(row['end'] - row['start'] for row in rows)


class EditPolicyTests(unittest.TestCase):
    def test_missing_mode_preserves_legacy_expressive(self):
        self.assertEqual(policy(), policy('expressive'))
        item = fixture()
        self.assertEqual(guarded_speech(item, 20, FPS),
                         guarded_speech(item, 20, FPS, edit_mode='expressive'))

    def test_legacy_modes_share_handles_without_trimming_speech(self):
        sizes = []
        for mode in MODES:
            with self.subTest(mode=mode):
                kept, cuts, _ = guarded_speech(fixture(), 20, FPS, edit_mode=mode)
                self.assertFalse(cuts)
                self.assertEqual(len(kept), 1)
                row = kept[0]
                self.assertEqual((row['speechStart'], row['speechEnd']), (2, 4))
                self.assertLessEqual(row['start'], 2)
                self.assertGreaterEqual(row['end'], 4)
                sizes.append(length(kept))
        self.assertEqual(len(set(sizes)), 1)

    def test_visual_extension_uses_one_bounded_policy(self):
        row = {'start': 2, 'end': 4, 'speechStart': 2.1, 'speechEnd': 3.8}
        limits = {mode: (.25, .8) for mode in MODES}
        spans = []
        for mode, (before, after) in limits.items():
            with self.subTest(mode=mode):
                start, end = cap_visual_extension(row, (-99, 99), policy(mode), FPS)
                self.assertGreaterEqual(start, row['start'] - before - 1/FPS)
                self.assertLessEqual(end, row['end'] + after + 1/FPS)
                self.assertLessEqual(start, row['start'])
                self.assertGreaterEqual(end, row['end'])
                spans.append(end-start)
        self.assertEqual(len(set(spans)), 1)

    def test_visual_policy_never_shrinks_selected_reaction(self):
        row = {'start': 1, 'end': 12, 'speechStart': 2, 'speechEnd': 3,
               'protected': True, 'text': '哈哈'}
        original = copy.deepcopy(row)
        for mode in MODES:
            with self.subTest(mode=mode):
                self.assertEqual(tuple(cap_visual_extension(row, (4, 5), policy(mode), FPS)),
                                 (1, 12))
                self.assertEqual(row, original)

    def test_bite_settle_limit_is_visible_in_review(self):
        def observed(board, starts, ends, model, old):
            return {
                'startIndex': min(range(len(starts)), key=lambda i: abs(starts[i]-old[0])),
                'endIndex': len(ends)-1, 'startContinuous': False,
                'endContinuous': True, 'confidence': .9, 'observation': '合成：咬下动作仍持续',
                'frameActions': [{'index': i, 'action': 'bite', 'confidence': .9}
                                 for i in range(len(starts)+len(ends))],
            }
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'synthetic.mov'
            source.write_bytes(b'synthetic placeholder; models are mocked')
            item = fixture()
            item['source'] = str(source)
            item['segments'] = []
            row = {'assetId': 'a', 'source': str(source), 'start': 2, 'end': 4,
                   'speechStart': 2.1, 'speechEnd': 3.8, 'text': '好吃', 'words': []}
            with patch.object(reaction_review, 'DATA', root), patch.object(
                    reaction_review, 'make_board', return_value=(root/'board.jpg', [])), patch.object(
                    reaction_review, 'infer', side_effect=observed):
                rows, report = reaction_review.review_boundaries([row], [item],
                    {'editMode': 'strict', 'visionModel': 'synthetic-only', 'fps': FPS}, root)
            self.assertEqual(report['policyLimitedClips'], 1)
            self.assertTrue(rows[0].get('policyReview'))
            self.assertLessEqual(rows[0]['end'], row['end']+.8+1/FPS)

    def test_safe_extension_rounding_stays_within_one_frame_of_policy(self):
        for mode in MODES:
            settings = policy(mode)
            for fps in (24, 25, 30, 60):
                for start, end in ((2, 4), (2.019, 4.013), (2.1, 4.7)):
                    with self.subTest(mode=mode, fps=fps, start=start):
                        row = {'start': start, 'end': end, 'speechStart': start+.2,
                               'speechEnd': end-.2}
                        capped = cap_visual_extension(row, (0, 20), settings, fps)
                        result = safe_extension(row, capped, {'duration': 20, 'segments': []}, fps)
                        self.assertGreaterEqual(result[0], start-settings['visualBefore']-1/fps-1e-8)
                        self.assertLessEqual(result[1], end+settings['visualAfter']+1/fps+1e-8)
                        self.assertLessEqual(result[0], start)
                        self.assertGreaterEqual(result[1], end)

    def test_whole_selected_sentence_not_hard_capped(self):
        item = fixture()
        item['segments'][0]['end'] = 12
        for mode in MODES:
            with self.subTest(mode=mode):
                selected, _, _, missing, _ = select([item], '达人：这个虾饺真好吃', FPS, edit_mode=mode)
                self.assertFalse(missing)
                self.assertEqual(len(selected), 1)
                self.assertLessEqual(selected[0]['start'], 2)
                self.assertGreaterEqual(selected[0]['end'], 12)

    def test_short_adjacent_reaction_restored_in_full(self):
        item = fixture()
        item['segments'].append({'start': 4.3, 'end': 5.1, 'text': '哈哈', 'words': []})
        for mode in MODES:
            with self.subTest(mode=mode):
                selected, _, _, missing, _ = select([item], '达人：这个虾饺真好吃', FPS, edit_mode=mode)
                self.assertFalse(missing)
                self.assertIn('哈哈', ''.join(row['text'] for row in selected))
                self.assertTrue(any(row['start'] <= 4.3 and row['end'] >= 5.1 for row in selected))

    def test_strict_requires_parseable_script(self):
        for script in ('', '注意保留人物反应。画面以食物特写为主。'):
            with self.subTest(script=script), self.assertRaises(ValueError):
                select([fixture()], script, FPS, edit_mode='strict')

    def test_strict_zero_match_cannot_silently_keep_all(self):
        with self.assertRaises(ValueError):
            select([fixture('这一串烤羊肉又香又嫩')], '达人：今天来选购秋季外套', FPS, edit_mode='strict')

    def test_every_mode_requires_script_and_never_falls_back_to_all_speech(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                for script in ('', '达人：今天来选购秋季外套'):
                    with self.assertRaises(ValueError):
                        select([fixture()], script, FPS, edit_mode=mode)

    def test_scripted_exchange_preserves_delayed_friend_reply_and_handoff(self):
        item = fixture('好多榴莲酱', 'unknown')
        item['segments'] += [
            {'start': 5, 'end': 7, 'text': '拿包纸吧', 'words': []},
            {'start': 12, 'end': 14, 'text': '自带纸巾给你', 'words': []},
        ]
        script = 'C1｜角色意图：达人闺蜜互动｜台词意图：好多榴莲酱，拿包纸吧，自带纸巾给你\n素材：a.mov'
        strict, _, _, strict_missing, _ = select([item], script, FPS, edit_mode='strict')
        expressive, _, _, expressive_missing, _ = select([item], script, FPS, edit_mode='expressive')
        self.assertFalse(strict_missing)
        self.assertFalse(expressive_missing)
        self.assertIn('自带纸巾给你', ''.join(row['text'] for row in strict))
        self.assertEqual(len(strict), 1)
        self.assertEqual(strict, expressive)
        self.assertGreaterEqual(strict[0]['end'], 14)

    def test_merge_honors_row_gap_limit_instead_of_legacy_eight_seconds(self):
        first = {'assetId': 'a', 'start': 0, 'end': 3, 'speechEnd': 2.5,
                 'text': '借我张纸', 'words': [], 'dialogueProtected': True,
                 'scriptTarget': 1, 'dialogueGapLimit': .35}
        second = dict(first, start=6, end=8, speechEnd=7.5, text='给你')
        self.assertEqual(len(merge([first, second])), 2)
        self.assertEqual(len(merge([dict(first, dialogueGapLimit=8),
                                    dict(second, dialogueGapLimit=8)])), 1)

    def test_merge_retains_policy_review_from_later_segment(self):
        first = {'assetId': 'a', 'start': 0, 'end': 3, 'speechEnd': 2.5,
                 'text': '借我张纸', 'words': []}
        second = dict(first, start=2, end=5, speechEnd=4.5, text='给你',
                      policyReview='后段额外留量受到所选档位限制')
        merged = merge([first, second])
        self.assertEqual(len(merged), 1)
        self.assertIn(second['policyReview'], str(merged[0].get('policyReview', '')))

    def test_plain_handle_does_not_import_next_utterance(self):
        item = fixture()
        item['segments'].append({'start': 4.1, 'end': 6, 'text': '另一句并未入选', 'words': []})
        for mode in MODES:
            with self.subTest(mode=mode):
                kept, _, _ = guarded_speech(item, 20, FPS, edit_mode=mode)
                self.assertLessEqual(kept[0]['end'], 4.1 + 1e-6)
                self.assertGreaterEqual(kept[0]['end'], kept[0]['speechEnd'])

    def test_all_modes_preserve_uncertain_friend_and_talent_voices(self):
        for mode in MODES:
            for role in ('unknown', 'mixed', 'friend', 'talent'):
                with self.subTest(mode=mode, role=role):
                    kept, cuts, _ = guarded_speech(fixture('你可以这样说', role), 20, FPS, edit_mode=mode)
                    self.assertFalse(cuts)
                    self.assertEqual(len(kept), 1)

    def test_all_modes_protect_laugh_and_director_scene_dialogue(self):
        for mode in MODES:
            for item in (fixture('这是免税不是免费', 'director'),
                         fixture('你可以这样说', 'director', [[1, 6, 'laughter']])):
                with self.subTest(mode=mode, text=item['segments'][0]['text']):
                    kept, cuts, _ = guarded_speech(item, 20, FPS, edit_mode=mode)
                    self.assertFalse(cuts)
                    self.assertTrue(kept)
                    self.assertGreaterEqual(kept[0]['end'], kept[0]['speechEnd'])

    def test_confirmed_safe_instruction_still_removed_all_modes(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                kept, cuts, _ = guarded_speech(fixture('你可以这样说', 'director'), 20, FPS, edit_mode=mode)
                self.assertFalse(kept)
                self.assertEqual(len(cuts), 1)


class EditModeSubmissionTests(unittest.TestCase):
    """Exercise submit(), with every external capability replaced by fixtures."""

    def setUp(self):
        import server
        self.server = server
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        (self.root/'jobs').mkdir()
        resolve = self.root/'ResolvePython'
        resolve.write_bytes(b'synthetic executable placeholder; never executed')
        media = self.root/'source.mov'
        media.write_bytes(b'synthetic media placeholder; never decoded')
        asset = {'id': 'a', 'path': str(media), 'name': media.name,
                 'size': media.stat().st_size, 'modified': int(media.stat().st_mtime*1000)}
        caps = {'resolve': {'installed': True},
                'jianying': {'available': True, 'app': True, 'draftRootExists': True},
                'localModels': ['qwen3-vl:2b-instruct'], 'modelCache': ['small']}
        self.jobs = {}
        for name, value in (('DATA', self.root), ('JOBS', self.jobs),
                            ('ASSET_MAP', {'a': asset}), ('RESOLVE', str(resolve))):
            self.stack.enter_context(patch.object(server, name, value))
        self.caps = self.stack.enter_context(patch.object(server, 'capabilities', return_value=caps))
        self.stack.enter_context(patch.object(server.voice_guard, 'choose_profile', return_value=None))
        self.prepare = self.stack.enter_context(patch.object(server.editor_startup, 'prepare'))
        self.spawn = self.stack.enter_context(patch.object(server.subprocess, 'Popen',
            side_effect=AssertionError('Submission must not start an editor or model')))
        self.request = {'kind': 'workflow', 'projectId': 'synthetic-submission',
                        'assetIds': ['a'], 'confirmed': True, 'unattended': True,
                        'editingScript': '达人：这个虾饺真好吃', 'model': 'small',
                        'visionModel': 'qwen3-vl:2b-instruct', 'fps': FPS}

    def tearDown(self):
        self.prepare.assert_not_called()
        self.spawn.assert_not_called()

    def test_invalid_mode_rejected_before_creating_job(self):
        for invalid in ('aggressive', '', 0, False, [], {}):
            with self.subTest(mode=invalid), self.assertRaises(ValueError):
                self.server.submit(dict(self.request, editMode=invalid))
            self.assertEqual(self.jobs, {})
            self.assertEqual(list((self.root/'jobs').iterdir()), [])
        self.caps.assert_not_called()

    def test_strict_unparseable_script_rejected_before_creating_job(self):
        for script in ('', '拍摄说明：请多拍菜品和餐厅环境。'):
            with self.subTest(script=script), self.assertRaises(ValueError):
                self.server.submit(dict(self.request, editMode='strict', editingScript=script))
            self.assertEqual(self.jobs, {})
            self.assertEqual(list((self.root/'jobs').iterdir()), [])
        self.caps.assert_not_called()

    def test_selected_mode_persisted_in_input_status_and_receipt(self):
        for mode in MODES:
            with self.subTest(mode=mode):
                response = self.server.submit(dict(self.request, editMode=mode, projectId='synthetic-'+mode))
                folder = self.root/'jobs'/response['id']
                saved_input = json.loads((folder/'input.json').read_text())
                saved_status = json.loads((folder/'status.json').read_text())
                self.assertEqual(saved_input['editMode'], 'script')
                self.assertEqual(saved_status['editMode'], 'script')
                self.assertEqual(response['editMode'], 'script')
                self.assertEqual(response['status'], 'queued')
                self.assertEqual(self.jobs[response['id']]['editMode'], 'script')
        self.assertEqual(len(self.jobs), len(MODES))

    def test_new_submission_without_mode_defaults_script(self):
        self.assertNotIn('editMode', self.request)
        response = self.server.submit(self.request)
        folder = self.root/'jobs'/response['id']
        self.assertEqual(response['editMode'], 'script')
        self.assertEqual(json.loads((folder/'input.json').read_text())['editMode'], 'script')
        self.assertEqual(json.loads((folder/'status.json').read_text())['editMode'], 'script')
        # Re-running legacy requests uses the single policy; stored history is untouched.
        self.assertEqual(policy(None)['mode'], 'script')


if __name__ == '__main__':
    unittest.main()
