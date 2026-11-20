import unittest
from editorial import clean_speech, dish_tags
from workflow import automatic_plan


def segment(tokens, start=0):
    words = [{'text': text, 'start': start+i*.5, 'end': start+(i+1)*.5} for i, text in enumerate(tokens)]
    return {'start': start, 'end': words[-1]['end'], 'text': ''.join(tokens), 'words': words}


def asset(name, segments, kind='人物', summary='一位女士在餐厅用餐'):
    return {'assetId': name, 'source': '/batch/4/'+name, 'video': True, 'duration': 30,
            'usableDuration': 30, 'segments': segments, 'vision': {'kind': kind, 'summary': summary}}


class EditorialTests(unittest.TestCase):
    def test_countdown_and_talent_in_same_segment(self):
        a = asset('main.mov', [segment(['3','2','1','开始','这个','虾饺','很好吃'])])
        kept, cuts, reviews = clean_speech(a, 30, 30)
        self.assertEqual(cuts[0]['text'], '321开始')
        self.assertEqual(kept[0]['text'], '这个虾饺很好吃')
        self.assertGreaterEqual(kept[0]['start'], cuts[0]['end'])

    def test_director_and_reply_in_same_segment(self):
        a = asset('main.mov', [segment(['再','夹','一下','鲍鱼','这个是','鲍鱼','很好吃'])])
        kept, cuts, _ = clean_speech(a, 30, 30)
        self.assertEqual(cuts[0]['text'], '再夹一下鲍鱼')
        self.assertEqual(kept[0]['text'], '这个是鲍鱼很好吃')

    def test_padding_merge_never_reintroduces_command(self):
        a = asset('main.mov', [segment(['好吃'],0), segment(['CUT'],.55), segment(['真的'],1.1)])
        kept, cuts, _ = clean_speech(a,30,30)
        self.assertEqual(len(kept),2)
        for s in kept:
            self.assertFalse(any(s['start'] < c['end'] and s['end'] > c['start'] for c in cuts))

    def test_no_words_mixed_sentence_preserved(self):
        a = asset('main.mov',[{'start':0,'end':5,'text':'三二一这个虾饺好吃'}])
        kept,cuts,reviews=clean_speech(a,30,30)
        self.assertEqual(len(cuts),0)
        self.assertEqual(len(kept),1)
        self.assertGreater(len(reviews),0)

    def test_scripted_second_speaker_and_reactions_not_deleted(self):
        texts=['这是免税不是免费啊','那再来包纸巾吧','表情太可爱了','你看这个很好吃','哇好吃']
        a=asset('main.mov',[segment([t],i*2) for i,t in enumerate(texts)])
        kept,cuts,_=clean_speech(a,30,30)
        self.assertEqual(cuts,[])
        self.assertTrue(all(t in ''.join(s['text'] for s in kept) for t in texts))

    def test_semantic_person_recovered_but_broll_speech_not_main(self):
        a=asset('吃虾饺.mov',[segment(['这个虾饺很好吃'])],kind='环境')
        b=asset('菜品/food.mov',[segment(['三二一'])],kind='食物')
        plan=automatic_plan([a,b],'test')
        self.assertEqual(len(plan['recoveredMainSources']),1)
        self.assertTrue(all(s['assetId']==a['assetId'] for s in plan['selectedSpeech']))

    def test_not_equating_different_ingredients(self):
        self.assertNotIn('烧鹅', dish_tags('烤鸭'))
        self.assertNotIn('脑花', dish_tags('豆腐煲'))
        self.assertNotIn('肠粉', dish_tags('红米肠粉'))

    def test_food_insert_uses_visible_frame_and_no_duplicate_ranges(self):
        a=asset('吃虾饺.mov',[segment(['这个','虾饺','非常','非常','好吃','味道','特别','好','真的','很鲜'],i*10) for i in range(3)])
        b=asset('菜品/food.mov',[],kind='食物',summary='虾饺特写')
        b['foodShots']=[{'at':10,'tags':['虾饺'],'confidence':.9},{'at':20,'tags':['虾饺'],'confidence':.9}]
        plan=automatic_plan([a,b],'test')
        foods=plan['foodPlacements']
        self.assertEqual(len(foods),2)
        self.assertTrue(all(x['start']>8 for x in foods))
        self.assertFalse(foods[0]['start']<foods[1]['end'] and foods[0]['end']>foods[1]['start'])
        self.assertTrue(all('词级时间' in x['matchBasis'] for x in foods))

    def test_empty_new_index_does_not_fall_back_to_bad_old_tags(self):
        a=asset('吃虾饺.mov',[segment(['这个虾饺']*10)])
        b=asset('菜品/food.mov',[],kind='食物',summary='虾饺')
        b['vision']['dishCandidates']=['虾饺']
        b['foodShots']=[]
        self.assertEqual(automatic_plan([a,b],'test')['foodPlacements'],[])

    def test_visible_sample_never_backtracks_into_empty_table(self):
        a=asset('吃虾饺.mov',[segment(['这个虾饺很好吃']*10)])
        b=asset('菜品/food.mov',[],kind='食物',summary='虾饺')
        b['foodShots']=[{'at':8.2,'tags':['虾饺'],'confidence':.9}]
        x=automatic_plan([a,b],'test')['foodPlacements'][0]
        self.assertGreaterEqual(x['start'],8.2)

    def test_changing_dish_sample_not_assumed_safe(self):
        a=asset('吃虾饺.mov',[segment(['这个虾饺很好吃']*10)])
        b=asset('菜品/food.mov',[],kind='食物',summary='虾饺')
        b['foodShots']=[{'at':2,'tags':['虾饺'],'confidence':.9,'nextAt':6,'nextTags':['菠萝包']}]
        self.assertEqual(automatic_plan([a,b],'test')['foodPlacements'],[])


if __name__ == '__main__':
    unittest.main()
