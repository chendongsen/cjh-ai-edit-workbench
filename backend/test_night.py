import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from common import write,read
from voice_guard import classify,guarded_speech,interval_role,fallback_guard,choose_profile
from night_edit import merge,targets,select,review_report
from workflow import automatic_plan
from server import safe_resume


def fixture(text='你可以这样说',role='director',protected=None):
    return {'assetId':'a','source':'/batch/a.mov','video':True,'audio':True,'duration':10,'usableDuration':10,
            'vision':{'kind':'人物'},'segments':[{'start':2,'end':4,'text':text,'words':[]}],
            'voiceGuard':{'windows':[{'start':1,'end':5,'role':role}], 'protected':protected or []}}


class NightTests(unittest.TestCase):
    def test_unknown_instruction_not_voice_deleted(self):
        kept,cuts,_=guarded_speech(fixture(role='unknown'),10,30)
        self.assertEqual(len(cuts),0);self.assertEqual(len(kept),1)

    def test_confident_director_instruction_removed(self):
        kept,cuts,_=guarded_speech(fixture(),10,30)
        self.assertEqual(len(cuts),1);self.assertEqual(kept,[])

    def test_director_normal_scene_dialogue_kept(self):
        kept,cuts,_=guarded_speech(fixture('这是免税不是免费'),10,30)
        self.assertEqual(len(kept),1);self.assertFalse(cuts)

    def test_friend_and_talent_instructions_stay(self):
        for role in ['friend','talent','mixed']:
            self.assertFalse(guarded_speech(fixture(role=role),10,30)[1])

    def test_laugh_protection_overrules_director(self):
        item=fixture(protected=[[1,6,'laughter']])
        kept,cuts,_=guarded_speech(item,10,30)
        self.assertFalse(cuts)
        self.assertTrue(kept[0]['protected'])
        self.assertGreaterEqual(kept[0]['end'],4)
        self.assertLessEqual(kept[0]['end'],4.8)

    def test_short_director_kept(self):
        item=fixture();item['segments'][0]['end']=3
        self.assertFalse(guarded_speech(item,10,30)[1])

    def test_handles_do_not_cross_removed_instruction(self):
        item=fixture();item['segments'].insert(0,{'start':.5,'end':1.7,'text':'哈哈','words':[]})
        kept,cuts,_=guarded_speech(item,10,30)
        self.assertEqual(len(cuts),1);self.assertLessEqual(kept[0]['end'],2)

    def test_mixed_windows_not_identified_as_one_person(self):
        self.assertEqual(interval_role(0,2,[{'start':0,'end':2,'role':'director'},
                                           {'start':1,'end':3,'role':'talent'}]),'mixed')

    def test_profile_margin_and_unknown(self):
        p={'references':[{'role':'director','embedding':[1,0]}, {'role':'talent','embedding':[.95,.05]}]}
        self.assertEqual(classify([1,0],p)[0],'unknown')
        self.assertEqual(classify([1,0],None)[0],'unknown')

    def test_profile_not_applied_to_other_directory(self):
        with patch('voice_guard.confirmed_profiles',return_value=[{'projectId':'old','scope':'/batch'}]):
            self.assertIsNone(choose_profile([{'path':'/different/a.mov'}],'new'))

    def test_fallback_never_deletes_by_voice(self):
        item=fallback_guard([fixture()])[0]
        self.assertFalse(guarded_speech(item,10,30)[1])

    def test_script_old_timecodes_ignored(self):
        script='C1｜角色意图：达人｜台词意图：特别好吃\n素材：a.mov｜源 900—999s'
        selected,_,_,missing,_=select([fixture('特别好吃','talent')],script,30)
        self.assertFalse(missing);self.assertEqual(selected[0]['speechStart'],2)
        self.assertLess(selected[0]['end'],10)

    def test_unknown_voice_can_match_script_without_claiming_identity(self):
        selected,*_=select([fixture('哈哈好吃','unknown')],'达人：哈哈好吃',30)
        self.assertEqual(len(selected),1)
        self.assertEqual(selected[0]['voiceRole'],'unknown')

    def test_merging_does_not_repeat_overlapping_handle(self):
        a={'assetId':'a','start':0,'end':3,'text':'a','words':[],'speechEnd':2}
        b=dict(a,start=2,end=4,text='b',speechEnd=3)
        result=merge([a,b]);self.assertEqual(len(result),1);self.assertEqual(result[0]['end'],4)

    def test_known_director_demonstration_not_preferred_for_script(self):
        item=fixture('特别好吃','director')
        item['segments'].append({'start':6,'end':8,'text':'特别好吃','words':[]})
        item['voiceGuard']['windows'].append({'start':5,'end':9,'role':'talent'})
        s,*_=select([item],'达人：特别好吃',30)
        self.assertGreater(s[0]['start'],5)

    def test_food_does_not_cover_protected_reaction(self):
        item=fixture('这个虾饺好好吃','talent',[[3,7,'reaction']])
        selected,*_=select([item],'达人：这个虾饺好好吃',30)
        food={'source':'/batch/food.mov','assetId':'food','video':True,'duration':10,'usableDuration':10,
              'vision':{'kind':'食物','dishCandidates':['虾饺']},'foodShots':[{'at':0,'tags':['虾饺'],'confidence':1}]}
        plan=automatic_plan([item,food],'test',30,selected)
        self.assertFalse(plan['foodPlacements'])

    def test_nle_incomplete_receipt_prevents_automatic_retry(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);self.assertTrue(safe_resume(root))
            (root/'resolve').mkdir();write(root/'resolve/started.json',{})
            self.assertFalse(safe_resume(root))
            write(root/'resolve/result.json',{'saved':True});self.assertTrue(safe_resume(root))
            (root/'jianying').mkdir();write(root/'jianying/started.json',{})
            self.assertFalse(safe_resume(root))
            write(root/'jianying/result.json',{'saved':True});self.assertTrue(safe_resume(root))


if __name__=='__main__':unittest.main()
