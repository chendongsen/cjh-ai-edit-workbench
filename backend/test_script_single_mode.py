"""Actual retake failure shapes, without models, media or editor side effects."""
import copy
import unittest

from night_edit import select, merge
from speech_sentences import script_spans
from script_coverage import build_coverage, validate_dialogue_integrity
from script_normalize import normalize_script
from reaction_review import safe_extension
from test_script_selection import source
from test_speech_sentences import long_block


class ScriptSingleMode(unittest.TestCase):
    def test_realigned_units_cannot_restore_shared_original_asr_batch(self):
        item=source([(1,2,'但是买大牌'),(3,4,'再来'),(5,6,'但是买大牌'),
                     (6.3,8,'还是知道去免税店'),(9,11,'明天几点集合')])
        for segment in item['segments']:
            segment.update(id='old-shared-id',asrReview={'decision':'audio-consensus-realignment',
                'start':1,'end':11,'original':'但是买大牌再来但是买大牌还是知道去免税店明天几点集合'})
        original=copy.deepcopy(item)
        script='达人：但是买大牌\n达人：还是知道去免税店'
        selected,_,_,missing,_=select([item],script,30)
        self.assertFalse(missing)
        text=''.join(r['text'] for r in selected)
        self.assertEqual(text.count('但是买大牌'),1)
        self.assertNotIn('再来',text);self.assertNotIn('集合',text)
        for row in selected:
            a,b=safe_extension(row,(0,13),item,30)
            self.assertFalse(a<4 and b>3)
            self.assertLessEqual(b,9)
        validate_dialogue_integrity(selected,30)
        self.assertEqual(item,original)

    def test_exact_script_word_edges_remove_unpunctuated_repeat_and_countdown(self):
        item=long_block([(1,1.2,'3'),(1.2,1.4,'2'),(1.4,1.6,'1'),
            (2,3,'还有一个后果'),(3,3.3,'就是'),(4,5,'还有一个后果'),(5,5.3,'就是'),
            (6,7,'变得太会生活了'),(8,10,'明天记得早点来')])
        script='达人：还有一个后果就是\n达人：变得太会生活了'
        selected,_,_,missing,_=select([item],script,30)
        self.assertFalse(missing)
        text=''.join(r['text'] for r in selected)
        self.assertEqual(text.count('还有一个后果'),1)
        self.assertNotIn('321',text);self.assertNotIn('明天',text)
        coverage=build_coverage(normalize_script(script),{'selectedSpeech':selected},items=[item])
        self.assertEqual(coverage['dialogue']['complete'],2)
        clipped=copy.deepcopy(selected)
        clipped[-1]['end']=clipped[-1]['speechEnd']-.5
        bad=build_coverage(normalize_script(script),{'selectedSpeech':clipped},items=[item])
        self.assertEqual(bad['dialogue']['complete'],1)

    def test_word_internal_script_match_never_invents_a_cut(self):
        item=long_block([(1,5,'这个羊肉串鲜嫩多汁明天还想来')])
        spans,_=script_spans(item['segments'][0],['这个羊肉串鲜嫩多汁'])
        self.assertEqual(spans,[])

    def test_scripted_handoff_survives_but_instruction_in_gap_does_not(self):
        script='C1｜角色意图：达人闺蜜互动｜台词意图：借我一张纸巾，给你\n素材：a.mov'
        item=source([(1,3,'借我一张纸巾'),(7,8,'给你')])
        kept=select([item],script,30)[0]
        self.assertEqual(len(kept),1)
        self.assertLessEqual(kept[0]['start'],1);self.assertGreaterEqual(kept[0]['end'],8)
        item=source([(1,3,'借我一张纸巾'),(4,5,'再来'),(7,8,'给你')])
        # A production cue invalidates this continuous take. Never glue its
        # two sides back together through later reaction merging.
        kept=select([item],script,30)[0]
        self.assertNotIn('再来',''.join(r['text'] for r in kept))
        self.assertTrue(all(not (r['start']<5 and r['end']>4) for r in merge(kept)))

    def test_exact_friend_reply_and_short_laugh_stay_with_script(self):
        item=long_block([(1,3,'这个羊肉串真好吃。'),(3.1,4,'哈哈哈！'),
                         (4.2,6,'我也想吃一串。'),(7,9,'明天几点开拍？')])
        script='达人：这个羊肉串真好吃\n闺蜜：我也想吃一串'
        kept,_,_,missing,_=select([item],script,30)
        self.assertFalse(missing)
        text=''.join(r['text'] for r in kept)
        self.assertIn('哈哈哈',text);self.assertIn('我也想吃一串',text)
        self.assertNotIn('开拍',text)


if __name__=='__main__':unittest.main()
