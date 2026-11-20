"""Offline regressions: intact required dialogue, independent of handle mode."""
import copy
import unittest

from edit_policy import policy
from night_edit import match_norm, merge, select, text_coverage, _link_existing_quantity_reply
from voice_guard import guarded_speech


MODES=('strict','balanced','expressive')


def source(parts, name='a.mov', role='unknown'):
    segments=[{'start':a,'end':b,'text':text,'words':[]} for a,b,text in parts]
    duration=max(b for _,b,_ in parts)+2
    return {'assetId':name,'source':'/synthetic/'+name,'video':True,'audio':True,
            'duration':duration,'usableDuration':duration,'vision':{'kind':'人物'},
            'segments':segments,'voiceGuard':{'windows':[{'start':0,'end':duration,'role':role}],
                                            'protected':[]}}


def cores(rows):
    return sorted((r['assetId'],s['index'],s['start'],s['end'],s['text'])
                  for r in rows for s in r.get('sourceSegments',[]))


class CompleteScriptTests(unittest.TestCase):
    def test_script_search_admits_equipment_dialogue_without_inventing_identity(self):
        item=source([(2.76,5.56,'现在上厕所把电池给它扣了')],'equipment.mov')
        item['vision']={'kind':'设备','summary':'手持外骨骼设备'}
        item.pop('voiceGuard')
        original=copy.deepcopy(item)
        selected,cuts,_,missing,_=select([item],'助理：趁他上厕所，把电池给他扣了',30,'strict')
        self.assertEqual(len(selected),1);self.assertFalse(cuts)
        self.assertTrue(selected[0]['scriptVisualReview'])
        self.assertEqual(selected[0]['sourceVisualKind'],'设备')
        self.assertEqual(selected[0]['voiceRole'],'unknown')
        self.assertIn('声音身份',selected[0]['visualClassReview'])
        self.assertEqual(missing[0]['status'],'partial')
        self.assertEqual(item,original)

    def test_environment_spoken_scene_can_match_but_no_script_policy_is_unchanged(self):
        item=source([(.4,2.06,'你看山景就在那里')],'landscape.mov')
        item['vision']={'kind':'环境','summary':'游客在山路上行走'}
        selected,_,_,_,_=select([item],'助理：你看，山顶就在那里',30,'strict')
        self.assertTrue(selected[0]['scriptVisualReview'])
        with self.assertRaisesRegex(ValueError,'请导入'):
            select([item],'',30,'balanced')

    def test_explicit_broll_or_excluded_video_not_admitted_by_script_match(self):
        for excluded in ('folder','flag'):
            item=source([(1,3,'这个设备很好用')])
            if excluded=='folder':item['source']='/synthetic/空镜/a.mov'
            else:item['excluded']=True
            with self.subTest(excluded=excluded),self.assertRaises(ValueError):
                select([item],'达人：这个设备很好用',30,'strict')

    def test_nonhuman_script_candidates_keep_existing_director_guard(self):
        item=source([(1,3,'你可以这样说')],role='director')
        item['vision']={'kind':'设备','summary':'设备特写'}
        with self.assertRaises(ValueError):select([item],'达人：你可以这样说',30,'strict')

    def test_wider_candidate_search_does_not_keep_unrelated_equipment_audio(self):
        main=source([(1,3,'今天我们一起去爬山')])
        other=source([(1,3,'我的麦克风是不是坏了')],'other.mov')
        other['vision']={'kind':'设备','summary':'设备特写'}
        selected,_,_,_,_=select([main,other],'达人：今天我们一起去爬山',30,'strict')
        self.assertEqual({r['assetId'] for r in selected},{'a.mov'})

    def test_discarded_unreliable_reply_is_not_falsely_reported_as_already_selected(self):
        tokens=['那你不要担心','这个电池续航有','18','公里呢','18','公里呢','还有很久','来来要了病了要了病了']
        item=source([(1,10,' '.join(tokens))])
        item['segments'][0]['words']=[{'start':1+i,'end':2+i,'text':token} for i,token in enumerate(tokens)]
        first='助理：那你不要担心，这个电池续航有18km呢'
        baseline=select([item],first,30,'strict')[0]
        selected,_,_,missing,_=select([item],first+'\n达人：18km？！那还有很久，亚拉边拉！',30,'strict')
        self.assertEqual(cores(selected),cores(baseline))
        self.assertEqual([(r['start'],r['end'],r['text']) for r in selected],
                         [(r['start'],r['end'],r['text']) for r in baseline])
        reply=next(r for r in missing if r['target']==2)
        self.assertEqual(reply['status'],'missing')
        self.assertNotIn(2,selected[0]['scriptTargetIds'])
        self.assertNotIn('还有很久', ''.join(r['text'] for r in selected))

    def test_existing_association_requires_exact_quantity_and_other_keyword_group(self):
        row={'assetId':'a','source':'/synthetic/a.mov','start':0,'end':5,
             'words':[{'start':1,'end':2,'text':'180公里，还有很久'}]}
        for text in ('18km，还有很久亚拉边拉','180km，你觉得怎么样','去去去','耶'):
            missing=[{'target':1,'text':text,'status':'missing'}]
            _link_existing_quantity_reply([copy.deepcopy(row)],missing)
            self.assertEqual(missing[0]['status'],'missing')

    def test_content_policy_is_identical_across_all_pacing_modes(self):
        content=[{k:policy(mode)[k] for k in ('minScore','extraTextPenalty','requiredJoinGap','fallback')}
                 for mode in MODES]
        self.assertEqual(content[0],content[1]);self.assertEqual(content[0],content[2])

    def test_more_than_six_units_retains_product_sentence_tail_as_one_take(self):
        item=source([(4.58,7.38,'它能通过AI智能识别你的步态'),(7.38,8.08,'像'),
                     (8.08,8.74,'走平路'),(9.18,9.56,'上坡'),(9.96,10.4,'下坡'),
                     (10.4,10.96,'都'),(10.96,13.04,'可以提供相对应的助力')])
        script='达人：它能通过AI智能识别你的步态，像走平路、上坡、下坡，都可以提供对应的助力。'
        snapshots=[]
        for mode in MODES:
            with self.subTest(mode=mode):
                selected,_,_,missing,_=select([item],script,30,mode)
                self.assertFalse(missing);self.assertEqual(len(selected),1)
                self.assertEqual(selected[0]['speechEnd'],13.04)
                self.assertGreaterEqual(selected[0]['end'],13.04)
                self.assertIn('可以提供相对应的助力',selected[0]['text'])
                self.assertEqual(selected[0]['scriptStatus'],'complete')
                snapshots.append(cores(selected))
        self.assertEqual(snapshots[0],snapshots[1]);self.assertEqual(snapshots[0],snapshots[2])

    def test_required_long_sentence_is_not_capped_at_twenty_four_seconds(self):
        parts=[(i*4+1,i*4+4,text) for i,text in enumerate(
            ('我们先登上山顶','然后把西瓜切好','摆好摊位','给游客递瓜','扫码之后收款','最后清点收入','再把设备收好','一起下山吃饭'))]
        item=source(parts)
        selected,_,_,missing,_=select([item],'达人：'+''.join(t for _,_,t in parts),30,'strict')
        self.assertFalse(missing)
        self.assertEqual(selected[-1]['speechEnd'],parts[-1][1])
        self.assertEqual(len(cores(selected)),len(parts))

    def test_partial_candidate_is_not_reported_as_complete(self):
        item=source([(1,4,'它能识别步态，走平路上坡下坡都可以')])
        selected,_,_,missing,_=select([item],'达人：它能识别步态，走平路上坡下坡都可以提供对应的助力',30,'strict')
        self.assertEqual(selected[0]['scriptStatus'],'partial')
        self.assertEqual(missing[0]['status'],'partial')
        self.assertIn('提供对应的助力',''.join(missing[0]['missingText']))
        self.assertNotIn('提供对应的助力',selected[0]['text'])

    def test_numeric_unit_equivalence_is_retrieval_only(self):
        item=source([(1,4,'这个单电池续航有十八公里呢')]);original=copy.deepcopy(item)
        for mode in MODES:
            selected,_,_,missing,_=select([item],'达人：这个单电池续航有18km呢',30,mode)
            self.assertFalse(missing)
            self.assertEqual(selected[0]['text'],item['segments'][0]['text'])
        self.assertEqual(item,original)
        self.assertEqual(match_norm('1.5km'),match_norm('一点五公里'))
        self.assertNotEqual(match_norm('1.5km'),match_norm('15公里'))

    def test_numeric_conflict_is_never_complete(self):
        evidence=text_coverage('电池续航有18km呢','电池续航有八十公里呢')
        self.assertEqual(evidence['status'],'partial')
        self.assertEqual(evidence['missingQuantities'],['18公里'])
        self.assertEqual(text_coverage('18km','118公里')['status'],'partial')

    def test_all_modes_reject_zero_matches_without_whole_batch_fallback(self):
        for mode in MODES:
            with self.subTest(mode=mode),self.assertRaises(ValueError):
                select([source([(1,4,'鲜嫩多汁的羊肉串')])],'达人：今天去选秋季外套',30,mode)

    def test_no_script_is_blocked_including_legacy_modes(self):
        for mode in ('script','strict','balanced','expressive'):
            with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,'请导入'):
                select([source([(1,4,'今天一起去爬山')])],'',30,mode)

    def test_nonempty_unreadable_script_never_silently_keeps_all_speech(self):
        item=source([(1,3,'你可以这样说'),(4,6,'今天来到示例烤肉'),(7,10,'明天我们去别的地方玩')])
        for mode in MODES:
            with self.subTest(mode=mode),self.assertRaisesRegex(ValueError,'已导入脚本'):
                select([item],'标题：示例烤肉\n画面：食物特写与后厨制作',30,mode)

    def test_unquoted_name_script_selects_script_content_not_whole_batch(self):
        item=source([(1,3,'今天来到示例烤肉'),(4,6,'这个羊肉串真的好香'),(7,10,'明天我们去别的地方玩')])
        for mode in MODES:
            rows,_,_,missing,_=select([item],'小周：今天来到示例烤肉\n小周：这个羊肉串真的好香',30,mode)
            self.assertFalse(missing)
            self.assertNotIn('明天', ''.join(row['text'] for row in rows))

    def test_subframe_fragment_and_noninteger_boundaries_never_clip_speech(self):
        item=source([(1.019,1.026,'嗯'),(1.026,2.013,'这个很好')])
        for mode in MODES:
            kept,_,_=guarded_speech(item,item['duration'],30,mode)
            self.assertEqual(len(kept),2)
            for row in kept:
                self.assertLessEqual(row['start'],row['speechStart'])
                self.assertGreaterEqual(row['end'],row['speechEnd'])
            selected,_,_,_,_=select([item],'达人：嗯这个很好',30,mode)
            self.assertEqual(len(selected),1)
            self.assertEqual(len(cores(selected)),2)

    def test_same_original_asr_sentence_not_left_as_half_sentence(self):
        item=source([(1,2,'我们先来'),(2,3,'试穿这台设备'),(3,4,'然后开始爬山')])
        for segment in item['segments']:segment['id']='original-sentence'
        selected,_,_,_,_=select([item],'达人：试穿这台设备',30,'strict')
        self.assertEqual(len(selected),1)
        self.assertEqual(selected[0]['speechStart'],1);self.assertEqual(selected[0]['speechEnd'],4)
        self.assertEqual(len(cores(selected)),3)

    def test_duplicate_full_take_selected_once_not_concatenated(self):
        item=source([(1,3,'今天去爬山'),(3.4,5.4,'今天去爬山')])
        selected,_,_,missing,_=select([item],'达人：今天去爬山',30,'strict')
        self.assertFalse(missing);self.assertEqual(len(cores(selected)),1)

    def test_removed_director_command_is_a_real_take_barrier(self):
        item=source([(1,3,'今天我们'),(3.2,5.2,'再来一遍'),(5.3,7,'开始去爬山')])
        item['voiceGuard']['windows']=[{'start':1,'end':3,'role':'talent'},
            {'start':3.2,'end':5.2,'role':'director'},{'start':5.3,'end':7,'role':'talent'}]
        selected,cuts,_,missing,_=select([item],'达人：今天我们开始去爬山',30,'strict')
        self.assertEqual(len(cuts),1)
        self.assertTrue(missing)
        self.assertFalse(any(r['start']<3.2 and r['end']>5.2 for r in selected))

    def test_adjacent_targets_keep_all_coverage_and_provenance_when_merged(self):
        item=source([(1,2,'有AI吗'),(2,3,'当然有呀')])
        selected,_,_,missing,_=select([item],'达人：有AI吗\n朋友：当然有呀',30,'strict')
        self.assertFalse(missing)
        self.assertEqual(selected[0]['scriptTargetIds'],[1,2])
        self.assertEqual({e['target'] for e in selected[0]['scriptCoverage']},{1,2})
        self.assertEqual(len(cores(selected)),2)

    def test_merged_target_boundary_does_not_break_next_required_take(self):
        item=source([(1,2,'有AI吗'),(2,3,'它能识别'),(3.6,4.6,'每一步'),(5.1,6,'提供助力')])
        selected,_,_,missing,_=select([item],'达人：有AI吗\n朋友：它能识别每一步提供助力',30,'strict')
        self.assertFalse(missing);self.assertEqual(len(selected),1)
        self.assertEqual(len(cores(selected)),4)

    def test_identical_words_at_different_times_are_not_dropped_from_text(self):
        item=source([(1,2,'快点'),(2,3,'快点')])
        selected,_,_,missing,_=select([item],'达人：快点快点',30,'strict')
        self.assertFalse(missing)
        self.assertEqual(selected[0]['text'].replace(' ',''),'快点快点')


if __name__=='__main__':unittest.main()
