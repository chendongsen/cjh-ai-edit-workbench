import copy
import unittest
from script_coverage import audit_script_conflicts, build_coverage, coverage_text, validate_dialogue_integrity


class ScriptCoverageTests(unittest.TestCase):
    def test_old_hits_are_not_falsely_complete(self):
        preparation={'targets':[{'text':'完整句'},{'text':'没有的句'}],
                     'shotRequirements':[{'id':'shot-1','text':'电池特写'}]}
        row={'assetId':'a','source':'/test/a.mov','start':0,'end':2,'scriptTarget':1}
        report=build_coverage(preparation, {'selectedSpeech':[row],'duration':2})
        self.assertEqual(report['dialogue']['partial'],1)
        self.assertEqual(report['dialogue']['missing'],1)
        self.assertEqual(report['shots']['unresolved'],1)
        self.assertIn('未匹配不等于原片缺失',coverage_text(report))

    def test_merged_targets_and_shot_statuses(self):
        prep={'targets':[{'text':'一'},{'text':'二'}]}
        row={'assetId':'a','source':'/test/a.mov','start':0,'end':2,'scriptTargetIds':[1,2],
             'scriptCoverage':[{'target':1,'status':'complete','sourceStart':0,'sourceEnd':2},
                               {'target':2,'status':'partial','sourceStart':0,'sourceEnd':2}]}
        shots={'requirements':[{'id':'s','text':'笑容','status':'covered-main'},
                               {'id':'b','text':'电池','status':'conflict'}]}
        r=build_coverage(prep,{'selectedSpeech':[row]},shots)
        self.assertEqual(r['dialogue']['complete'],1)
        self.assertEqual(r['dialogue']['partial'],1)
        self.assertEqual(r['shots']['coveredMain'],1)
        self.assertEqual(r['shots']['unresolved'],1)

    def test_full_evidence_cannot_hide_later_cut(self):
        row={'assetId':'a','source':'a.mov','start':0,'end':2,'scriptTargetIds':[1],
             'scriptCoverage':[{'target':1,'status':'complete','sourceStart':0,'sourceEnd':4}]}
        r=build_coverage({'targets':[{'text':'完整句'}]},{'selectedSpeech':[row]})
        self.assertEqual(r['dialogue']['complete'],0)
        self.assertEqual(r['dialogue']['partial'],1)

    def dialogue_with_pause(self):
        evidence={'target':1,'status':'complete','sourceStart':2,'sourceEnd':11}
        first={'assetId':'a','source':'/test/a.mov','start':1.9,'end':4.2,'scriptTargetIds':[1],
               'scriptCoverage':[evidence]}
        second=dict(first,start=8.9,end=11.2)
        item={'assetId':'a','source':'/test/a.mov','segments':[
            {'start':2,'end':4,'text':'拿包纸吧'}, {'start':9,'end':11,'text':'自带纸巾给你'}]}
        return {'targets':[{'text':'拿包纸吧，自带纸巾给你'}]}, {'selectedSpeech':[first,second]}, item

    def test_removed_silence_is_not_missing_dialogue_with_original_asr(self):
        preparation,plan,item=self.dialogue_with_pause()
        report=build_coverage(preparation,plan,items=[item])
        self.assertEqual(report['dialogue']['complete'],1)
        self.assertEqual(report['dialogue']['partial'],0)
        evidence=report['dialogue']['items'][0]['evidence'][0]
        self.assertEqual(evidence['retentionBasis'],'refined-asr-cores')
        self.assertEqual(evidence['requiredCores'],[{'start':2,'end':4},{'start':9,'end':11}])
        self.assertTrue(evidence['fullyRetained'])

    def test_real_missing_core_or_cut_word_is_still_partial(self):
        for change in ('remove-reply','cut-tail','cut-head','cut-interior'):
            preparation,plan,item=self.dialogue_with_pause()
            with self.subTest(change=change):
                if change=='remove-reply':plan['selectedSpeech'].pop()
                elif change=='cut-tail':plan['selectedSpeech'][1]['end']=10.99
                elif change=='cut-head':plan['selectedSpeech'][1]['start']=9.01
                else:
                    last=plan['selectedSpeech'].pop()
                    plan['selectedSpeech'] += [dict(last,end=9.5),dict(last,start=9.51)]
                report=build_coverage(preparation,plan,items=[item])
                self.assertEqual(report['dialogue']['complete'],0)
                self.assertEqual(report['dialogue']['partial'],1)

    def test_unselected_speech_inside_gap_cannot_be_claimed_as_silence(self):
        preparation,plan,item=self.dialogue_with_pause()
        item['segments'].insert(1,{'start':5,'end':7,'text':'这里还有必要台词'})
        report=build_coverage(preparation,plan,items=[item])
        self.assertEqual(report['dialogue']['complete'],0)
        self.assertEqual(report['dialogue']['partial'],1)

    def test_missing_or_incomplete_original_asr_keeps_envelope_fallback(self):
        preparation,plan,item=self.dialogue_with_pause()
        for items in (None,[],[dict(item,source='/other/a.mov')],
                      [dict(item,segments=[])],[dict(item,segments=item['segments'][:1])],
                      [dict(item,segments=[*item['segments'],{'start':float('nan'),'end':6}])]):
            with self.subTest(items=items):
                report=build_coverage(preparation,plan,items=items)
                self.assertEqual(report['dialogue']['complete'],0)
                self.assertEqual(report['dialogue']['partial'],1)

    def test_original_core_crossing_evidence_edge_is_not_partly_waived(self):
        preparation,plan,item=self.dialogue_with_pause()
        item['segments'][1]['end']=12
        report=build_coverage(preparation,plan,items=[item])
        self.assertEqual(report['dialogue']['complete'],0)
        self.assertEqual(report['dialogue']['partial'],1)

    def test_boundary_validation_rejects_any_speech_core_cut(self):
        row={'source':'a.mov','text':'一句话','start':.1,'end':2,'speechStart':0,'speechEnd':2}
        with self.assertRaisesRegex(ValueError,'完整台词切点'):
            validate_dialogue_integrity([row])
        validate_dialogue_integrity([dict(row,start=0,end=2.2)])

    def test_partial_parent_retains_component_progress_and_physical_inserts(self):
        placement={'assetId':'a','source':'a.mov','start':1,'end':3,'at':5,'track':2,'componentId':'s:c1'}
        shot={'id':'s','scene':'台阶','text':'摘电池并展示变慢','status':'partial','reason':'只找到摘电池',
              'componentCoverage':[{'id':'s:c1','text':'摘电池','status':'inserted','reason':'已安全放入','placement':placement},
                                   {'id':'s:c2','text':'变慢','status':'unmatched','reason':'没有可靠画面'}],
              'remainingComponents':[{'id':'s:c2','text':'变慢','status':'unmatched','reason':'没有可靠画面'}],
              'nonVisualObligations':[{'text':'续航18公里','kind':'claim','status':'needs-audio-review'}],
              'placements':[placement]}
        report=build_coverage({'targets':[]},{'duration':20}, {'requirements':[shot],'summary':{'newInsertCount':1}})
        self.assertEqual(report['shots']['inserted'],0)
        self.assertEqual(report['shots']['unresolved'],1)
        self.assertEqual(report['shots']['physicalInserts'],1)
        self.assertEqual(report['status'],'draft-with-gaps')
        text=coverage_text(report)
        for phrase in ('实际新增第二轨 1 段','不等于完成要求 1 项','摘电池','已补充镜头候选','草稿 5.00s',
                       '仍缺内容','变慢','非视觉义务','续航18公里','不据此认定整项完成'):
            self.assertIn(phrase,text)

    def test_old_shot_reports_do_not_invent_physical_insert_count(self):
        shot={'id':'s','text':'电池','status':'inserted','placement':{'existing':True}}
        old=build_coverage({}, {}, {'requirements':[shot]})
        self.assertEqual(old['shots']['inserted'],1)
        self.assertNotIn('physicalInserts',old['shots'])
        self.assertNotIn('实际新增第二轨',coverage_text(old))
        current=build_coverage({}, {}, {'requirements':[shot],'summary':{'newInsertCount':0}})
        self.assertEqual(current['shots']['physicalInserts'],0)
        self.assertIn('实际新增第二轨 0 段',coverage_text(current))

    def test_component_placements_distinguish_existing_v2(self):
        placement={'track':2,'at':7,'existing':True}
        shot={'id':'s','text':'电池与腿部','status':'partial','componentCoverage':[
            {'id':'s:c1','text':'电池','status':'inserted','placement':placement}],
            'remainingComponents':['腿部'],'placements':[placement]}
        report=build_coverage({}, {}, {'requirements':[shot],'summary':{'newInsertCount':0}})
        text=coverage_text(report)
        self.assertIn('已有片段，未新增',text)
        self.assertIn('本项实际使用第二轨 1 段，其中新增 0 段',text)
        self.assertEqual(report['shots']['unresolved'],1)

    def test_speedup_requirement_keeps_verified_picture_but_is_not_complete(self):
        requirement={'id':'shot-17','scene':'山脚安全坡道（适应机器）',
                     'text':'放一些爬坡的片段做加速','sourceLine':6,'sourceColumn':'文案动作'}
        placement={'assetId':'a','source':'a.mov','start':1,'end':4,'at':5,'track':1}
        shot=dict(requirement,status='covered-main',reason='画面已通过复核',placement=placement,
                  evidence={'observed':'爬坡'},componentCoverage=[
                      {'id':'shot-17:c1','text':'爬坡','status':'covered-main','placement':placement}])
        original=copy.deepcopy(shot)
        report=build_coverage({'shotRequirements':[requirement]}, {}, {'requirements':[shot]})
        self.assertEqual(report['shots']['coveredMain'],0)
        self.assertEqual(report['shots']['unresolved'],1)
        row=report['shots']['items'][0]
        self.assertEqual(row['status'],'partial')
        self.assertEqual(row['text'],requirement['text'])
        self.assertEqual(row['placement'],placement)
        self.assertEqual(row['evidence'],shot['evidence'])
        self.assertEqual(row['componentCoverage'],shot['componentCoverage'])
        self.assertEqual(row['visualCoverage']['status'],'covered-main')
        self.assertEqual(row['nonVisualObligations'][0]['text'],requirement['text'])
        self.assertEqual(row['nonVisualObligations'][0]['effects'],['加速'])
        self.assertEqual(shot,original)
        self.assertIn('待完成／核验后期：加速',coverage_text(report))
        # A report-only rebuild is idempotent and does not alter raw evidence.
        rebuilt=build_coverage({'shotRequirements':[requirement]}, {},
                               {'requirements':report['shots']['items']})
        self.assertEqual(rebuilt['shots']['items'],report['shots']['items'])

    def test_explicit_postproduction_obligations_downgrade_inserts_not_insert_count(self):
        cases=[('笑脸慢放',['慢放']),('爬山片段加速播放',['加速']),
               ('起步用2倍速播放',['加速']),('切瓜后添加花字“好吃”',['花字']),
               ('爬山镜头叠加字幕“十五分钟后”',['字幕']),
               ('字幕：十五分钟后',['字幕']),('字幕“十五分钟后”',['字幕']),('花字“十五分钟后”',['花字']),
               ('笑容做慢动作效果并加上花字“胜利”',['慢放','花字'])]
        for text,effects in cases:
            with self.subTest(text=text):
                requirement={'id':'s','text':text}
                shot=dict(requirement,status='inserted',placements=[{'track':2,'at':1}],
                    nonVisualObligations=[{'text':'保留笑声','kind':'dialogue','status':'needs-audio-review'}])
                report=build_coverage({'shotRequirements':[requirement]}, {},
                    {'requirements':[shot],'summary':{'newInsertCount':1}})
                row=report['shots']['items'][0]
                self.assertEqual(report['shots']['inserted'],0)
                self.assertEqual(report['shots']['physicalInserts'],1)
                self.assertEqual(row['visualCoverage']['status'],'inserted')
                self.assertEqual(row['nonVisualObligations'][0],shot['nonVisualObligations'][0])
                self.assertEqual(row['nonVisualObligations'][1]['effects'],effects)

    def test_real_actions_quotes_and_negative_instructions_are_not_editing_obligations(self):
        for text in ('小林快速爬坡','小林加速爬坡','助理放慢脚步','车辆加速驶离',
                     '小林使用加速模式快速爬坡','演员做加速运动','演员以慢动作示范抬腿',
                     '小林说“把这个片段加速播放并加字幕”','小林看屏幕上的字幕',
                     '小林看到花字后笑了','片段不做加速','片段不要加速播放',
                     '笑脸不用慢放','不要添加字幕','无需花字','不需要做加速','不得加速播放',
                     '小林慢慢放下西瓜','西瓜需要轻拿慢放','人物慢放下手中西瓜'):
            with self.subTest(text=text):
                shot={'id':'s','text':text,'status':'covered-main'}
                report=build_coverage({}, {}, {'requirements':[shot]})
                self.assertEqual(report['shots']['coveredMain'],1)
                self.assertNotIn('nonVisualObligations',report['shots']['items'][0])

    def test_original_preparation_instruction_survives_simplified_visual_report(self):
        prep={'shotRequirements':[{'id':'s','text':'爬坡片段做加速','sourceLine':3}]}
        shots={'requirements':[{'id':'s','text':'爬坡片段','status':'covered-main'}]}
        report=build_coverage(prep,{},shots)
        row=report['shots']['items'][0]
        self.assertEqual(row['status'],'partial')
        self.assertEqual(row['text'],'爬坡片段')
        self.assertEqual(row['nonVisualObligations'][0]['text'],'爬坡片段做加速')

    def test_unmatched_picture_is_not_promoted_by_pending_edit_instruction(self):
        shot={'id':'s','text':'爬坡片段做加速','status':'unmatched','reason':'未找到画面'}
        report=build_coverage({}, {}, {'requirements':[shot]})
        row=report['shots']['items'][0]
        self.assertEqual(row['status'],'unmatched')
        self.assertNotIn('visualCoverage',row)
        self.assertEqual(report['shots']['unresolved'],1)

    def manual_review(self, **changes):
        row={'requirementId':'shot-23','status':'uncertain',
             'reason':'仅见白袍人物展示手机，未确认助理或坏笑',
             'source':'13.mov 1.533、2.733、3.900 秒；0.jpg、1.jpg、2.jpg',
             'reviewer':'Codex 抽帧复核（不是剪辑师人工审片）','reviewedAt':'2026-10-22'}
        row.update(changes)
        return {'revision':'manual-shot-review-1','reviews':[row]}

    def test_explicit_supplemental_review_downgrades_without_changing_visual_evidence(self):
        shot={'id':'shot-23','text':'助理露出坏笑','status':'covered-main','reason':'模型认为符合',
              'evidence':{'description':'举手机'},'placement':{'track':1,'start':1.53,'end':3.93,'at':5},
              'componentCoverage':[{'id':'shot-23:c1','status':'covered-main','text':'助理露出坏笑'}]}
        original=copy.deepcopy(shot)
        manual=self.manual_review()
        report=build_coverage({}, {}, {'requirements':[shot]}, manual_review=manual)
        row=report['shots']['items'][0]
        self.assertEqual(row['status'],'uncertain')
        self.assertEqual(report['shots']['coveredMain'],0)
        self.assertEqual(report['shots']['unresolved'],1)
        self.assertEqual(row['visualCoverage'],{'status':'covered-main','reason':'模型认为符合'})
        for field in ('text','evidence','placement','componentCoverage'):
            self.assertEqual(row[field],original[field])
        self.assertEqual(shot,original)
        self.assertEqual(report['manualReview'],manual)
        self.assertEqual(row['manualReview']['source'],manual['reviews'][0]['source'])
        text=coverage_text(report)
        for phrase in ('补充抽帧复核意见','仅见白袍人物展示手机','原模型视觉结论','不是剪辑师人工审片'):
            self.assertIn(phrase,text)
        # Without explicit supplemental evidence, automatic coverage is unchanged.
        self.assertEqual(build_coverage({}, {}, {'requirements':[shot]})['shots']['coveredMain'],1)

    def test_supplemental_review_preserves_pending_postproduction_and_real_insert_count(self):
        shot={'id':'shot-23','text':'爬坡片段做加速','status':'inserted','reason':'画面符合',
              'placements':[{'track':2,'at':5}]}
        report=build_coverage({}, {}, {'requirements':[shot],'summary':{'newInsertCount':1}},
                              manual_review=self.manual_review(status='partial'))
        row=report['shots']['items'][0]
        self.assertEqual(row['status'],'partial')
        self.assertEqual(row['manualReview']['previousCoverageStatus'],'partial')
        self.assertEqual(row['manualReview']['originalVisualStatus'],'inserted')
        self.assertEqual(row['nonVisualObligations'][0]['effects'],['加速'])
        self.assertEqual(report['shots']['inserted'],0)
        self.assertEqual(report['shots']['physicalInserts'],1)

    def test_supplemental_review_rejects_upgrades_unknown_ids_duplicate_and_edit_payloads(self):
        shot={'id':'shot-23','text':'助理露出坏笑','status':'covered-main'}
        bad=[self.manual_review(status='covered-main'),self.manual_review(status='inserted'),
             self.manual_review(requirementId='unknown'),self.manual_review(reviewer=''),
             self.manual_review(start=0),{'revision':'wrong','reviews':[]},
             {'revision':'manual-shot-review-1','reviews':None}]
        duplicate=self.manual_review()
        duplicate['reviews']*=2
        bad.append(duplicate)
        for manual in bad:
            with self.subTest(manual=manual):
                original=copy.deepcopy(shot)
                with self.assertRaises(ValueError):
                    build_coverage({}, {}, {'requirements':[shot]}, manual_review=manual)
                self.assertEqual(shot,original)
        for status in ('unmatched','uncertain','partial','conflict'):
            with self.subTest(originalStatus=status):
                with self.assertRaisesRegex(ValueError,'原本标记完成'):
                    build_coverage({}, {}, {'requirements':[dict(shot,status=status)]},
                                   manual_review=self.manual_review(status='partial'))

    def test_real_style_explicit_time_conflict(self):
        prep={'targets':[{'scene':'山脚安全坡道','text':'现在才16：00，我们一个小时登顶','sourceLine':6}],
              'shotRequirements':[{'id':'shot-14','scene':'山脚安全坡道','text':'举手机看时间14:00立Flag“一小时登顶”，背瓜小跑出发。','sourceLine':6,'sourceColumn':'画面内容'}]}
        conflicts=audit_script_conflicts(prep)
        self.assertEqual(len(conflicts),1)
        self.assertEqual(conflicts[0]['kind'],'time')
        self.assertEqual(conflicts[0]['dialogue']['value'],'16:00')
        self.assertEqual(conflicts[0]['shot']['value'],'14:00')
        report=build_coverage(prep,{})
        self.assertEqual(report['dialogue']['missing'],1)
        self.assertEqual(report['shots']['unresolved'],1)
        text=coverage_text(report)
        self.assertIn(prep['targets'][0]['text'],text)
        self.assertIn(prep['shotRequirements'][0]['text'],text)
        self.assertIn('不自动选择',text)

    def test_real_style_explicit_cost_conflict_not_profit(self):
        prep={'targets':[{'scene':'山顶→下山步道','text':'哦哟哟！成本75，赚了180！'}],
              'shotRequirements':[{'id':'shot-35','scene':'山顶→下山步道','text':'算账“成本76、卖了180”'}]}
        conflicts=audit_script_conflicts(prep)
        self.assertEqual(len(conflicts),1)
        self.assertEqual(conflicts[0]['field'],'成本')
        self.assertEqual(conflicts[0]['dialogue']['value'],'75')
        self.assertEqual(conflicts[0]['shot']['value'],'76')

    def test_script_conflict_guardrails_for_scene_same_value_and_ambiguous_numbers(self):
        cases=[
            ({'scene':'山脚','text':'现在16:00，成本75'},{'scene':'山顶','text':'时间14:00，成本76'}),
            ({'scene':'山脚','text':'现在4:00，成本75.0'},{'scene':'山脚','text':'时间04：00，成本75'}),
            ({'scene':'山脚','text':'从14:00到16:00，成本75，成本76'},{'scene':'山脚','text':'时间15:00，成本77'}),
            ({'scene':'山脚','text':'赚了180，18公里续航'},{'scene':'山脚','text':'卖了200，续航20公里'}),
            ({'text':'时间14:00，成本75'},{'text':'时间16:00，成本76'}),
        ]
        for dialogue,shot in cases:
            with self.subTest(dialogue=dialogue,shot=shot):
                self.assertEqual(audit_script_conflicts({'targets':[dialogue],'shotRequirements':[shot]}),[])


if __name__=='__main__':unittest.main()
