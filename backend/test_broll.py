import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from common import read
from broll import enhance, free_windows, intent, options_for, same_scene, verify_insert


def main(text='这个虾饺皮薄馅多，口感非常鲜甜', name='吃虾饺.mov'):
    item = {'assetId':'main','source':'/batch/shop/'+name,'video':True,'duration':30,
            'usableDuration':30,'vision':{'kind':'人物'},'voiceGuard':{'protected':[]}}
    clip = {'assetId':'main','source':item['source'],'start':1,'end':12,'speechStart':1,
            'speechEnd':10,'at':0,'text':text,'voiceRole':'unknown'}
    return item, clip


def food(dish='虾饺', source='/batch/shop/菜品/food.mov'):
    return {'assetId':'food','source':source,'video':True,'duration':20,'usableDuration':20,
            'vision':{'kind':'食物'},'foodShots':[{'at':2,'nextAt':8,'nextTags':[dish],
            'tags':[dish],'confidence':.9,'frame':'frame.jpg'}]}


class BrollTests(unittest.TestCase):
    def run_plan(self, item, clip, foods, verifier=lambda *args:(True,{})):
        plan = {'selectedSpeech':[clip],'foodPlacements':[],'duration':clip['end']-clip['start']}
        with tempfile.TemporaryDirectory() as temp:
            return enhance(plan,[item,*foods],{'visionModel':'local','fps':30},Path(temp),verifier)

    def test_preserves_main_and_avoids_tail(self):
        item,clip=main();before=copy.deepcopy(clip)
        plan=self.run_plan(item,clip,[food()])
        self.assertTrue(plan['foodPlacements'])
        self.assertEqual(plan['selectedSpeech'],[before])
        for shot in plan['foodPlacements']:
            self.assertLessEqual(shot['at']+shot['end']-shot['start'],clip['speechEnd']-clip['start']-.19)

    def test_friend_and_mixed_not_covered(self):
        for role in ('friend','mixed'):
            item,clip=main();clip['voiceRole']=role
            self.assertFalse(self.run_plan(item,clip,[food()])['foodPlacements'])

    def test_audio_protection_splits_not_discards_whole_sentence(self):
        item,clip=main();clip['protected']=True
        item['voiceGuard']['protected']=[[3,5,'laugh']]
        for a,b in free_windows(clip,item,{}):
            self.assertFalse(a<5 and b>3)
        self.assertTrue(self.run_plan(item,clip,[food()])['foodPlacements'])

    def test_no_cross_restaurant(self):
        item,clip=main()
        self.assertFalse(same_scene(item,food(source='/batch/other/food.mov')))
        self.assertFalse(self.run_plan(item,clip,[food(source='/batch/other/food.mov')])['foodPlacements'])

    def test_no_wrong_dish(self):
        item,clip=main()
        self.assertFalse(self.run_plan(item,clip,[food('菠萝包')])['foodPlacements'])

    def test_generic_breakfast_can_use_same_scene_food(self):
        item,clip=main('广东人不能亏待自己的嘴','早餐很讲究.mov')
        self.assertTrue(self.run_plan(item,clip,[food()])['foodPlacements'])

    def test_name_handles_asr_error_without_changing_text(self):
        item,clip=main('婆豆腐你见过里面加马花','麻婆豆腐里面加脑花.mov')
        result=self.run_plan(item,clip,[food('麻婆豆腐')])
        self.assertTrue(result['foodPlacements'])
        self.assertEqual(result['selectedSpeech'][0]['text'],clip['text'])

    def test_pronoun_only_adjacent_unique_same_scene(self):
        item,clip=main('这个很香','generic.mov')
        self.assertEqual(intent(clip,item,[food()],(item,{'虾饺'}))[0],{'虾饺'})
        self.assertFalse(intent(clip,item,[food()],(item,{'虾饺','菠萝包'}))[0])
        other=dict(item,source='/batch/other/main.mov')
        self.assertFalse(intent(clip,item,[food()],(other,{'虾饺'}))[0])

    def test_failed_visual_never_inserted(self):
        item,clip=main()
        plan=self.run_plan(item,clip,[food()],lambda *args:(False,{'reason':'unclear'}))
        self.assertFalse(plan['foodPlacements'])

    def test_exception_fails_closed(self):
        item,clip=main()
        def broken(*args): raise ValueError('model unavailable')
        with self.assertRaisesRegex(RuntimeError, '不能把零插入标为完成'):
            self.run_plan(item,clip,[food()],broken)

    def test_model_reaction_causes_shift_within_speech(self):
        item,clip=main();seen=[]
        def verify(c,*args):
            seen.append(c['at'])
            return (len(seen)>1, {'main':{'frames':[]}})
        plan=self.run_plan(item,clip,[food()],verify)
        self.assertEqual(len(seen),2)
        self.assertGreater(plan['foodPlacements'][0]['at'],seen[0])

    def test_inconsistent_next_frame_rejected(self):
        item,clip=main();candidate=food()
        candidate['foodShots'][0]['nextTags']=['菠萝包']
        self.assertFalse(self.run_plan(item,clip,[candidate])['foodPlacements'])

    def test_environment_same_scene_and_silent_v2_contract(self):
        item,clip=main('我们在这里俯瞰城市','俯瞰城市.mov')
        candidate=food(source='/batch/shop/环境空镜/city.mov')
        candidate.update(vision={'kind':'环境','summary':'窗外城市天际线'},foodShots=[],
                         frames=[{'at':1,'file':'a.jpg'},{'at':8,'file':'b.jpg'}])
        result=self.run_plan(item,clip,[candidate])
        self.assertEqual(result['foodPlacements'][0]['brollKind'],'environment')

    def test_historical_named_environment_folder_remains_candidate(self):
        item,clip=main('这里的人都是穿短裤短袖的','人物.mov')
        candidate=food(source='/batch/shop/路人穿短袖、短裤/2026.mov')
        candidate.update(vision={'kind':'环境','summary':'人行道上穿短袖的行人'},foodShots=[],
            frames=[{'at':1,'file':'a.jpg'},{'at':8,'file':'b.jpg'}])
        result=self.run_plan(item,clip,[candidate])
        self.assertTrue(result['foodPlacements'])
        self.assertEqual(result['foodPlacements'][0]['dish'],'日常穿着')

    def test_role_folders_are_same_scene_but_store_names_are_not_stripped(self):
        item,clip=main()
        item['source']='/batch/示例烤肉/人物/host.mov'
        self.assertTrue(same_scene(item,food(source='/batch/示例烤肉/菜品/closeup.mov')))
        self.assertTrue(same_scene(item,food(source='/batch/示例烤肉/后厨/cook.mov')))
        self.assertFalse(same_scene(item,food(source='/batch/别家烧烤/菜品/closeup.mov')))
        self.assertFalse(same_scene(item,food(source='/batch/示例烤肉/第二场/菜品/closeup.mov')))
        self.assertFalse(same_scene(item,food(source='/batch/人物广场/菜品/closeup.mov')))

    def test_existing_chapter_to_named_broll_subfolders_remain_same_scene(self):
        # Historical shoot folder conventions, not only idealized labels.
        for main_path,broll_path in [
            ('/batch/4/人物.mov','/batch/4/示例餐厅菜品/特写.mov'),
            ('/batch/5/烧鹅.mov','/batch/5/菜品空镜/烧鹅.mov'),
            ('/batch/2/人物.mov','/batch/2/路人穿短袖、短裤/路人.mov')]:
            item,clip=main();item['source']=main_path
            self.assertTrue(same_scene(item,food(source=broll_path)))

    def unlisted_food(self):
        result=food(source='/batch/shop/菜品/羊肉串.mov')
        result.update(foodShots=[], frames=[{'at':1,'file':'a.jpg'},{'at':7,'file':'b.jpg'}],
            foodIndex={'frames':[{'index':0,'label':'其他食物','visible':True,'confidence':.9},
                                 {'index':1,'label':'其他食物','visible':True,'confidence':.9}]})
        return result

    def test_food_outside_old_dish_vocabulary_reaches_verification(self):
        item,clip=main('羊肉串肉很嫩，这是招牌','烧烤介绍.mov')
        seen=[]
        def verify(candidate,*args):
            seen.append(candidate)
            return True,{}
        result=self.run_plan(item,clip,[self.unlisted_food()],verify)
        self.assertTrue(seen)
        self.assertTrue(result['foodPlacements'])
        self.assertEqual(result['selectedSpeech'][0]['text'],clip['text'])

    def test_unlisted_food_rejection_is_not_overridden_by_generic_topic(self):
        item,clip=main('这串羊肉非常嫩','烧烤介绍.mov')
        result=self.run_plan(item,clip,[self.unlisted_food()],lambda *args:(False,{'reason':'牛肉不是羊肉'}))
        self.assertFalse(result['foodPlacements'])

    def test_unlisted_food_prompt_retains_specific_dialogue_and_source_metadata(self):
        item,clip=main('这串羊肉非常嫩','烧烤介绍.mov')
        candidate={'source':'/batch/shop/菜品/羊肉串.mov','start':1,'end':3,'at':2,
                   'brollKind':'food','dish':'同场餐食'}
        calls=[]
        def infer(source,times,model,prompt,choices,cache):
            calls.append(prompt)
            return {'frames':[{'index':i,'label':'usable' if len(calls)==1 else 'talk','confidence':.9}for i in range(3)]}
        with patch('broll.infer_frames',side_effect=infer):
            passed,_=verify_insert(candidate,clip,item,'local')
        self.assertTrue(passed)
        self.assertIn(clip['text'],calls[0])
        self.assertIn('羊肉串',calls[0])
        self.assertIn('羊肉不能用牛肉替代',calls[0])
        self.assertIn('选unclear',calls[0])

    def test_not_visible_food_index_cannot_supply_insert_candidate(self):
        item,clip=main('这串羊肉非常嫩','烧烤介绍.mov')
        candidate=self.unlisted_food()
        for row in candidate['foodIndex']['frames']:row['visible']=False
        self.assertFalse(self.run_plan(item,clip,[candidate])['foodPlacements'])

    def test_reviewed_exclusion_not_recovered_as_unlabelled_food(self):
        item,clip=main('这串羊肉非常嫩','烧烤介绍.mov')
        candidate=self.unlisted_food();candidate['foodReview']={'reviewedBy':'editor'}
        self.assertFalse(self.run_plan(item,clip,[candidate])['foodPlacements'])

    def test_chef_cooking_and_server_delivery_are_supported_topics(self):
        for dialogue,folder,summary,topic in [
                ('这些肉串都是师傅现烤的','后厨','厨师在烤架上翻动肉串','后厨制作'),
                ('我们点的这盘刚上桌','上菜','服务员端着一盘菜摆到桌上','上菜过程')]:
            item,clip=main(dialogue,'介绍.mov')
            candidate=food(source=f'/batch/shop/{folder}/shot.mov')
            candidate.update(vision={'kind':'人物','summary':summary},foodShots=[],
                frames=[{'at':1,'file':'a.jpg'},{'at':7,'file':'b.jpg'}])
            result=self.run_plan(item,clip,[candidate])
            self.assertTrue(result['foodPlacements'])
            self.assertEqual(result['foodPlacements'][0]['dish'],topic)

    def test_visual_cooking_and_serving_work_without_named_files_or_folders(self):
        for dialogue,kind,summary,topic in [
                ('师傅现场制作，每串都是现烤','人物','厨师在烤架上翻动肉串','后厨制作'),
                ('这盘刚上桌，还是热的','人物','服务员端着菜摆到桌上','上菜过程'),
                ('师傅正在后厨现烤','食物','烤架上的肉串正在制作','后厨制作'),
                ('我们来看看这家餐厅的环境','环境','餐厅内部的用餐区和餐桌','餐厅环境')]:
            item,clip=main(dialogue,'IMG_1000.mov')
            candidate=food(source='/batch/shop/IMG_1001.mov')
            candidate.update(vision={'kind':kind,'summary':summary},foodShots=[],
                frames=[{'at':1,'file':'a.jpg'},{'at':7,'file':'b.jpg'}])
            result=self.run_plan(item,clip,[candidate])
            self.assertTrue(result['foodPlacements'])
            self.assertEqual(result['foodPlacements'][0]['dish'],topic)

    def test_generic_person_talking_or_tasting_not_used_as_service_action(self):
        for summary in ['男子介绍手上的烧烤','厨师站在后厨讲话',
                        '男子讲解烤制肉串的步骤','女子端着盘子试吃肉串']:
            item,clip=main('师傅现烤的羊肉串刚上桌','IMG_1000.mov')
            candidate=food(source='/batch/shop/IMG_1001.mov')
            candidate.update(vision={'kind':'人物','summary':summary},foodShots=[],
                frames=[{'at':1,'file':'a.jpg'},{'at':7,'file':'b.jpg'}])
            self.assertFalse(self.run_plan(item,clip,[candidate])['foodPlacements'])

    def test_cooking_does_not_use_ordinary_talent_performance(self):
        item,clip=main('师傅现烤的羊肉串','介绍.mov')
        candidate=food(source='/batch/shop/人物/试吃羊肉串.mov')
        candidate.update(vision={'kind':'人物','summary':'男子介绍手上的烧烤'},foodShots=[],
            frames=[{'at':1,'file':'a.jpg'},{'at':7,'file':'b.jpg'}])
        self.assertFalse(self.run_plan(item,clip,[candidate])['foodPlacements'])

    def test_preserve_script_insert_and_main_required_range(self):
        item,clip=main()
        existing={'assetId':'script','source':'/batch/shop/script.mov','at':0,'start':0,'end':2,
                  'brollKind':'script','dish':'脚本必需镜头'}
        plan={'selectedSpeech':[clip],'foodPlacements':[existing],'duration':11}
        before=copy.deepcopy(plan)
        with tempfile.TemporaryDirectory() as tmp:
            result=enhance(plan,[item,food()],{'visionModel':'local'},Path(tmp),
                verifier=lambda *args:(True,{}),preserve_existing=True,protected_main_ranges=[(6,9)])
        self.assertEqual(result['foodPlacements'][0],existing)
        self.assertEqual(result['selectedSpeech'],before['selectedSpeech'])
        self.assertEqual(result['brollSummary']['preservedCount'],1)
        self.assertGreater(result['brollSummary']['newInsertCount'],0)
        for shot in result['foodPlacements'][1:]:
            a,b=shot['at'],shot['at']+shot['end']-shot['start']
            self.assertGreaterEqual(a,2)
            self.assertFalse(a<9 and b>6)

    def test_existing_source_window_is_not_reused(self):
        item,clip=main();candidate=food()
        clip.update(end=22,speechEnd=20)
        existing={'assetId':'food','source':candidate['source'],'at':0,'start':2,'end':6,
                  'brollKind':'script','dish':'脚本镜头'}
        plan={'selectedSpeech':[clip],'foodPlacements':[existing],'duration':21}
        with tempfile.TemporaryDirectory() as tmp:
            result=enhance(plan,[item,candidate],{'visionModel':'local'},Path(tmp),
                verifier=lambda *args:(True,{}),preserve_existing=True)
            report=read(Path(tmp)/'broll-review.json')
        self.assertEqual(result['foodPlacements'],[existing])
        self.assertEqual(report['blocks'][0]['reason'],'no-unused-source-window')

    def test_required_shots_consume_optional_budget_without_being_removed(self):
        item,clip=main()
        existing={'assetId':'script','source':'/batch/shop/script.mov','at':0,'start':0,'end':6,
                  'brollKind':'script','dish':'脚本必需镜头'}
        plan={'selectedSpeech':[clip],'foodPlacements':[existing],'duration':11}
        verifier=Mock(return_value=(True,{}))
        with tempfile.TemporaryDirectory() as tmp:
            result=enhance(plan,[item,food()],{'visionModel':'local'},Path(tmp),
                verifier=verifier,preserve_existing=True)
        verifier.assert_not_called()
        self.assertEqual(result['foodPlacements'],[existing])
        self.assertEqual(result['brollSummary']['newInsertCount'],0)

    def test_preserved_script_shot_does_not_mask_optional_model_failure(self):
        item,clip=main()
        existing={'assetId':'script','source':'/batch/shop/script.mov','at':0,'start':0,'end':2,
                  'brollKind':'script','dish':'脚本镜头'}
        plan={'selectedSpeech':[clip],'foodPlacements':[existing],'duration':11}
        def broken(*args):raise RuntimeError('model unavailable')
        with tempfile.TemporaryDirectory() as tmp:
            result=enhance(plan,[item,food()],{'visionModel':'local'},Path(tmp),
                verifier=broken,preserve_existing=True)
            report=read(Path(tmp)/'broll-review.json')
        self.assertEqual(result['foodPlacements'],[existing])
        self.assertFalse(report['complete'])
        self.assertFalse(result['brollSummary']['optionalReviewComplete'])
        self.assertGreater(result['brollSummary']['reviewErrorCount'],0)
        self.assertTrue(result['brollSummary']['warnings'])

    def test_missing_insert_reasons_are_reported_without_claiming_coverage(self):
        for dialogue,role,candidates,reason in [
            ('这句话与食物无关','unknown',[food()],'no-topic'),
            ('虾饺很好吃','friend',[food()],'no-safe-window'),
            ('虾饺很好吃','unknown',[],'no-candidates')]:
            item,clip=main(dialogue,'generic.mov');clip['voiceRole']=role
            plan={'selectedSpeech':[clip],'foodPlacements':[],'duration':11}
            with tempfile.TemporaryDirectory() as tmp:
                result=enhance(plan,[item,*candidates],{'visionModel':'local'},Path(tmp),verifier=lambda *args:(True,{}))
                report=read(Path(tmp)/'broll-review.json')
            self.assertEqual(report['blocks'][0]['reason'],reason)
            self.assertEqual(result['brollSummary']['blockStatusCounts'],{reason:1})
            self.assertEqual(result['brollSummary']['insertCount'],0)


if __name__=='__main__': unittest.main()
