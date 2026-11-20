import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import speaker as s
from common import write, read


class SpeakerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.c={'start':1.,'end':3.,'intent':'instruction','stable':True}
        self.good={'talent':.2,'friend':.3,'director':.85}

    def test_unconfirmed_never_changes(self):
        self.assertFalse(s.decide(self.c,self.good,[],False)[0])

    def test_instruction_and_voice_both_required(self):
        self.assertTrue(s.decide(self.c,self.good,[],True)[0])
        self.assertFalse(s.decide(dict(self.c,intent='dialogue'),self.good,[],True)[0])

    def test_reaction_and_friend_override_voice(self):
        for reason in ['笑声','达人反应','闺蜜接话']:
            self.assertFalse(s.decide(self.c,self.good,[[2,2.5,reason]],True)[0])

    def test_short_overlap_unstable_retained(self):
        for updates in [{'end':2.1},{'overlap':True},{'stable':False}]:
            self.assertFalse(s.decide(dict(self.c,**updates),self.good,[],True)[0])

    def test_missing_uncertain_and_nonfinite_retained(self):
        for updates in [{'director':.5},{'friend':.8},{'director':float('nan')}]:
            self.assertFalse(s.decide(self.c,dict(self.good,**updates),[],True)[0])
        self.assertFalse(s.decide(self.c,{'director':1},[],True)[0])

    def test_adjacent_protected_turn_not_muted(self):
        pcm=np.full(s.SR*4,12345,dtype=np.int16)
        out=s.attenuate(pcm,[[1.12,2.88]])
        self.assertTrue(np.array_equal(out[3*s.SR:],pcm[3*s.SR:]))
        self.assertTrue(np.array_equal(out[:s.SR],pcm[:s.SR]))
        self.assertEqual(len(out),len(pcm))
        self.assertLess(abs(int(out[2*s.SR])),20)

    def test_empty_mask_bit_identical(self):
        pcm=np.array([-32768,-1,0,1,32767],dtype=np.int16)
        self.assertTrue(np.array_equal(s.attenuate(pcm,[]),pcm))

    def test_media_traversal_rejected(self):
        for value in ['spk:../../x:a.wav','spk:ok:../a.wav','spk:ok:a.json','file:/etc/passwd']:
            with self.assertRaises(ValueError):s.media_path(value)

    def test_full_pipeline_synthetic_fixture_not_user_references(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(s,'BASE',Path(tmp)):
            dest=s.folder('fixture');(dest/'media').mkdir(parents=True)
            pcm=np.full(s.SR*5,10000,dtype=np.int16)
            s.wav_write(dest/'media/before.wav',pcm)
            refs=[{'id':r,'source':r,'start':0,'end':3,'embedding':e} for r,e in [('a',[1,0,0]),('b',[0,1,0]),('c',[0,0,1])]]
            candidate=dict(self.c,embedding=[0,1,0],windows=[[0,1,0],[0,1,0]])
            manifest={'modelReady':True,'model':{'name':'SYNTHETIC TEST ONLY'},'references':refs,
             'samples':[{'id':'s1','title':'fixture','source':'heldout','start':0,'end':5,
              'before':'spk:fixture:before.wav','protected':[[3,5,'reaction']], 'candidates':[candidate]}]}
            write(dest/'manifest.json',manifest)
            labels={'a':'talent','b':'director','c':'friend'}
            for bad,ack in [(labels,False),({'a':'talent'},True),({'a':'talent','b':'director','c':'pending'},True)]:
                with self.assertRaises(ValueError):s.validate_confirmation('fixture',bad,ack)
            payload=s.validate_confirmation('fixture',labels,True)
            s.run(payload,dest)
            result=read(dest/'result.json');self.assertEqual(result['changedSamples'],1)
            after=s.wav_read(s.media_path(result['samples'][0]['after']))
            self.assertTrue(np.array_equal(after[3*s.SR:],pcm[3*s.SR:]))
            self.assertEqual(result['attenuatedSeconds'],1.76)
            # Same-source reference overlap must not count as evidence.
            manifest['references'][1]['source']='heldout'
            write(dest/'manifest.json',manifest)
            with self.assertRaises(ValueError):s.run(payload,dest)  # stale approval
            s.run(s.validate_confirmation('fixture',labels,True),dest)
            self.assertEqual(read(dest/'result.json')['changedSamples'],0)


if __name__=='__main__':unittest.main()
