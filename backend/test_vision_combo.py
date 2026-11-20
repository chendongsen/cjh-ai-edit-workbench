import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from common import read
from vision_combo import face_decision, face_ranges, enabled, create, Ensemble, contrast_allowed
from broll import enhance, proposals, subtract_ranges
from test_broll import main, food


class ComboTests(unittest.TestCase):
    def row(self, faces=1, **scores):return {'faces':faces,'scores':scores}

    def test_ordinary_speech_and_blink_not_reaction(self):
        self.assertTrue(face_decision([self.row(jawOpen=.05,eyeBlinkLeft=0),
            self.row(jawOpen=.9,eyeBlinkLeft=.9)])[0])

    def test_smile_protected(self):
        self.assertFalse(face_decision([self.row(mouthSmileLeft=.8,mouthSmileRight=.7)])[0])

    def test_menu_requires_positive_margin_over_wall_art(self):
        self.assertFalse(contrast_allowed('菜单',.39,[.42,.40]))
        self.assertFalse(contrast_allowed('菜单',.42,[.41]))
        self.assertTrue(contrast_allowed('菜单',.47,[.39,.40]))

    def test_brow_change_protected(self):
        self.assertFalse(face_decision([self.row(browInnerUp=.1),self.row(browInnerUp=.6)])[0])

    def test_no_face_and_multiple_faces_uncertain(self):
        for faces in (0,2,3):self.assertFalse(face_decision([self.row(faces)])[0])
        self.assertFalse(face_decision([])[0])

    def test_short_expression_only_excludes_local_window(self):
        rows=[dict(self.row(),at=1),dict(self.row(mouthSmileLeft=.8,mouthSmileRight=.8),at=2),dict(self.row(),at=3)]
        protected=face_ranges(rows,1,4)
        self.assertEqual(protected,[[1.75,2.3]])
        free=subtract_ranges([(0,5)],protected)
        self.assertEqual(free,[(0,1.75),(2.3,5)])

    def test_explicit_disable_overrides_local_default(self):
        with patch('vision_combo.read',return_value={'enabled':True}):
            self.assertTrue(enabled({}))
            self.assertFalse(enabled({'visionCombo':False}))

    def test_model_failure_visible_fallback(self):
        with patch('vision_combo.Ensemble',side_effect=RuntimeError('missing model')):
            engine,report=create({'visionCombo':True})
        self.assertIsNone(engine)
        self.assertEqual(report['status'],'fallback')
        self.assertIn('missing model',report['warnings'][0])

    def test_retrieval_cannot_bypass_qwen(self):
        class Fake:
            def rank(self,block,options,*args):return [dict(o,retrievalScore=.99) for o in options]
            def check_face(self,*args):return True,{'passed':True}
            def close(self):pass
        item,clip=main();plan={'selectedSpeech':[clip],'foodPlacements':[],'duration':11}
        before=copy.deepcopy(plan['selectedSpeech'])
        with tempfile.TemporaryDirectory() as tmp, patch('vision_combo.create',return_value=(Fake(),{'status':'active','warnings':[]})), patch('broll.verify_insert',return_value=(False,{'reason':'wrong dish'})) as qwen:
            result=enhance(plan,[item,food()],{'visionModel':'local'},Path(tmp))
            self.assertTrue(qwen.called)
            self.assertFalse(result['foodPlacements'])
            self.assertEqual(result['selectedSpeech'],before)
            self.assertEqual(read(Path(tmp)/'vision-combo-report.json')['status'],'active')

    def test_face_failure_preserves_performance(self):
        class Fake:
            def rank(self,block,options,*args):return options
            def check_face(self,*args):raise RuntimeError('face engine failed')
            def close(self):pass
        item,clip=main();plan={'selectedSpeech':[clip],'foodPlacements':[],'duration':11}
        with tempfile.TemporaryDirectory() as tmp, patch('vision_combo.create',return_value=(Fake(),{'status':'active','warnings':[]})), patch('broll.verify_insert') as qwen:
            with self.assertRaisesRegex(RuntimeError,'face engine failed'):
                enhance(plan,[item,food()],{'visionModel':'local'},Path(tmp))
            qwen.assert_not_called()

    def test_retrieval_recovers_unlabelled_but_not_conflicting_or_other_scene(self):
        import numpy as np
        engine=Ensemble.__new__(Ensemble)
        engine.text=lambda _:np.array([1.,0.])
        engine.embed=lambda _:np.array([.9,.1])
        engine.report={'rankedBlocks':0,'retrievalCandidates':0}
        item,clip=main()
        good=food(source='/batch/shop/菜品/未命名.mov')
        good.update(foodShots=[],frames=[{'at':1,'file':'x.jpg'},{'at':6,'file':'y.jpg'}])
        wrong=dict(good,assetId='wrong',source='/batch/shop/菜品/菠萝包.mov')
        other=dict(good,assetId='other',source='/batch/other/菜品/虾饺.mov')
        reviewed=dict(good,assetId='reviewed',foodReview={'reviewedBy':'editor'})
        plan={'selectedSpeech':[clip]}
        block=next(proposals(plan,[item,good]))
        options=engine.rank(block,[],[item,good,wrong,other,reviewed],{},30)
        self.assertTrue(options)
        self.assertEqual({o['item']['assetId'] for o in options},{'food'})
        self.assertTrue(all(o['confidence']==0 for o in options))
        self.assertTrue(all('不是菜名确认' in o['basis'] for o in options))

    def test_mixed_dish_and_menu_options_have_independent_queries(self):
        import numpy as np
        engine=Ensemble.__new__(Ensemble)
        def text(query):
            if '菜单' in query:return np.array([0.,1.])
            if '虾饺' in query:return np.array([1.,0.])
            return np.array([-1.,-1.])  # the wall-art negative queries
        engine.text=text
        engine.embed=lambda frame:np.array([0.,1.]) if str(frame).endswith('menu.jpg') else np.array([1.,0.])
        engine.report={'rankedBlocks':0,'retrievalCandidates':0}
        item,clip=main('菜单上的虾饺多少钱')
        candidate=food()
        options=[dict(item=candidate,start=1,maximum=6,dish='菜单',brollKind='environment',
                      confidence=.7,frame='menu.jpg',basis='menu candidate'),
                 dict(item=candidate,start=7,maximum=10,dish='虾饺',brollKind='food',
                      confidence=.9,frame='food.jpg',basis='food candidate')]
        block=next(proposals({'selectedSpeech':[clip]},[item,candidate]))
        result=engine.rank(block,options,[item,candidate],{},30)
        self.assertEqual({o['dish']:o['retrievalScore']for o in result},{'菜单':1.,'虾饺':1.})
        self.assertNotIn('contrastRejected',engine.report)

    def test_cooking_option_not_scored_as_generic_food(self):
        import numpy as np
        engine=Ensemble.__new__(Ensemble)
        engine.text=lambda query:np.array([0.,1.]) if '后厨制作' in query else np.array([1.,0.])
        engine.embed=lambda frame:np.array([0.,1.])
        engine.report={'rankedBlocks':0,'retrievalCandidates':0}
        item,clip=main('师傅正在现烤羊肉串','介绍.mov')
        candidate=food()
        options=[dict(item=candidate,start=1,maximum=6,dish='后厨制作',brollKind='environment',
                      confidence=.7,frame='cooking.jpg',basis='cooking candidate')]
        block=next(proposals({'selectedSpeech':[clip]},[item,candidate]))
        result=engine.rank(block,options,[item,candidate],{},30)
        self.assertEqual(result[0]['dish'],'后厨制作')
        self.assertEqual(result[0]['retrievalScore'],1.)


if __name__=='__main__':unittest.main()
