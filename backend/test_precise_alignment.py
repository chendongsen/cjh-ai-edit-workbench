import copy,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import precise_alignment as alignment


def row(text='好吃', start=1., end=3., identifier='s1', **extra):
    return dict(id=identifier,start=start,end=end,text=text,
                words=[{'start':start,'end':end,'text':text}],voiceRole='unknown',**extra)


class PreciseAlignmentTests(unittest.TestCase):
    def test_audio_agreement_only(self):
        self.assertIsNone(alignment.choose_text('好吃','非常好吃')[0])
        self.assertIsNone(alignment.choose_text('哈哈','')[0])
        self.assertEqual(alignment.choose_text('好吃','好吃。')[1],'audio-agreement')
        self.assertEqual(alignment.choose_text('好迟','好吃。',['好吃'])[1],'independent-audio-consensus')

    def test_raw_alignment_anomalies_never_interpolated(self):
        rows=alignment.parse_raw_timestamps(['好','吃'],[160,320,240,480])
        self.assertEqual(rows[1]['start_time'],240)
        words=[{'text':r['text'],'start':r['start_time']/1000,'end':r['end_time']/1000} for r in rows]
        with self.assertRaises(ValueError):alignment.validate_words(words,'好吃',0,1)
        with self.assertRaises(ValueError):alignment.parse_raw_timestamps(['好'],[160])

    def test_full_text_required(self):
        with self.assertRaisesRegex(ValueError,'完整覆盖'):
            alignment.validate_words([{'text':'好','start':1,'end':2}],'好吃',0,3)

    def test_invalid_times_rejected(self):
        for words in ([{'text':'好','start':1,'end':1}],
                      [{'text':'好','start':1,'end':2},{'text':'吃','start':1.9,'end':3}],
                      [{'text':'好','start':-1,'end':1}],
                      [{'text':'好','start':1,'end':float('nan')}],
                      [{'text':'好','start':1,'end':4}]):
            with self.subTest(words=words),self.assertRaises(ValueError):
                alignment.validate_words(words,'好吃',0,3)

    def test_no_fake_confidence_or_uniform_times(self):
        words=alignment.validate_words([{'text':'好','start':1.02,'end':1.11},
                                       {'text':'吃','start':2.57,'end':2.91}],'好吃。',1,3)
        self.assertEqual([w['start'] for w in words],[1.02,2.57])
        self.assertFalse(any('probability' in w for w in words))

    def test_whole_source_sentence_rebuilt(self):
        groups=alignment.groups_for({'segments':[row('谁',1,1.3),row('知道',1.3,2),row('下一句',3,4,'s2')]})
        self.assertEqual([g['text'] for g in groups],['谁知道','下一句'])
        self.assertEqual(groups[0]['indices'],[0,1])

    def test_ctc_origin_rebuilt(self):
        review={'start':1.,'end':3.,'senseVoice':'谁知道','whisperRetry':'谁知道'}
        group=alignment.groups_for({'segments':[row('谁',1,1.3,None,asrReview=review),
                           row('知道',1.3,3,None,asrReview=review)]})[0]
        self.assertEqual(group['text'],'谁知道');self.assertEqual(group['references'],['谁知道'])

    def test_other_utterance_breaks_group(self):
        groups=alignment.groups_for({'segments':[row('一',1,2),row('二',2,3,'s2'),row('三',3,4)]})
        self.assertEqual(len(groups),3)

    def test_unknown_role_not_upgraded(self):
        group=alignment.groups_for({'segments':[row()]})[0]
        result={'key':'abc','status':'aligned','qwenText':'好吃。',
                'words':[{'text':'好','start':1.1,'end':1.5},{'text':'吃','start':2.1,'end':2.8}]}
        new=alignment.apply_result(group,{'key':'abc','start':.8,'end':3.2},result)
        self.assertEqual(new['voiceRole'],'unknown');self.assertFalse(new['voiceStable'])
        self.assertEqual(new['id'],'s1');self.assertEqual(new['preciseAlignment']['sourceSegmentIds'],['s1'])
        self.assertFalse(new['preciseAlignment']['scriptWasModelInput'])

    def test_mixed_roles_preserve_old_boundary_and_risk(self):
        a=row('好',1,2);a['voiceRole']='unknown';b=row('吃',2,3);b['voiceRole']='director'
        group=alignment.groups_for({'segments':[a,b]})[0]
        with self.assertRaisesRegex(alignment.BoundaryRetention,'候选说话人'):
            alignment.apply_result(group,{'key':'k','start':1,'end':3},
                {'key':'k','status':'aligned','qwenText':'好吃','words':[{'text':'好吃','start':1,'end':3}]})

    def test_production_countdown_boundary_cannot_be_merged_by_alignment(self):
        group=alignment.groups_for({'segments':[row('三二一',3.68,5.62),row('广东的传统点心',6.10,7.54)]})[0]
        result={'key':'k','status':'aligned','qwenText':'三二一，广东的传统点心。','words':[
            {'text':'三二一','start':3.72,'end':4.84},{'text':'广东的传统点心','start':6.12,'end':7.64}]}
        with self.assertRaisesRegex(alignment.BoundaryRetention,'倒数'):
            alignment.apply_result(group,{'key':'k','start':3.56,'end':7.66},result)

    def test_stable_voice_evidence_is_not_discarded(self):
        old=row();old.update(voiceRole='director',voiceStable=True)
        self.assertIn('稳定',alignment.boundary_retention_reason(alignment.groups_for({'segments':[old]})[0]))

    def test_protected_leading_and_trailing_reaction_survives_all_modes(self):
        from voice_guard import guarded_speech
        from night_edit import select
        for mode in ('strict','balanced','expressive'):
            for event,word_times,role in (
                ([1.,2.,'笑声事件'],[(2.5,2.8),(3.,3.5)],'unknown'),
                ([3.5,4.5,'笑声事件'],[(1.5,1.9),(2.1,2.5)],'unknown'),
                ([1.,2.,'已确认闺蜜反应'],[(2.5,2.8),(3.,3.5)],'friend')):
                with self.subTest(mode=mode,event=event,role=role):
                    original=row(start=1.,end=4.);original['voiceRole']=role
                    group=alignment.groups_for({'segments':[original]})[0]
                    result={'key':'k','status':'aligned','qwenText':'好吃。',
                        'words':[{'text':t,'start':a,'end':b} for t,(a,b) in zip('好吃',word_times)]}
                    new=alignment.apply_result(group,{'key':'k','start':.88,'end':4.12},result,[event])
                    item={'assetId':'a','source':'/a.mov','video':True,'audio':True,
                        'vision':{'kind':'人物'},'usableDuration':8.,'duration':8.,'segments':[new],
                        'voiceGuard':{'windows':[{'start':0,'end':8,'role':role}],
                          'protected':[event],'utterances':[{'start':new['start'],'end':new['end'],
                            'role':role,'stable':False,'similarities':{}}]}}
                    kept,removed,reviews=guarded_speech(item,8.,30,mode)
                    self.assertTrue(kept[0]['protected'])
                    start,end=max(1.,event[0]),min(4.,event[1])
                    self.assertLessEqual(kept[0]['start'],start);self.assertGreaterEqual(kept[0]['end'],end)
                    chosen,*_=select([item],'达人：好吃',30,mode)
                    self.assertTrue(chosen)
                    self.assertLessEqual(chosen[0]['start'],start);self.assertGreaterEqual(chosen[0]['end'],end)
                    self.assertEqual(new['preciseAlignment']['wordSpeechStart'],word_times[0][0])

    def test_protection_intersection_cannot_capture_neighbor_or_whole_file(self):
        group=alignment.groups_for({'segments':[row(start=1.,end=4.)]})[0]
        result={'key':'k','status':'aligned','qwenText':'好吃','words':[{'text':'好吃','start':2.,'end':3.}]}
        task={'key':'k','start':.88,'end':4.12}
        new=alignment.apply_result(group,task,result,[[0,99,'整段闺蜜互动'],[7,8,'另句笑声']])
        self.assertEqual((new['start'],new['end']),(1.,4.))
        self.assertEqual(len(new['preciseAlignment']['protectedSpans']),1)
        plain=alignment.apply_result(group,task,result,[])
        self.assertEqual((plain['start'],plain['end']),(2.,3.))

    def test_bad_cache_and_disagreement_cannot_apply(self):
        group=alignment.groups_for({'segments':[row()]})[0]
        with self.assertRaisesRegex(ValueError,'音频文字支持'):
            alignment.apply_result(group,{'key':'k','start':1,'end':3},
                {'key':'k','status':'aligned','qwenText':'脚本才有的句子','words':[]})
        with self.assertRaisesRegex(ValueError,'不匹配'):
            alignment.apply_result(group,{'key':'k','start':1,'end':3},{'key':'other'})

    def test_long_word_suspect(self):
        self.assertIn('原词时间被拉长',alignment.reasons_for(alignment.groups_for({'segments':[row()]})[0]))

    def test_disabled_is_untouched(self):
        items=[{'segments':[row()]}]
        with patch.object(alignment,'CONFIG',Path('/nonexistent/semantic.json')):
            result,report=alignment.run(items,{'preciseAlignment':False},'/nonexistent')
        self.assertIs(result,items);self.assertEqual(report['status'],'disabled')

    def test_pipeline_provenance_originals_and_script_exclusion(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);model=root/'model';model.mkdir()
            (model/'config.json').write_text('{}');(model/'weights.safetensors').write_bytes(b'test')
            source=root/'a.mov';source.write_bytes(b'test media');config=root/'semantic-engines.json'
            config.write_text(json.dumps({'python':sys.executable,'asrModel':str(model),
                'alignerModel':str(model),'preciseAlignment':True}))
            item={'assetId':'a','source':str(source),'audio':True,'usableDuration':8,
                'segments':[row(),row('哇',4,5,'s2')],
                'voiceGuard':{'windows':[],'protected':[[4,6,'笑声保护']],
                    'utterances':[{'start':1.,'end':3.,'role':'unknown','similarities':{},'stable':False}]}}
            original=copy.deepcopy(item)
            def fake(manifest,phase):
                self.assertNotIn('仅在原脚本的台词',json.dumps(manifest,ensure_ascii=False))
                self.assertNotIn('script',manifest)
                for task in manifest['tasks']:
                    if task['currentText']=='好吃':
                        result={'key':task['key'],'status':'aligned','qwenText':'好吃。',
                            'words':[{'text':'好','start':1.1,'end':1.5},{'text':'吃','start':2,'end':2.9}]}
                    else:result={'key':task['key'],'status':'disagreement','qwenText':'不要','reason':'文字分歧'}
                    Path(task['cachePath']).write_text(json.dumps(result))
            with patch.object(alignment,'DATA',root),patch.object(alignment,'CONFIG',config):
                result,report=alignment.run([item],{'preciseAlignment':True,'preciseAlignmentAll':True,
                    'script':'仅在原脚本的台词'},root/'output',runner=fake)
            self.assertEqual(item,original);self.assertEqual(report['aligned'],1)
            self.assertEqual(report['disagreements'],1);self.assertEqual(result[0]['segments'][1],original['segments'][1])
            self.assertEqual(result[0]['voiceGuard']['protected'],original['voiceGuard']['protected'])
            self.assertEqual(result[0]['segments'][0]['text'],'好吃。')
            self.assertTrue((root/'output/precise-aligned-items.json').is_file())


if __name__=='__main__':unittest.main()
