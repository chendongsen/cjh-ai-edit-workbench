"""New retrieval candidates must pass all existing editorial gates."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import broll
from test_broll import main, food

class SemanticPipelineTests(unittest.TestCase):
    def candidate(self,item):
        return dict(item=item,start=2.,maximum=6.,dish='与口播对应的补充画面',brollKind='environment',
            confidence=0.,retrievalScore=.98,frame=None,basis='视频检索候选，未确认',semanticRetrieval=True,
            retrievalQuery='正在端菜上桌',retrievalEvidence={'sourceStart':2.,'sourceEnd':6.})

    def plan(self,passed,enabled=True):
        item,clip=main('服务员把热菜端上桌','人物.mov')
        insert=food(source='/batch/shop/IMG_0001.mov');insert.update(vision={'kind':'人物'},foodShots=[])
        plan={'selectedSpeech':[clip],'foodPlacements':[],'duration':11.};seen=[]
        def verify(candidate,*args):
            seen.append(candidate);return passed,{'reason':'实际视觉复核'}
        with tempfile.TemporaryDirectory() as tmp, patch('video_retrieval.enabled',return_value=enabled), patch('video_retrieval.retrieved_options',return_value=[self.candidate(insert)]) as query, patch('broll.options_for',return_value=[]):
            result=broll.enhance(plan,[item,insert],{'visionModel':'local','fps':30},Path(tmp),verifier=verify)
        return result,seen,query,clip

    def test_high_similarity_does_not_override_visual_rejection(self):
        result,seen,_,_=self.plan(False)
        self.assertTrue(seen);self.assertEqual(result['foodPlacements'],[])

    def test_verified_retrieved_insert_preserves_main_and_provenance(self):
        result,seen,_,clip=self.plan(True)
        self.assertTrue(result['foodPlacements']);self.assertEqual(result['selectedSpeech'],[clip])
        shot=result['foodPlacements'][0]
        self.assertTrue(shot['semanticRetrieval'])
        self.assertEqual(shot['retrievalEvidence'],{'sourceStart':2.,'sourceEnd':6.})
        self.assertLessEqual(shot['at']+shot['end']-shot['start'],clip['speechEnd']-clip['start']-.19)

    def test_explicit_disabled_does_not_use_an_old_index(self):
        result,seen,query,_=self.plan(True,False)
        query.assert_not_called();self.assertEqual(seen,[]);self.assertEqual(result['foodPlacements'],[])

    def test_semantic_environment_verification_receives_full_dialogue(self):
        item,clip=main('这里能看到珠江和广州塔','人物.mov')
        candidate=dict(assetId='insert',source='/batch/shop/IMG_001.mov',start=2.,end=4.,at=1.,
            dish='与口播对应的补充画面',brollKind='environment',semanticRetrieval=True)
        calls=[]
        def infer(source,times,model,prompt,labels,*args):
            calls.append(prompt);return {'frames':[{'label':labels[0],'confidence':.99} for _ in times]}
        with patch('broll.infer_frames',side_effect=infer):ok,_=broll.verify_insert(candidate,clip,item,'local')
        self.assertTrue(ok);self.assertIn(clip['text'],calls[0]);self.assertIn('检索分数不是画面正确的证据',calls[0])

if __name__=='__main__':unittest.main()
