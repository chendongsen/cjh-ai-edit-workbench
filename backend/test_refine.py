import unittest
from speech_refine import ahc,arbitrate,split_words,low_confidence,ctc_units
from reaction_review import safe_extension,sample_times,validate
from voice_guard import guarded_speech
from test_night import fixture


class RefinementTests(unittest.TestCase):
    def test_script_like_guess_cannot_replace_original(self):
        self.assertEqual(arbitrate('鸟的味道','水牛奶的味道','鸟的味道')[0],'鸟的味道')

    def test_two_audio_decoders_agree(self):
        self.assertEqual(arbitrate('人猫脚柳','容貌焦虑','容貌焦虑。')[1],'audio-consensus-correction')

    def test_empty_secondary_not_a_correction(self):
        self.assertEqual(arbitrate('哈哈','','')[0],'哈哈')

    def test_word_split_keeps_short_response(self):
        s={'start':1,'end':4,'text':'好吃嗯','words':[{'start':1,'end':2,'text':'好吃'},
                                                    {'start':3,'end':4,'text':'嗯'}]}
        rows=split_words(s,[2.5]);self.assertEqual(''.join(r['text'] for r in rows),'好吃嗯')
        self.assertEqual(rows[-1]['start'],3)

    def test_cluster_keeps_minority_speaker(self):
        labels=ahc([[1,0],[.99,.01],[0,1]])
        self.assertEqual(labels[0],labels[1]);self.assertNotEqual(labels[1],labels[2])

    def test_unknown_no_forced_identity(self):
        self.assertEqual(len(set(ahc([[1,0],[-1,0],[0,1]]))),3)

    def test_refined_director_requires_subwindow_agreement(self):
        i=fixture();i['voiceGuard'].update(requireStable=True,utterances=[{'start':2,'end':4,
            'role':'director','similarities':{},'stable':False}])
        self.assertFalse(guarded_speech(i,10,30)[1])

    def test_vision_cannot_shorten(self):
        s={'start':2,'end':4,'speechStart':2.2,'speechEnd':3}
        self.assertEqual(safe_extension(s,(3,3),{'duration':10,'segments':[]},30),(2,4))

    def test_extension_stops_at_next_utterance(self):
        s={'start':2,'end':4,'speechStart':2.2,'speechEnd':3}
        self.assertEqual(safe_extension(s,(2,7),{'duration':10,'segments':[{'start':4.5,'end':6}]},30),(2,4.5))

    def test_final_fractional_frame_never_shaves_speech(self):
        s={'start':8,'end':10.01,'speechStart':8,'speechEnd':10.01}
        self.assertGreaterEqual(safe_extension(s,(8,10.01),{'duration':10.01,'segments':[]},30)[1],10.01)

    def test_frame_samples_clamped(self):
        ts=sample_times(.1,.75,3,2)
        self.assertTrue(all(0<=t<=1.96 for t in ts));self.assertEqual(ts,sorted(set(ts)))

    def test_invalid_visual_output_rejected(self):
        with self.assertRaises(ValueError):validate({'startIndex':99},[0],[1])

    def test_original_high_confidence_skips_rewrite(self):
        self.assertFalse(low_confidence({'words':[{'text':'哈哈','probability':.98}]}))

    def test_ctc_alignment_uses_measured_gaps(self):
        rows=ctc_units({'tokens':['好','。','给','你'],'timestamps':[.2,.4,5,5.2]},10,17)
        self.assertEqual(len(rows),2);self.assertGreater(rows[1]['start'],14.8)
        self.assertEqual(''.join(r['text'] for r in rows),'好给你')

    def test_ctc_malformed_timestamps_not_invented(self):
        self.assertFalse(ctc_units({'tokens':['好'],'timestamps':[]},0,2))

    def test_friend_action_gap_does_not_drop_reply(self):
        from night_edit import select
        i=fixture('好多榴莲酱','unknown',[[0,20,'闺蜜互动']]);i['duration']=i['usableDuration']=20
        i['segments'] += [{'start':5,'end':7,'text':'拿包纸吧','words':[]},
                          {'start':12,'end':14,'text':'自带纸巾给你','words':[]}]
        selected,*_=select([i],'C1｜角色意图：达人闺蜜互动｜台词意图：好多榴莲酱，拿包纸吧，自带纸巾给你\n素材：a.mov',30)
        self.assertIn('给你',''.join(s['text'] for s in selected))
        self.assertEqual(len(selected),1)

    def test_short_exact_reaction_match_not_lost_in_full_sentence(self):
        from night_edit import select
        i=fixture('哇好好看应该是很好吃','unknown')
        selected,_,_,missing,_=select([i],'达人：哇好好看',30)
        self.assertTrue(selected);self.assertFalse(missing)

    def test_phonetic_recovery_never_rewrites_audio_transcript(self):
        from night_edit import select
        i=fixture('哎呀赶胜胜挡花花呀','unknown')
        script='C1｜角色意图：达人｜台词意图：哎呀该省省该花花\n素材：a.mov'
        selected,_,_,missing,_=select([i],script,30)
        self.assertEqual(missing[0]['status'],'partial');self.assertEqual(selected[0]['text'],'哎呀赶胜胜挡花花呀')
        self.assertIn('需听审',selected[0]['scriptMatchBasis'])

    def test_phonetic_fallback_requires_named_source(self):
        from night_edit import select
        i=fixture('哎呀赶胜胜挡花花呀','unknown')
        with self.assertRaises(ValueError):select([i],'达人：哎呀该省省该花花',30)


if __name__=='__main__':unittest.main()
