"""Behavior regressions for script-only complete sentences in long ASR blocks."""
import copy
import unittest
from speech_sentences import sentence_spans
from night_edit import select, merge
from reaction_review import safe_extension
from script_coverage import build_coverage
from script_normalize import normalize_script
from test_script_selection import source, MODES


def long_block(parts):
    item=source([(parts[0][0],parts[-1][1],''.join(text for _,_,text in parts))])
    item['segments'][0]['id']='one-asr-block'
    item['segments'][0]['words']=[{'start':a,'end':b,'text':text,'probability':.99} for a,b,text in parts]
    return item


class SentenceEvidence(unittest.TestCase):
    def test_split_whole_sentences_keeps_original_times_and_text(self):
        item=long_block([(1,3,'再来一遍。'),(3.4,6,'这个羊肉串鲜嫩多汁。'),(6.5,9,'我昨天去了另一家店。')])
        spans,reason=sentence_spans(item['segments'][0])
        self.assertIsNone(reason)
        self.assertEqual([(r['start'],r['end'],r['text']) for r in spans],
            [(1,3,'再来一遍。'),(3.4,6,'这个羊肉串鲜嫩多汁。'),(6.5,9,'我昨天去了另一家店。')])

    def test_short_script_keeps_complete_sentence_not_substring(self):
        item=long_block([(1,3,'今天我们来到示例烤肉。'),(3.4,6,'这个羊肉串鲜嫩多汁，烤得刚刚好。'),(6.5,9,'我昨天去了另一家店。')])
        for mode in MODES:
            selected,_,_,missing,_=select([item],'达人：这个羊肉串鲜嫩多汁',30,mode)
            self.assertFalse(missing)
            self.assertEqual(len(selected),1)
            self.assertEqual(selected[0]['text'],'这个羊肉串鲜嫩多汁，烤得刚刚好。')
            self.assertLessEqual(selected[0]['start'],3.4)
            self.assertGreaterEqual(selected[0]['end'],6)

    def test_director_instruction_and_off_script_sentence_do_not_ride_with_required_line(self):
        item=long_block([(1,3,'再来一遍。'),(3.4,6,'这个羊肉串鲜嫩多汁。'),(6.5,9,'我昨天去了另一家店。')])
        original=copy.deepcopy(item)
        for mode in MODES:
            selected,cuts,_,missing,_=select([item],'达人：这个羊肉串鲜嫩多汁',30,mode)
            self.assertFalse(missing)
            self.assertFalse(cuts) # This is script selection, not claimed speaker identification.
            self.assertEqual(selected[0]['voiceRole'],'unknown')
            self.assertEqual(selected[0]['text'],'这个羊肉串鲜嫩多汁。')
            self.assertGreaterEqual(selected[0]['start'],3)
            self.assertLessEqual(selected[0]['end'],6.5)
        self.assertEqual(item,original)

    def test_selected_short_laugh_is_restored_after_script_sentence(self):
        item=long_block([(1,3,'这个羊肉串鲜嫩多汁。'),(3.2,4,'哈哈哈！'),(4.5,7,'我昨天去了另一家店。')])
        for mode in MODES:
            selected,_,_,_,_=select([item],'达人：这个羊肉串鲜嫩多汁',30,mode)
            text=''.join(r['text'] for r in selected)
            self.assertIn('哈哈哈',text)
            self.assertNotIn('昨天',text)
            self.assertTrue(any(r.get('protected') for r in selected))

    def test_untranscribed_reaction_in_sentence_gap_survives_every_mode(self):
        item=long_block([(1,3,'这个羊肉串鲜嫩多汁。'),(4,6,'明天你几点来。')])
        item['voiceGuard']['protected']=[[3.13,3.77,'笑声事件']]
        for mode in MODES:
            row=select([item],'达人：这个羊肉串鲜嫩多汁',30,mode)[0][0]
            self.assertGreaterEqual(row['end'],3.77)
            self.assertLessEqual(row['end'],4)
            self.assertNotIn('明天',row['text'])
            self.assertTrue(row['reactionEdgeProtection'])
            self.assertGreaterEqual(safe_extension(row,(1,3),item,30)[1],3.77)

    def test_untranscribed_leading_friend_reaction_in_gap_survives(self):
        item=long_block([(1,3,'你把手机给我。'),(4,6,'这个羊肉串鲜嫩多汁。')])
        item['voiceGuard']['protected']=[[3.13,3.77,'已确认闺蜜短反应']]
        for mode in MODES:
            row=select([item],'达人：这个羊肉串鲜嫩多汁',30,mode)[0][0]
            self.assertLessEqual(row['start'],3.13)
            self.assertGreaterEqual(row['start'],3)
            self.assertNotIn('手机',row['text'])

    def test_reaction_edge_cannot_extend_into_omitted_spoken_core(self):
        item=long_block([(1,3,'这个羊肉串鲜嫩多汁。'),(4,6,'明天你几点来。')])
        item['voiceGuard']['protected']=[[3.1,5,'笑声事件']]
        for mode in MODES:
            row=select([item],'达人：这个羊肉串鲜嫩多汁',30,mode)[0][0]
            self.assertLessEqual(row['end'],4)
            self.assertNotIn('明天',row['text'])

    def test_coarse_window_does_not_force_whole_gap_retention(self):
        item=long_block([(1,3,'这个羊肉串鲜嫩多汁。'),(7,9,'明天你几点来。')])
        item['voiceGuard']['protected']=[[2,6,'SenseVoice 反应候选（窗口级）']]
        row=select([item],'达人：这个羊肉串鲜嫩多汁',30,'strict')[0][0]
        self.assertFalse(row['reactionEdgeProtection'])
        self.assertLess(row['end'],6)

    def test_scripted_friend_reply_kept_in_full(self):
        item=long_block([(1,3,'这个羊肉串鲜嫩多汁。'),(3.4,5,'我也想要一串。'),(5.5,7,'明天你几点来？')])
        for mode in MODES:
            selected,_,_,missing,_=select([item],'达人：这个羊肉串鲜嫩多汁\n闺蜜：我也想要一串',30,mode)
            self.assertFalse(missing)
            self.assertIn('我也想要一串',''.join(r['text'] for r in selected))
            self.assertNotIn('明天',''.join(r['text'] for r in selected))

    def test_visual_extension_cannot_restore_rejected_sentences(self):
        item=long_block([(1,3,'再来一遍。'),(3.4,6,'这个羊肉串鲜嫩多汁。'),(6.5,9,'我昨天去了另一家店。')])
        row=select([item],'达人：这个羊肉串鲜嫩多汁',30,'strict')[0][0]
        a,b=safe_extension(row,(0,11),item,30)
        self.assertGreaterEqual(a,3)
        self.assertLessEqual(b,6.5)
        self.assertLessEqual(a,row['speechStart']);self.assertGreaterEqual(b,row['speechEnd'])

    def test_merged_selected_siblings_are_not_cut_by_each_others_stops(self):
        item=long_block([(1,3,'这个羊肉串鲜嫩多汁。'),(3.2,4,'哈哈哈！'),(4.5,7,'我昨天去了另一家店。')])
        rows=select([item],'达人：这个羊肉串鲜嫩多汁',30,'strict')[0]
        self.assertEqual(len(rows),1)
        a,b=safe_extension(rows[0],(0,10),item,30)
        self.assertLessEqual(a,1);self.assertGreaterEqual(b,4);self.assertLessEqual(b,4.5)

    def test_complete_script_across_pause_in_same_asr_block_is_not_truncated(self):
        item=long_block([(1,3,'这个羊肉串鲜嫩多汁。'),(8,10,'外焦里嫩太香了。')])
        script='达人：这个羊肉串鲜嫩多汁，外焦里嫩太香了。'
        for mode in MODES:
            selected,_,_,missing,_=select([item],script,30,mode)
            self.assertFalse(missing)
            self.assertIn('外焦里嫩太香了',''.join(r['text'] for r in selected))
            self.assertGreaterEqual(selected[-1]['end'],10)

    def test_long_reaction_extension_stops_before_later_off_script_speech(self):
        item=long_block([(1,3,'这个羊肉串鲜嫩多汁。'),(8.017,10,'明天你几点过来。')])
        row=select([item],'达人：这个羊肉串鲜嫩多汁',30,'expressive')[0][0]
        self.assertLessEqual(safe_extension(row,(0,11),item,30)[1],8.017)

    def test_coverage_recomputes_whole_sentences_from_original_word_evidence(self):
        item=long_block([(1,3,'再来一遍。'),(3.4,6,'这个羊肉串鲜嫩多汁。'),(6.5,9,'我昨天去了另一家店。')])
        script='达人：这个羊肉串鲜嫩多汁'
        selected,_,_,missing,_=select([item],script,30,'strict')
        report=build_coverage(normalize_script(script),{'selectedSpeech':selected,'missingScript':missing,'duration':3},items=[item])
        self.assertEqual(report['dialogue']['complete'],1)
        selected[0]['end']=5.9
        report=build_coverage(normalize_script(script),{'selectedSpeech':selected,'missingScript':missing,'duration':3},items=[item])
        self.assertEqual(report['dialogue']['complete'],0)

    def test_middle_off_script_sentence_is_not_brought_back_by_same_target_merge(self):
        item=long_block([(1,3,'这个羊肉串鲜嫩多汁。'),(3.2,3.7,'你把手机给我。'),(3.9,5,'外焦里嫩太香了。')])
        script='达人：这个羊肉串鲜嫩多汁，外焦里嫩太香了。'
        for mode in MODES:
            selected,_,_,missing,_=select([item],script,30,mode)
            self.assertFalse(missing)
            self.assertEqual(len(selected),2)
            self.assertNotIn('手机',''.join(r['text'] for r in selected))
            self.assertFalse(any(r['start']<3.2 and r['end']>3.7 for r in selected))
            reviewed=[dict(r, start=safe_extension(r,(0,7),item,30)[0],
                           end=safe_extension(r,(0,7),item,30)[1]) for r in selected]
            self.assertEqual(len(merge(reviewed)),2)
            report=build_coverage(normalize_script(script),{'selectedSpeech':reviewed,'missingScript':missing,'duration':5},items=[item])
            self.assertEqual(report['dialogue']['complete'],1)

    def test_protected_event_between_required_sentences_is_not_omitted_by_text(self):
        for reaction in ('笑声。','真有你的！'):
            item=long_block([(1,3,'这个羊肉串鲜嫩多汁。'),(3.2,3.7,reaction),(3.9,5,'外焦里嫩太香了。')])
            item['voiceGuard']['protected']=[[3.2,3.7,'SenseVoice 笑声／惊讶候选']]
            for mode in MODES:
                with self.subTest(reaction=reaction,mode=mode):
                    selected,_,_,missing,_=select([item],'达人：这个羊肉串鲜嫩多汁，外焦里嫩太香了。',30,mode)
                    self.assertFalse(missing)
                    self.assertIn(reaction,''.join(row['text'] for row in selected))
                    self.assertTrue(any(row.get('protected') and row['start']<=3.2 and row['end']>=3.7 for row in selected))

    def test_trailing_audio_event_restored_from_precise_window_without_off_script_tail(self):
        for reaction in ('笑声。','真有你的！'):
            item=long_block([(1,3,'这个羊肉串鲜嫩多汁。'),(3.2,3.7,reaction),(3.9,5,'明天你几点过来。')])
            item['voiceGuard']['protected']=[[3.2,3.7,'SenseVoice 笑声／惊讶候选']]
            for mode in MODES:
                with self.subTest(reaction=reaction,mode=mode):
                    selected,_,_,missing,_=select([item],'达人：这个羊肉串鲜嫩多汁',30,mode)
                    self.assertFalse(missing)
                    self.assertIn(reaction,''.join(row['text'] for row in selected))
                    self.assertNotIn('明天',''.join(row['text'] for row in selected))
                    self.assertTrue(any(row['start']<=3.2 and row['end']>=3.7 for row in selected))
                    after=[dict(row,start=safe_extension(row,(0,8),item,30)[0],end=safe_extension(row,(0,8),item,30)[1]) for row in selected]
                    self.assertTrue(all(row['end']<=3.9 for row in after))

    def test_event_flag_does_not_leak_from_whole_asr_block_to_unrelated_sentence(self):
        from night_edit import _script_sentence_units
        from voice_guard import guarded_speech
        from edit_policy import policy
        item=long_block([(1,3,'这个羊肉串鲜嫩多汁。'),(3.2,3.7,'笑声。'),(3.9,5,'明天你几点过来。')])
        item['voiceGuard']['protected']=[[3.2,3.7,'真实事件窗口']]
        kept=guarded_speech(item,item['duration'],30,'balanced')[0]
        self.assertTrue(kept[0]['protected'])
        units=_script_sentence_units(kept,30,policy('balanced'),item['voiceGuard']['protected'])
        self.assertEqual([row['protected'] for row in units],[False,True,False])
        self.assertFalse(units[-1]['audioProtectionEvidence'])

    def test_adjacent_confirmed_friend_short_reply_stays_but_next_unknown_speech_does_not(self):
        item=source([(1,3,'这个羊肉串鲜嫩多汁。'),(3.2,3.7,'我也想要一串。'),(3.9,5,'明天你几点过来。')])
        item['voiceGuard']['windows']=[{'start':1,'end':3,'role':'talent'},
            {'start':3.2,'end':3.7,'role':'friend'},{'start':3.9,'end':5,'role':'unknown'}]
        for mode in MODES:
            selected,_,_,missing,_=select([item],'达人：这个羊肉串鲜嫩多汁',30,mode)
            self.assertFalse(missing)
            self.assertIn('我也想要一串',''.join(row['text'] for row in selected))
            self.assertNotIn('明天',''.join(row['text'] for row in selected))

    def test_precise_reaction_restoration_reassembles_original_sentence_fragments(self):
        item=source([(1,3,'这个羊肉串鲜嫩多汁。'),(3.2,3.4,'真有'),(3.4,3.7,'你的！'),(3.9,5,'明天你几点过来。')])
        item['segments'][1]['id']=item['segments'][2]['id']='one-reaction-sentence'
        item['voiceGuard']['protected']=[[3.2,3.7,'精确惊讶事件']]
        for mode in MODES:
            selected,_,_,_,_=select([item],'达人：这个羊肉串鲜嫩多汁',30,mode)
            joined=''.join(row['text'] for row in selected).replace(' ','')
            self.assertIn('真有你的！',joined)
            self.assertNotIn('明天',joined)
            self.assertGreaterEqual(selected[-1]['end'],3.7)

    def test_coarse_whole_scene_window_does_not_restore_an_isolated_go_or_buy(self):
        item=source([(18.3,20.74,'该省省该花花'),(21.32,21.8,'去'),(21.8,22.06,'买')])
        item['segments'][1]['id']=item['segments'][2]['id']='one-go-buy-sentence'
        item['voiceGuard']['protected']=[[0,24,'脚本明确闺蜜互动，整段保护']]
        for mode in MODES:
            selected,_,_,_,_=select([item],'达人：该省省该花花',30,mode)
            self.assertNotIn('去',''.join(row['text'] for row in selected))
            self.assertNotIn('买',''.join(row['text'] for row in selected))
            self.assertIn('去买',selected[-1]['reactionRecoveryReview'])

    def test_rolling_surprised_window_does_not_add_repeat_or_first_word_of_next_take(self):
        item=source([(5.09,6.69,'本来只想解解馋'),(7.41,9.11,'本来只想解解馋'),
                     (9.11,9.71,'谁'),(9.71,10.69,'知道吃了那么多'),
                     (10.69,11.47,'谁'),(11.47,12.49,'知道吃那么多')])
        item['segments'][2]['id']=item['segments'][3]['id']='first-who-sentence'
        item['segments'][4]['id']=item['segments'][5]['id']='second-who-sentence'
        item['voiceGuard']['protected']=[[7.7,13.5,'SenseVoice 反应候选（窗口级）']]
        for mode in MODES:
            selected,_,_,_,_=select([item],'达人：本来只想解解馋\n达人：谁知道吃了那么多',30,mode)
            text=''.join(row['text'] for row in selected).replace(' ','')
            self.assertEqual(text.count('本来只想解解馋'),1)
            self.assertEqual(text.count('谁'),1)
            self.assertTrue(all(row['speechEnd']<=10.69 for row in selected))

    def test_known_friend_split_response_is_restored_whole(self):
        item=source([(1,3,'这个羊肉串鲜嫩多汁。'),(3.2,3.4,'我也'),(3.4,3.7,'想要一串。'),(3.9,5,'明天你几点过来。')])
        item['segments'][1]['id']=item['segments'][2]['id']='one-friend-response'
        item['voiceGuard']['windows']=[{'start':1,'end':3,'role':'talent'},
            {'start':3.2,'end':3.7,'role':'friend'},{'start':3.9,'end':5,'role':'unknown'}]
        for mode in MODES:
            selected,_,_,_,_=select([item],'达人：这个羊肉串鲜嫩多汁',30,mode)
            self.assertIn('我也想要一串',''.join(row['text'] for row in selected).replace(' ',''))
            self.assertNotIn('明天',''.join(row['text'] for row in selected))

    def test_sentence_list_cannot_fake_missing_word_or_cut_end_as_complete(self):
        item=long_block([(1,3,'这个羊肉串鲜嫩多汁。'),(3.2,3.7,'你把手机给我。'),(3.9,5,'外焦里嫩太香了。')])
        script='达人：这个羊肉串鲜嫩多汁，外焦里嫩太香了。'
        selected,_,_,missing,_=select([item],script,30,'strict')
        selected[-1]['end']=4.5
        for row in selected:
            row['scriptCoverage'][0]['sourceSentenceSpans'][-1]['end']=4.5
        report=build_coverage(normalize_script(script),{'selectedSpeech':selected,'missingScript':missing,'duration':4},items=[item])
        self.assertEqual(report['dialogue']['complete'],0)

    def test_missing_words_does_not_prove_partial_block_cut(self):
        item=long_block([(1,3,'再来一遍。'),(3.4,6,'这个羊肉串鲜嫩多汁。')])
        item['segments'][0]['words'].pop(0)
        spans,reason=sentence_spans(item['segments'][0])
        self.assertEqual(spans,[]);self.assertIn('没有完整覆盖',reason)
        selected,_,_,_,_=select([item],'达人：这个羊肉串鲜嫩多汁',30,'strict')
        self.assertEqual(selected[0]['text'],item['segments'][0]['text'])
        self.assertIn('完整覆盖',selected[0]['scriptSegmentationReview'])

    def test_bad_timing_never_drives_sentence_cuts(self):
        baseline=long_block([(1,3,'好吃。'),(3.4,6,'我还想吃。')])['segments'][0]
        for field,value in [('start',2.9),('end',float('nan')),('end',9),('probability',.2)]:
            segment=copy.deepcopy(baseline);segment['words'][1][field]=value
            with self.subTest(field=field,value=value):
                spans,reason=sentence_spans(segment)
                self.assertFalse(spans);self.assertTrue(reason)

    def test_sentence_boundary_inside_one_timestamped_word_is_not_cut(self):
        segment={'start':1,'end':3,'text':'好吃。再来。','words':[{'start':1,'end':3,'text':'好吃。再来。'}]}
        spans,reason=sentence_spans(segment)
        self.assertFalse(spans);self.assertIn('词内部',reason)

    def test_decimals_and_commas_do_not_cut_a_required_sentence(self):
        segment={'start':1,'end':3,'text':'It costs 18.5 yuan, very good.','words':[]}
        self.assertEqual(sentence_spans(segment),([],None))


if __name__=='__main__':unittest.main()
