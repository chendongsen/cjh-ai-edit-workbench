import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import script_shots as shots


def item(identifier='main', summary='人在讲解', duration=20):
    return {'assetId':identifier, 'source':'/batch/'+identifier+'.mov', 'duration':duration,
            'usableDuration':duration, 'video':True, 'vision':{'kind':'其他', 'summary':summary},
            'frames':[{'at':0, 'file':identifier+'.jpg'}, {'at':8, 'file':identifier+'8.jpg'}],
            'voiceGuard':{'protected':[]}}


def clip(identifier='main', at=0, start=1, end=12, target=1):
    return {'assetId':identifier, 'source':'/batch/'+identifier+'.mov', 'start':start, 'end':end,
            'at':at, 'speechStart':start, 'speechEnd':end-1, 'text':'普通讲解内容',
            'voiceRole':'unknown', 'scriptTarget':target, 'scriptTargetIds':[target]}


def prep(text='外骨骼腿部电机运转特写'):
    return {'targets':[{'text':'这就是助力设备', 'scene':'上山'}], 'shotRequirements':[
        {'id':'shot-1', 'text':text, 'scene':'上山', 'sourceLine':3, 'sourceColumn':'画面', 'targetIds':[1]}]}


def frame_result(label, confidence=.95):
    return {'frames':[{'index':n, 'label':label, 'confidence':confidence, 'description':'mock'} for n in range(3)]}


def requirement_result(label, criteria, confidence=.95, sequence_required=False):
    result = frame_result('usable' if label == 'partial' else label, confidence)
    status = {'usable':'visible', 'partial':'partial', 'reject':'absent', 'unclear':'unclear'}[label]
    result['criteria'] = [{'index':i, 'status':status, 'confidence':confidence,
                           'frameIndices':[0,1,2] if status in ('visible','partial') else [], 'description':'mock'} for i in range(len(criteria))]
    result['sequence'] = {'status':'verified' if sequence_required else 'not-required', 'confidence':confidence,
                           'frameIndices':[0,2] if sequence_required else []}
    result['evidence'] = [{'at':i, 'file':f'mock/{i}.jpg'} for i in range(3)]
    return result


def counterevidence_result(atoms, status='pass', people=2, confidence=.95):
    evidence = {'leading':'两个人一前一后行走，一人在引导另一人', 'dressing':'另一人正在帮助穿戴并固定设备',
                'cutting':'刀具接触瓜并切割', 'handoff':'物品从一人递给另一人', 'carrying-moving':'人物肩扛物品迈步行走',
                'entrance':'清楚可见实体门洞与入口标识', 'ground':'多个西瓜放在地面上',
                'worn-mechanism':'腿部机械外骨骼支架清楚可见', 'closeup':'对应主体局部细节特写'}
    return {'observations':[{'index':i, 'peopleCount':people, 'description':'实际画面证据'} for i in range(3)],
            'checks':[{'index':i, 'status':status, 'confidence':confidence, 'frameIndices':[0,1,2],
                       'observedEvidence':evidence.get(a['kind'], '实际动作和构图证据'),
                       'missingEvidence':'' if status == 'pass' else '缺少要求'} for i,a in enumerate(atoms)]}


class ScriptShotsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.output = Path(self.temp.name)
        self.main, self.insert = item(), item('23', '外骨骼腿部电机运转特写')
        self.plan = {'selectedSpeech':[clip()], 'foodPlacements':[], 'duration':11}
        self.calls = []
        self.main_label, self.candidate_label, self.cover_label = 'reject', 'usable', 'talk'
        self.create = patch.object(shots.vision_combo, 'create', return_value=(None, {'status':'test'}))
        self.create.start()
        self.infer = patch.object(shots.broll, 'infer_frames', side_effect=self.fake_infer)
        self.infer.start()
        self.requirement_infer = patch.object(shots, '_infer_requirement', side_effect=self.fake_requirement)
        self.requirement_infer.start()
        self.frame_paths = patch.object(shots, '_requirement_frame_paths', return_value=[shots.DATA/'mock'/f'{i}.jpg' for i in range(3)])
        self.frame_paths.start()
        self.counter_infer = patch.object(shots, '_infer_counterevidence', side_effect=lambda files,model,atoms: counterevidence_result(atoms))
        self.counter_infer.start()

    def tearDown(self):
        self.infer.stop()
        self.requirement_infer.stop()
        self.frame_paths.stop()
        self.counter_infer.stop()
        self.create.stop()
        self.temp.cleanup()

    def fake_infer(self, source, times, model, prompt, choices, cache):
        self.calls.append({'source':source, 'times':times, 'prompt':prompt, 'choices':choices})
        if choices == ['talk', 'reaction', 'unclear']:
            return frame_result(self.cover_label)
        return frame_result(self.main_label if source.endswith('main.mov') else self.candidate_label)

    def fake_requirement(self, source, times, model, prompt, criteria, sequence_required):
        self.calls.append({'source':source, 'times':times, 'prompt':prompt, 'criteria':criteria})
        label = self.candidate_label if source.endswith('/23.mov') else self.main_label
        return requirement_result(label, criteria, sequence_required=sequence_required)

    def run_plan(self, preparation=None, plan=None, items=None, **kwargs):
        return shots.apply_script_shots(plan or self.plan, items or [self.main, self.insert],
            preparation or prep(), self.output, kwargs.pop('model', 'qwen-local'), log=lambda x:None, **kwargs)

    def test_general_product_shot_numeric_name_no_food_label_and_keeps_main(self):
        before = copy.deepcopy(self.plan)
        result, report = self.run_plan()
        self.assertEqual(result['selectedSpeech'], before['selectedSpeech'])
        self.assertEqual(self.plan, before)
        self.assertEqual(report['requirements'][0]['status'], 'inserted')
        placement = result['foodPlacements'][0]
        self.assertEqual(placement['assetId'], '23')
        self.assertEqual(placement['brollKind'], 'script')
        self.assertTrue(placement['audioMuted'])
        self.assertFalse(placement['confirmed'])
        self.assertEqual(report['summary'], {'required':1, 'coveredMain':0, 'inserted':1, 'newInsertCount':1, 'unresolved':0})
        self.assertTrue((self.output/'script-shots-report.json').exists())

    def test_existing_main_requires_visual_not_summary(self):
        self.main['vision']['summary'] = prep()['shotRequirements'][0]['text']
        self.main_label = 'usable'
        result, report = self.run_plan()
        self.assertEqual(report['requirements'][0]['status'], 'covered-main')
        self.assertFalse(result['foodPlacements'])
        self.assertTrue(self.calls)
        for call in self.calls:
            self.assertTrue(all(1 <= t < 12 for t in call['times']))

    def test_main_hidden_by_existing_v2_cannot_count_as_visible(self):
        self.main_label = 'usable'
        self.plan['foodPlacements'] = [{'assetId':'23', 'start':0, 'end':11, 'at':0}]
        result, report = self.run_plan()
        self.assertEqual(report['requirements'][0]['status'], 'inserted')
        self.assertTrue(report['requirements'][0]['placement']['existing'])
        self.assertFalse(any(c['source'].endswith('main.mov') for c in self.calls))
        self.assertEqual(len(result['foodPlacements']), 1)
        self.assertEqual(report['summary']['newInsertCount'], 0)

    def test_partial_composite_not_counted(self):
        self.candidate_label = 'partial'
        result, report = self.run_plan(prep('走路并停下转身，特写电机启动'))
        self.assertEqual(report['requirements'][0]['status'], 'partial')
        self.assertEqual(report['summary']['unresolved'], 1)
        self.assertFalse(result['foodPlacements'])

    def test_unclear_never_forced(self):
        self.candidate_label = 'unclear'
        result, report = self.run_plan()
        self.assertEqual(report['requirements'][0]['status'], 'uncertain')
        self.assertFalse(result['foodPlacements'])

    def test_face_reaction_or_unavailable_window_reports_conflict(self):
        self.cover_label = 'reaction'
        result, report = self.run_plan()
        self.assertEqual(report['requirements'][0]['status'], 'conflict')
        self.assertFalse(result['foodPlacements'])
        self.assertTrue(report['requirements'][0]['verifiedCandidates'])

    def test_friend_mixed_and_explicit_reaction_protected(self):
        for role in ('friend', 'mixed'):
            with self.subTest(role=role):
                self.plan['selectedSpeech'][0]['voiceRole'] = role
                result, report = self.run_plan()
                self.assertEqual(report['requirements'][0]['status'], 'conflict')
                self.assertFalse(result['foodPlacements'])

    def test_extended_reaction_handle_does_not_block_whole_speech(self):
        self.plan['selectedSpeech'][0]['reactionProtection'] = {'start':1, 'end':12}
        result, report = self.run_plan()
        self.assertEqual(report['requirements'][0]['status'], 'inserted')
        end = result['foodPlacements'][0]['mainSourceStart'] + result['foodPlacements'][0]['end']-result['foodPlacements'][0]['start']
        self.assertLessEqual(end, self.plan['selectedSpeech'][0]['speechEnd'])

    def test_unknown_voice_not_deleted_and_laugh_interval_not_covered(self):
        self.main['voiceGuard']['protected'] = [[1, 5, 'laugh']]
        result, report = self.run_plan()
        self.assertEqual(report['requirements'][0]['status'], 'inserted')
        placement = result['foodPlacements'][0]
        self.assertGreaterEqual(placement['mainSourceStart'], 5)
        self.assertEqual(result['selectedSpeech'], self.plan['selectedSpeech'])

    def test_no_anchor_no_arbitrary_insertion(self):
        self.plan['selectedSpeech'][0]['scriptTarget'] = 99
        self.plan['selectedSpeech'][0]['scriptTargetIds'] = [99]
        result, report = self.run_plan()
        self.assertEqual(report['requirements'][0]['status'], 'unmatched')
        self.assertFalse(result['foodPlacements'])
        self.assertFalse(self.calls)

    def test_other_take_in_same_scene_is_used(self):
        self.plan['selectedSpeech'][0]['voiceRole'] = 'friend'
        self.plan['selectedSpeech'].append(clip('second', 11, target=2))
        self.plan['duration'] = 22
        p = prep()
        p['targets'].append({'text':'继续讲解', 'scene':'上山'})
        p['shotRequirements'][0]['targetIds'] = [1, 2]
        # Only the actual supporting shot verifies; the second main take is ordinary talk.
        def infer(source, times, model, prompt, choices, cache):
            if choices == ['talk', 'reaction', 'unclear']:
                return frame_result('talk')
            return frame_result('usable' if source.endswith('/23.mov') else 'reject')
        with patch.object(shots.broll, 'infer_frames', side_effect=infer):
            result, report = self.run_plan(p, items=[self.main, item('second'), self.insert])
        self.assertEqual(report['requirements'][0]['status'], 'inserted')
        self.assertEqual(result['foodPlacements'][0]['mainAssetId'], 'second')
        self.assertGreater(result['foodPlacements'][0]['at'], 11)

    def test_array_and_scene_anchor_contract(self):
        p = prep()
        self.plan['selectedSpeech'][0].update(scriptTarget=99, scriptTargetIds=[1, 4])
        self.assertEqual(len(shots.anchors(p['shotRequirements'][0], self.plan, p)), 1)
        self.plan['selectedSpeech'][0].update(scriptTargetIds=[], scriptScenes=['上山'])
        self.assertEqual(len(shots.anchors(p['shotRequirements'][0], self.plan, p)), 1)

    def test_explicit_broll_anchor_does_not_expand_to_whole_scene(self):
        p = prep()
        requirement = dict(p['shotRequirements'][0], anchorMode='targets-only', targetIds=[1])
        self.plan['selectedSpeech'][0].update(scriptTarget=99, scriptTargetIds=[99], scriptScenes=['上山'])
        self.assertEqual(shots.anchors(requirement, self.plan, p), [])
        self.plan['selectedSpeech'][0].update(scriptTarget=1, scriptTargetIds=[1])
        self.assertEqual(len(shots.anchors(requirement, self.plan, p)), 1)
        requirement['targetIds'] = []
        self.assertEqual(shots.anchors(requirement, self.plan, p), [])

    def test_merged_dialogue_anchor_uses_requested_line_time_not_other_line(self):
        p = prep()
        requirement = dict(p['shotRequirements'][0], anchorMode='targets-only', targetIds=[2])
        main = self.plan['selectedSpeech'][0]
        main.update(at=100., start=1., end=12., speechStart=1.2, speechEnd=11.,
                    scriptTarget=1, scriptTargetIds=[1, 2], scriptCoverage=[
                        {'target':1, 'sourceStart':1.2, 'sourceEnd':4.3},
                        {'target':2, 'sourceStart':4.3, 'sourceEnd':9.1}])
        original = copy.deepcopy(self.plan)
        anchors = shots.anchors(requirement, self.plan, p)
        self.assertEqual(len(anchors), 1)
        self.assertEqual((anchors[0]['start'], anchors[0]['end'], anchors[0]['at']), (4.3, 9.1, 103.3))
        self.assertEqual(self.plan, original)
        main['scriptCoverage'] = []
        self.assertEqual(shots.anchors(requirement, self.plan, p), [])

    def test_existing_overlay_not_overlapped(self):
        self.plan['foodPlacements'] = [{'assetId':'old', 'start':0, 'end':3, 'at':0}]
        result, report = self.run_plan()
        self.assertEqual(report['requirements'][0]['status'], 'inserted')
        self.assertGreaterEqual(result['foodPlacements'][1]['at'], 3)
        self.assertEqual(result['foodPlacements'][0], self.plan['foodPlacements'][0])

    def test_literal_source_range_remains_candidate_and_ambiguity_is_reported(self):
        req = dict(prep()['shotRequirements'][0], sourceName='23.mov', sourceRange={'in':3., 'out':4.6})
        options, warning = shots.referenced_candidates(req, [self.insert], 30, 2.4, [])
        self.assertIsNone(warning)
        self.assertEqual((options[0]['start'], options[0]['end']), (3., 4.6))
        self.assertNotIn('visualVerified', options[0])
        duplicate = dict(self.insert, assetId='other', source='/other/23.mov')
        options, warning = shots.referenced_candidates(req, [self.insert, duplicate], 30, 2.4, [])
        self.assertFalse(options)
        self.assertIn('同名不唯一', warning)

    def test_explicit_second_track_requirement_is_not_satisfied_by_main_picture(self):
        p = prep()
        p['shotRequirements'][0].update(requiredTrack='V2', sourceName='23.mov', sourceRange={'in':3., 'out':4.6})
        result, report = self.run_plan(p)
        self.assertEqual(report['requirements'][0]['status'], 'inserted')
        self.assertTrue(result['foodPlacements'])
        self.assertFalse(any(e.get('location') == 'main' for e in report['requirements'][0]['evidence']))

    def test_retrieval_score_never_passes_visual_gate(self):
        self.candidate_label = 'reject'
        result, report = self.run_plan()
        self.assertEqual(report['requirements'][0]['status'], 'unmatched')
        self.assertFalse(result['foodPlacements'])

    def test_same_requirements_and_retrieval_across_modes(self):
        outputs = []
        for mode in ('strict', 'balanced', 'expressive'):
            plan = copy.deepcopy(self.plan)
            plan['editPolicy'] = {'mode':mode}
            result, report = self.run_plan(plan=plan)
            outputs.append((report['summary'], [(r['id'], r['status']) for r in report['requirements']], result['foodPlacements']))
        self.assertEqual(outputs[0], outputs[1])
        self.assertEqual(outputs[1], outputs[2])

    def test_remote_or_cloud_model_never_called(self):
        for model in ('remote-cloud', None):
            result, report = self.run_plan(model=model)
            self.assertEqual(report['requirements'][0]['status'], 'uncertain')
            self.assertFalse(result['foodPlacements'])
        with patch.object(shots.broll, 'OLLAMA_URL', 'https://remote.example'):
            self.run_plan()
        self.assertFalse(self.calls)

    def test_failure_is_reported_and_main_stays_unchanged(self):
        with patch.object(shots, '_infer_requirement', side_effect=RuntimeError('offline')):
            result, report = self.run_plan()
        self.assertEqual(report['requirements'][0]['status'], 'uncertain')
        self.assertIn('offline', report['requirements'][0]['reason'])
        self.assertEqual(result['selectedSpeech'], self.plan['selectedSpeech'])
        self.assertTrue(report['complete'])
        self.assertEqual(report['summary']['unresolved'], 1)

    def test_invalid_or_low_confidence_frames_fail_closed(self):
        with patch.object(shots, '_infer_requirement', side_effect=lambda s,t,m,p,c,q: requirement_result('usable', c, .3)):
            result, report = self.run_plan()
        self.assertFalse(result['foodPlacements'])
        self.assertEqual(report['requirements'][0]['status'], 'uncertain')
        with patch.object(shots, '_infer_requirement', return_value={'frames':[]}):
            result, report = self.run_plan()
        self.assertFalse(result['foodPlacements'])
        self.assertIn('不完整', report['requirements'][0]['reason'])

    def test_each_requirement_checkpoint_and_no_silent_drops(self):
        p = prep()
        p['shotRequirements'].append({'id':'shot-2', 'scene':'other', 'targetIds':[99], 'text':'山门入口'})
        writes = []
        real_write = shots.write
        def record(path, value):
            writes.append(copy.deepcopy(value))
            real_write(path, value)
        with patch.object(shots, 'write', side_effect=record):
            result, report = self.run_plan(p)
        self.assertGreaterEqual(len(writes), 4)
        self.assertEqual(report['summary']['required'], 2)
        self.assertEqual([r['id'] for r in report['requirements']], ['shot-1', 'shot-2'])
        self.assertEqual(report['requirements'][1]['status'], 'unmatched')

    def test_candidates_include_unlabelled_no_frames_and_numeric_filename(self):
        other = item('123', '', 6)
        other['frames'] = []
        result = shots.candidate_windows(prep()['shotRequirements'][0], [other], 30, 2.4)
        self.assertTrue(result)
        self.assertEqual(result[0]['item']['assetId'], '123')
        self.assertEqual(result[0]['retrievalScore'], 0)
        self.assertTrue(all(o['end'] <= 6 for o in result))

    def test_sequential_elements_can_occur_in_different_frames(self):
        requirement = prep('先拿起设备，然后穿上设备')['shotRequirements'][0]
        requirement['components'] = ['拿起设备', '穿上设备']
        result = requirement_result('usable', requirement['components'], sequence_required=True)
        result['criteria'][0]['frameIndices'] = [0]
        result['criteria'][1]['frameIndices'] = [2]
        with patch.object(shots, '_infer_requirement', return_value=result):
            state, evidence = shots._verify_visual(self.insert, 0, 2.4, requirement, 'local', 30)
        self.assertEqual(state, 'verified')
        self.assertTrue(evidence['sequenceRequired'])

    def test_one_element_or_unproven_order_is_partial_not_complete(self):
        requirement = prep('先拿起设备，然后穿上设备')['shotRequirements'][0]
        requirement['components'] = ['拿起设备', '穿上设备']
        for missing_element in (True, False):
            result = requirement_result('usable', requirement['components'], sequence_required=True)
            if missing_element:
                result['criteria'][1].update(status='absent', frameIndices=[])
            else:
                result['sequence'].update(status='unclear', frameIndices=[])
            with patch.object(shots, '_infer_requirement', return_value=result):
                state, _ = shots._verify_visual(self.insert, 0, 2.4, requirement, 'local', 30)
            self.assertEqual(state, 'partial')

    def test_excess_elements_not_silently_dropped(self):
        requirement = prep()['shotRequirements'][0]
        requirement['components'] = [f'动作{i}' for i in range(13)]
        state, evidence = shots._verify_visual(self.insert, 0, 2.4, requirement, 'local', 30)
        self.assertEqual(state, 'uncertain')
        self.assertIn('未省略', evidence['reason'])
        self.assertFalse(self.calls)

    def test_later_requirement_cannot_cover_earlier_required_main_shot(self):
        p = prep('主画面必须看见人物拿起背包')
        p['shotRequirements'].append(dict(p['shotRequirements'][0], id='shot-2', text='外骨骼腿部电机运转特写'))
        def infer(source, times, model, prompt, criteria, sequence_required):
            is_first = '主画面必须看见人物拿起背包' in criteria
            visible = source.endswith('/main.mov') if is_first else source.endswith('/23.mov')
            return requirement_result('usable' if visible else 'reject', criteria, sequence_required=sequence_required)
        with patch.object(shots, '_infer_requirement', side_effect=infer):
            result, report = self.run_plan(p)
        first, second = report['requirements']
        self.assertEqual(first['status'], 'covered-main')
        self.assertEqual(second['status'], 'inserted')
        a, b = shots._timeline_range(first['placement'])
        x, y = shots._timeline_range(second['placement'])
        self.assertTrue(y <= a or x >= b)
        self.assertGreaterEqual(second['placement']['mainSourceStart'], first['placement']['end'])
        self.assertTrue(report['mainCoverageVisibilityChecked'])
        self.assertEqual(report['summary']['coveredMain'], 1)

    def test_main_protection_is_timeline_occurrence_not_all_repeated_source_takes(self):
        protected = {'assetId':'main', 'start':1, 'end':3.4, 'at':0, 'track':1}
        first, second = clip(), clip(at=11, target=2)
        first_windows = shots._main_safe_windows(first, self.main, {}, [], 30, [protected])
        second_windows = shots._main_safe_windows(second, self.main, {}, [], 30, [protected])
        self.assertGreaterEqual(first_windows[0][0], 3.4)
        self.assertLess(second_windows[0][0], 3.4)
        self.assertAlmostEqual(second_windows[0][0], 1.4)

    def test_visibility_audit_downgrades_occluded_coverage(self):
        rows = [{'id':'r1', 'status':'covered-main', 'placement':{'assetId':'main', 'start':1, 'end':3.4, 'at':0}},
                {'id':'r2', 'status':'covered-main', 'placement':{'assetId':'main', 'start':1, 'end':3.4, 'at':11}}]
        blockers = [{'assetId':'23', 'start':0, 'end':2.4, 'at':.4}]
        self.assertEqual(shots._audit_main_visibility(rows, blockers), ['r1'])
        self.assertEqual(rows[0]['status'], 'conflict')
        self.assertEqual(rows[1]['status'], 'covered-main')
        self.assertEqual(len(rows[0]['occludedBy']), 1)

    def test_known_brief_smile_is_protected_even_if_new_samples_say_talk(self):
        boundary = {'clips':[{'assetId':'main', 'before':[1,12], 'startTimes':[2.5], 'endTimes':[],
                              'observed':{'frameActions':[{'index':0, 'action':'smile', 'confidence':.99}]}}]}
        shots.write(self.output/'boundary-review.json', boundary)
        result, report = self.run_plan()
        self.assertEqual(report['requirements'][0]['status'], 'inserted')
        placement = result['foodPlacements'][0]
        a = placement['mainSourceStart']
        b = a+placement['end']-placement['start']
        self.assertTrue(b <= 2.25 or a >= 3)
        self.assertTrue(any(c.get('choices') == ['talk', 'reaction', 'unclear'] for c in self.calls))

    def test_gesture_alone_not_whole_speech_protection(self):
        boundary = {'clips':[{'assetId':'main', 'startTimes':[2.5], 'endTimes':[],
                              'observed':{'frameActions':[{'index':0, 'action':'gesture', 'confidence':.99}]}}]}
        windows = shots._main_safe_windows(clip(), self.main, boundary, [], 30)
        self.assertAlmostEqual(windows[0][0], 1.4)
        self.assertGreater(windows[0][1], 3)

    def test_continuation_smile_index_mapping_and_confidence_gate(self):
        boundary = {'clips':[{'assetId':'main', 'before':[1,12], 'startTimes':[], 'endTimes':[], 'observed':{},
            'continuations':[{'endTimes':[2.5], 'observed':{'frameActions':[{'index':1, 'action':'smile', 'confidence':.9}]}}]}]}
        windows = shots._main_safe_windows(clip(), self.main, boundary, [], 30)
        self.assertTrue(all(b <= 2.25 or a >= 3 for a, b in windows))
        boundary['clips'][0]['continuations'][0]['observed']['frameActions'][0]['confidence'] = .5
        windows = shots._main_safe_windows(clip(), self.main, boundary, [], 30)
        self.assertAlmostEqual(windows[0][0], 1.4)

    def test_visible_clause_and_quoted_speech_are_separate_partial_obligations(self):
        original = '小林上坡步履轻松、表情享受，说“像有人在后面帮我”'
        p = prep(original)
        p['shotRequirements'][0]['sourceRefs'] = [{'sourceLine':3, 'text':original}]
        result, report = self.run_plan(p)
        row = report['requirements'][0]
        self.assertEqual(row['text'], original)
        self.assertEqual(row['sourceRefs'], p['shotRequirements'][0]['sourceRefs'])
        self.assertEqual(row['status'], 'partial')
        self.assertEqual(row['componentCoverage'][0]['text'], '小林上坡步履轻松、表情享受')
        self.assertEqual(row['nonVisualObligations'][0]['text'], '说“像有人在后面帮我”')
        self.assertEqual(row['nonVisualObligations'][0]['status'], 'needs-audio-review')
        self.assertEqual(report['summary']['inserted'], 0)
        self.assertEqual(report['summary']['newInsertCount'], 1)
        self.assertEqual(len(row['placements']), 1)
        self.assertEqual(row['placements'][0]['componentId'], 'shot-1:c1')
        self.assertTrue(result['foodPlacements'][0]['audioMuted'])
        self.assertEqual(result['foodPlacements'][0]['requirementId'], 'shot-1')
        self.assertNotIn('像有人在后面帮我', ''.join(c['prompt'] for c in self.calls))

    def test_accounting_quote_does_not_invent_bill_or_phone_shot(self):
        original = '算账“成本76、卖了180”'
        result, report = self.run_plan(prep(original))
        row = report['requirements'][0]
        self.assertEqual(row['text'], original)
        self.assertEqual(row['componentCoverage'], [])
        self.assertEqual(row['nonVisualObligations'][0]['text'], original)
        self.assertEqual(row['nonVisualObligations'][0]['kind'], 'claim')
        self.assertEqual(row['status'], 'uncertain')
        self.assertFalse(result['foodPlacements'])
        self.assertFalse(self.calls)
        self.assertEqual(result['selectedSpeech'], self.plan['selectedSpeech'])

    def test_parallel_components_partial_candidate_never_inserted(self):
        def infer(source, times, model, prompt, criteria, sequence_required):
            label = 'reject'
            if source.endswith('/23.mov'):
                label = 'usable' if criteria[0] == '切瓜' else 'partial'
            return requirement_result(label, criteria, sequence_required=sequence_required)
        with patch.object(shots, '_infer_requirement', side_effect=infer):
            result, report = self.run_plan(prep('切瓜、递瓜、收款到账给特写'))
        row = report['requirements'][0]
        self.assertEqual(row['status'], 'partial')
        self.assertEqual([c['text'] for c in row['componentCoverage']], ['切瓜','递瓜','收款到账给特写'])
        self.assertEqual([c['status'] for c in row['componentCoverage']], ['inserted','partial','partial'])
        self.assertEqual([c['text'] for c in row['remainingComponents']], ['递瓜','收款到账给特写'])
        self.assertEqual(len(result['foodPlacements']), 1)
        self.assertEqual(len(row['placements']), 1)
        self.assertEqual(report['summary']['newInsertCount'], 1)
        self.assertEqual(report['summary']['inserted'], 0)
        self.assertEqual(report['summary']['unresolved'], 1)

    def test_all_parallel_components_pass_and_insert_count_is_physical(self):
        by_text = {'切瓜':'23', '递瓜':'24', '收款到账给特写':'25'}
        def infer(source, times, model, prompt, criteria, sequence_required):
            matched = source.endswith('/'+by_text.get(criteria[0], 'none')+'.mov')
            return requirement_result('usable' if matched else 'reject', criteria, sequence_required=sequence_required)
        with patch.object(shots, '_infer_requirement', side_effect=infer):
            result, report = self.run_plan(prep('切瓜、递瓜、收款到账给特写'),
                items=[self.main, item('23','切瓜'), item('24','递瓜'), item('25','收款到账给特写')])
        row = report['requirements'][0]
        self.assertEqual(row['status'], 'inserted')
        self.assertEqual(row['remainingComponents'], [])
        self.assertEqual(row['nonVisualObligations'], [])
        self.assertEqual(len(result['foodPlacements']), 3)
        self.assertEqual(report['summary']['newInsertCount'], 3)
        self.assertEqual(report['summary']['inserted'], 1)
        self.assertEqual([p['componentId'] for p in row['placements']], ['shot-1:c1','shot-1:c2','shot-1:c3'])
        for a,b in zip(result['foodPlacements'], result['foodPlacements'][1:]):
            self.assertLessEqual(shots._timeline_range(a)[1], b['at']+1e-6)
        self.assertEqual(result['selectedSpeech'], self.plan['selectedSpeech'])

    def test_sequence_is_not_split_into_independently_complete_short_shots(self):
        text = '先切瓜，然后递瓜'
        parts, duties, pending = shots._decompose_requirement(prep(text)['shotRequirements'][0])
        self.assertEqual(parts, [text])
        self.assertEqual(duties, [])
        self.assertFalse(pending)
        self.candidate_label = 'partial'
        result, report = self.run_plan(prep(text))
        self.assertEqual(report['requirements'][0]['status'], 'partial')
        self.assertFalse(result['foodPlacements'])

    def test_mixed_sequential_chain_keeps_original_order_outstanding(self):
        text = '先切瓜，说“好了”，然后递瓜'
        p = prep(text)
        p['shotRequirements'][0]['sequenceRequired'] = True
        result, report = self.run_plan(p)
        row = report['requirements'][0]
        self.assertEqual(row['text'], text)
        self.assertEqual(row['status'], 'partial')
        self.assertTrue(row['sequencePending'])
        self.assertIn('shot-1:sequence', [c['id'] for c in row['remainingComponents']])
        self.assertEqual(report['summary']['inserted'], 0)
        self.assertNotIn('好了', ''.join(c['prompt'] for c in self.calls))

    def test_noun_lists_not_split_and_quoted_commas_not_lost(self):
        text = '黑色、金色机械腿特写'
        self.assertEqual(shots._decompose_requirement(prep(text)['shotRequirements'][0])[0], [text])
        text = '小林说“成本76、卖了180，赚104”'
        parts, duties, _ = shots._decompose_requirement(prep(text)['shotRequirements'][0])
        self.assertFalse(parts)
        self.assertEqual(duties[0]['text'], text)

    def test_explicit_product_effect_is_not_visual_truth(self):
        text = '小林和助理笑着下山，外骨骼展示下坡助力与缓降'
        result, report = self.run_plan(prep(text))
        row = report['requirements'][0]
        self.assertEqual(row['status'], 'partial')
        self.assertEqual(row['componentCoverage'][0]['text'], '小林和助理笑着下山')
        self.assertEqual(row['nonVisualObligations'][0]['kind'], 'claim')
        self.assertNotIn('展示下坡助力', ''.join(c['prompt'] for c in self.calls))

    def test_partial_parent_main_component_still_protected_from_later_v2(self):
        p = prep('人物拿起背包，说“现在出发”')
        p['shotRequirements'].append(dict(p['shotRequirements'][0], id='shot-2', text='电机运转特写'))
        def infer(source, times, model, prompt, criteria, sequence_required):
            visible = source.endswith('/main.mov') if criteria == ['人物拿起背包'] else source.endswith('/23.mov')
            return requirement_result('usable' if visible else 'reject', criteria, sequence_required=sequence_required)
        with patch.object(shots, '_infer_requirement', side_effect=infer):
            result, report = self.run_plan(p)
        first, second = report['requirements']
        self.assertEqual(first['status'], 'partial')
        self.assertEqual(first['componentCoverage'][0]['status'], 'covered-main')
        self.assertEqual(second['status'], 'inserted')
        a,b = shots._timeline_range(first['componentCoverage'][0]['placement'])
        x,y = shots._timeline_range(second['placement'])
        self.assertTrue(y <= a or x >= b)

    def test_existing_component_overlay_has_no_new_physical_insert(self):
        self.plan['foodPlacements'] = [{'assetId':'23', 'start':0, 'end':2.4, 'at':.4}]
        result, report = self.run_plan(prep('人物上坡，说“好轻松”'))
        row = report['requirements'][0]
        self.assertEqual(row['status'], 'partial')
        self.assertTrue(row['placements'][0]['existing'])
        self.assertEqual(row['placements'][0]['componentId'], 'shot-1:c1')
        self.assertEqual(report['summary']['newInsertCount'], 0)
        self.assertEqual(len(result['foodPlacements']), 1)

    def test_shared_closeup_qualifier_is_not_lost_on_parallel_actions(self):
        def infer(source, times, model, prompt, criteria, sequence_required):
            value = requirement_result('usable' if source.endswith('/23.mov') else 'reject', criteria)
            if source.endswith('/23.mov'):
                # The action is clearly visible, but only in a medium/full shot.
                value['criteria'][-1].update(status='absent', frameIndices=[], description='普通近景不是局部特写')
            self.assertEqual(criteria[-1], '共享构图限定：特写')
            self.assertIn('普通人物近景或全景', prompt)
            return value
        with patch.object(shots, '_infer_requirement', side_effect=infer):
            result, report = self.run_plan(prep('切瓜、递瓜、收款到账给特写'))
        row = report['requirements'][0]
        self.assertEqual(row['status'], 'partial')
        self.assertFalse(result['foodPlacements'])
        self.assertEqual([c['text'] for c in row['componentCoverage']], ['切瓜','递瓜','收款到账给特写'])
        self.assertTrue(all(c['visualQualifier'] == '特写' for c in row['componentCoverage']))
        self.assertTrue(all(c['visualQualifierSource']['text'] == '给特写' for c in row['componentCoverage']))
        self.assertEqual(report['summary']['newInsertCount'], 0)

    def test_continuous_context_not_split_and_future_plan_not_visual(self):
        text = '连续上坡路段，拍小林迈步与外骨骼腿部运行'
        self.assertEqual(shots._decompose_requirement(prep(text)['shotRequirements'][0])[0], [text])
        text = '下次要当老板让助理背瓜，两人大笑'
        parts, duties, _ = shots._decompose_requirement(prep(text)['shotRequirements'][0])
        self.assertEqual(parts, ['两人大笑'])
        self.assertEqual(duties[0]['text'], '下次要当老板让助理背瓜')
        self.assertEqual(duties[0]['kind'], 'claim')

    def test_component_source_offsets_and_visible_context_keep_literal_provenance(self):
        text = '切瓜、递瓜、收款到账给特写'
        _, report = self.run_plan(prep(text))
        for child in report['requirements'][0]['componentCoverage']:
            self.assertEqual(text[child['sourceStart']:child['sourceEnd']], child['text'])
            self.assertEqual(child['visualContext'], '切瓜；递瓜；收款到账给特写')

    def test_unquoted_language_and_amounts_require_audio_not_images(self):
        text = '游客围观砍价，小林现学现卖用粤语招呼，逗笑众人'
        _, report = self.run_plan(prep(text))
        row = report['requirements'][0]
        self.assertEqual(row['text'], text)
        self.assertEqual(row['status'], 'partial')
        self.assertEqual(row['nonVisualObligations'][0]['text'], '小林现学现卖用粤语招呼')
        self.assertNotIn('粤语', ''.join(c['prompt'] for c in self.calls))
        self.calls.clear()
        result, report = self.run_plan(prep('成本75，赚了180'))
        row = report['requirements'][0]
        self.assertEqual(row['componentCoverage'], [])
        self.assertEqual([d['text'] for d in row['nonVisualObligations']], ['成本75','赚了180'])
        self.assertTrue(all(d['kind'] == 'claim' for d in row['nonVisualObligations']))
        self.assertFalse(self.calls)
        self.assertFalse(result['foodPlacements'])

    def test_shared_closeup_survives_following_quote(self):
        for quote in ('“很好吃”', '‘很好吃’'):
            text = '切瓜、递瓜给特写，说'+quote
            def infer(source, times, model, prompt, criteria, sequence_required):
                value = requirement_result('usable' if source.endswith('/23.mov') else 'reject', criteria)
                self.assertEqual(criteria[-1], '共享构图限定：特写')
                self.assertNotIn('很好吃', prompt)
                if source.endswith('/23.mov'):
                    value['criteria'][-1].update(status='absent', frameIndices=[], description='全景')
                return value
            with patch.object(shots, '_infer_requirement', side_effect=infer):
                result, report = self.run_plan(prep(text))
            row = report['requirements'][0]
            self.assertEqual([c['text'] for c in row['componentCoverage']], ['切瓜','递瓜给特写'])
            self.assertTrue(all(c['visualQualifier'] == '特写' for c in row['componentCoverage']))
            self.assertTrue(all(text[c['visualQualifierSource']['sourceStart']:c['visualQualifierSource']['sourceEnd']] == '给特写'
                                for c in row['componentCoverage']))
            self.assertFalse(result['foodPlacements'])

    def test_one_physical_overlay_reused_by_many_components_counted_once(self):
        result, report = self.run_plan(prep('切瓜、递瓜、收款到账给特写'))
        row = report['requirements'][0]
        self.assertEqual(row['status'], 'inserted')
        self.assertEqual(len(row['componentCoverage']), 3)
        self.assertEqual(len(result['foodPlacements']), 1)
        self.assertEqual(len(row['placements']), 1)
        self.assertEqual(row['placements'][0]['componentIds'], ['shot-1:c1','shot-1:c2','shot-1:c3'])
        self.assertFalse(row['placements'][0]['existing'])
        self.assertEqual(report['summary']['newInsertCount'], 1)
        self.assertTrue(row['componentCoverage'][1]['placement']['existing'])

    def test_every_provisional_match_requires_independent_audit(self):
        for requirement in ('人物双手捧着物品', '门洞入口', '切瓜特写', '助理帮人物穿戴设备'):
            with self.subTest(requirement=requirement):
                self.counter_infer.stop()
                with patch.object(shots, '_infer_counterevidence', side_effect=lambda f,m,a: counterevidence_result(a)) as audit:
                    state, evidence = shots._verify_visual(self.insert, 0, 2.4,
                        prep(requirement)['shotRequirements'][0], 'local', 30)
                self.counter_infer.start()
                self.assertTrue(audit.called)
                self.assertEqual(state, 'verified')
                self.assertIn('independentReview', evidence)

    def test_counterevidence_rejection_short_circuits_primary(self):
        with patch.object(shots, '_infer_counterevidence', side_effect=lambda f,m,a: counterevidence_result(a, status='absent')) as audit, \
                patch.object(shots, '_infer_requirement') as primary:
            result, report = self.run_plan()
        self.assertTrue(audit.called)
        primary.assert_not_called()
        self.assertFalse(result['foodPlacements'])

    def test_people_lower_bound_overrides_false_pass_label(self):
        with patch.object(shots, '_infer_counterevidence', side_effect=lambda f,m,a: counterevidence_result(a, people=1)):
            result, report = self.run_plan(prep('助理把小林带到西瓜摊，满地西瓜'))
        self.assertFalse(result['foodPlacements'])
        self.assertNotIn(report['requirements'][0]['status'], ('inserted','covered-main'))
        failures = [e['auditFailures'] for e in report['requirements'][0]['evidence'] if e.get('auditFailures')]
        self.assertTrue(any(any('至少两个人' in f['requirement'] for f in group) for group in failures))

    def test_related_state_or_backdrop_cannot_satisfy_action_or_setting(self):
        cases = [('切瓜特写', '一名男子双手拿着一个西瓜'),
                 ('助理帮小林穿戴设备', '一名男子已经穿戴好设备站立'),
                 ('山门入口', '一名男子站在浮雕石墙前'),
                 ('扛起西瓜就走', '一名男子双手把西瓜举高')]
        for text, observed in cases:
            with self.subTest(requirement=text):
                def infer(files, model, atoms):
                    result = counterevidence_result(atoms)
                    for check in result['checks']:
                        check['observedEvidence'] = observed
                    return result
                with patch.object(shots, '_infer_counterevidence', side_effect=infer):
                    result, report = self.run_plan(prep(text))
                self.assertFalse(result['foodPlacements'])
                self.assertNotIn(report['requirements'][0]['status'], ('inserted','covered-main'))

    def test_missing_evidence_contradiction_overrides_pass(self):
        atoms = shots._audit_atoms({'text':'山门入口'})
        value = counterevidence_result(atoms)
        for check in value['checks']:
            check.update(observedEvidence='图中是一名男子站在浮雕墙前', missingEvidence='画面没有明确显示山门入口')
        state, failures = shots._counterevidence_status(value, atoms)
        self.assertEqual(state, 'rejected')
        self.assertEqual(len(failures), len(atoms))

    def test_negative_evidence_words_not_treated_as_positive_keyword_support(self):
        atoms = shots._audit_atoms({'text':'切瓜特写'})
        value = counterevidence_result(atoms)
        for check in value['checks']:
            check['observedEvidence'] = '没有刀具接触和切割动作，未出现细节特写'
        state, failures = shots._counterevidence_status(value, atoms)
        self.assertNotEqual(state, 'verified')
        self.assertTrue(failures)

    def test_unavailable_or_invalid_counterevidence_never_falls_back_to_first_pass(self):
        for mode in ('offline', 'bad-index', 'low-confidence'):
            def infer(files, model, atoms):
                if mode == 'offline':
                    raise RuntimeError('offline')
                result = counterevidence_result(atoms, confidence=.5 if mode == 'low-confidence' else .95)
                if mode == 'bad-index':
                    result['checks'][0]['frameIndices'] = [4]
                return result
            with self.subTest(mode=mode), patch.object(shots, '_infer_counterevidence', side_effect=infer):
                result, report = self.run_plan()
            self.assertFalse(result['foodPlacements'])
            self.assertEqual(report['requirements'][0]['status'], 'uncertain')

    def test_main_and_existing_v2_need_same_second_gate(self):
        self.main_label = 'usable'
        self.plan['foodPlacements'] = [{'assetId':'23', 'start':0, 'end':2.4, 'at':.4}]
        with patch.object(shots, '_infer_counterevidence', side_effect=lambda f,m,a: counterevidence_result(a, status='absent')):
            result, report = self.run_plan()
        self.assertNotIn(report['requirements'][0]['status'], ('inserted','covered-main'))
        self.assertEqual(result['foodPlacements'], self.plan['foodPlacements'])

    def test_auditory_hesitation_not_visually_proven(self):
        result, report = self.run_plan(prep('助理支支吾吾'))
        row = report['requirements'][0]
        self.assertEqual(row['componentCoverage'], [])
        self.assertEqual(row['nonVisualObligations'][0]['text'], '助理支支吾吾')
        self.assertFalse(self.calls)
        self.assertFalse(result['foodPlacements'])

    def test_reordered_gates_keep_exact_acceptance_intersection(self):
        requirement = prep('男子双手捧着水果，摊位背景')['shotRequirements'][0]
        atoms = shots._audit_atoms(requirement)
        for primary_label in ('usable','partial','reject','unclear'):
            for audit_label in ('pass','absent','unclear','partial'):
                calls = []
                audit_result = counterevidence_result(atoms, status='pass' if audit_label == 'partial' else audit_label)
                if audit_label == 'partial':
                    audit_result['checks'][1].update(status='absent', missingEvidence='第二项缺失')
                def audit(files, model, criteria):
                    calls.append('audit')
                    return audit_result
                def primary(source, times, model, prompt, criteria, sequence):
                    calls.append('primary')
                    return requirement_result(primary_label, criteria)
                with self.subTest(primary=primary_label,audit=audit_label), \
                        patch.object(shots, '_infer_counterevidence', side_effect=audit), \
                        patch.object(shots, '_infer_requirement', side_effect=primary):
                    status, evidence = shots._verify_visual(self.insert,0,2.4,requirement,'local',30)
                    self.assertEqual(status == 'verified', primary_label == 'usable' and audit_label == 'pass')
                    self.assertEqual(calls, ['audit','primary'] if audit_label == 'pass' else ['audit'])
                    self.assertEqual(evidence['primarySkipped'], audit_label != 'pass')
                    self.assertEqual(evidence['verificationOrder'], 'counterevidence-then-primary')
                    self.assertEqual(len(evidence['visual']['evidence']), 3)

    def test_uncertain_audit_short_circuits_even_a_malformed_primary(self):
        with patch.object(shots, '_infer_counterevidence', side_effect=lambda f,m,a: counterevidence_result(a, status='unclear')), \
                patch.object(shots, '_infer_requirement', side_effect=AssertionError('must not run')) as primary:
            status, evidence = shots._verify_visual(self.insert,0,2.4,prep()['shotRequirements'][0],'local',30)
        self.assertEqual(status, 'uncertain')
        self.assertTrue(evidence['primarySkipped'])
        primary.assert_not_called()

    def test_primary_cache_identity_and_pixels_survive_pipeline_revision(self):
        source = self.output/'input.mov'
        source.write_bytes(b'test-source')
        args = (str(source), [0,.5,1], 'local', 'unchanged-primary-prompt', ['visible'], False)
        stat = source.stat()
        expected = shots.hashlib.sha256(shots.json.dumps(['script-shots-4',str(source),stat.st_size,stat.st_mtime_ns,
            [0,.5,1],'local','unchanged-primary-prompt',['visible'],False],ensure_ascii=False).encode()).hexdigest()
        with patch.object(shots,'DATA',self.output), patch.object(shots,'REVISION','future-pipeline-only'):
            self.assertEqual(shots._requirement_folder(*args).name, expected)
            self.frame_paths.stop()
            def extract(command, **kwargs):
                Path(command[-1]).write_bytes(b'test-image')
            with patch.object(shots.subprocess,'run',side_effect=extract) as ffmpeg:
                first = shots._requirement_frame_paths(*args)
                second = shots._requirement_frame_paths(*args)
            self.frame_paths.start()
            self.assertEqual(first,second)
            self.assertEqual(ffmpeg.call_count,3)
            self.assertEqual([p.name for p in first], ['0.jpg','1.jpg','2.jpg'])

    def test_per_candidate_and_stage_progress_without_changing_plan(self):
        messages = []
        result, report = shots.apply_script_shots(self.plan,[self.main,self.insert],prep(),self.output,'local',log=messages.append)
        self.assertTrue(any('主线窗口 1/' in line for line in messages))
        self.assertTrue(any('新镜头候选 1/' in line for line in messages))
        self.assertTrue(any('反证审核：' in line for line in messages))
        self.assertTrue(any('反证通过，核对完整要素/顺序' in line for line in messages))
        self.assertTrue(any('人物反应/遮盖位置复核' in line for line in messages))
        self.assertEqual(report['summary']['newInsertCount'],1)


if __name__ == '__main__':
    unittest.main()
