"""Explicit script requirements govern V2 and cannot be replaced by generic shots."""
import copy
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workflow import supporting_pictures, required_main_ranges, run
from common import write


class SupportingPipelineTests(unittest.TestCase):
    def test_explicit_script_shots_do_not_trigger_generic_fill(self):
        main = [{'assetId':'main','at':0,'start':0,'end':15,'text':'制作再上菜'}]
        plan = {'selectedSpeech':main,'duration':15,'foodPlacements':[{'legacy':True}]}
        required = {'assetId':'required','start':1,'end':3,'at':4,'brollKind':'script'}
        prepared = dict(copy.deepcopy(plan),foodPlacements=[required])
        report = {'requirements':[{'status':'partial','componentCoverage':[
            {'status':'covered-main','placement':{'at':1,'start':2,'end':4,'track':1}},
            {'status':'inserted','placement':dict(required,track=2)}]}]}
        events=[]
        def shots(p,*args,**kwargs):
            events.append('required');self.assertEqual(p['foodPlacements'],[])
            return prepared,report
        def optional(p,*args,**kwargs):
            events.append('optional');self.assertEqual(p['foodPlacements'],[required])
            self.assertTrue(kwargs['preserve_existing'])
            self.assertEqual(kwargs['protected_main_ranges'],[(1.,3.)])
            return p
        with patch('script_shots.apply_script_shots',side_effect=shots),patch('broll.enhance',side_effect=optional) as extra:
            result,actual_report=supporting_pictures(plan,[],{'shotRequirements':[{'text':'上菜'}]},
                {'unattended':True,'visionModel':'local'},Path('/unused'),lambda *a:None)
        self.assertEqual(events,['required'])
        extra.assert_not_called()
        self.assertEqual(result['selectedSpeech'],main)
        self.assertEqual(actual_report,report)
        self.assertEqual(result['brollSummary']['scriptCount'],1)
        self.assertEqual(result['brollSummary']['foodCount'],0)
        self.assertEqual(result['brollSummary']['environmentCount'],0)
        self.assertEqual(result['usedFoodSources'],1)

    def test_zero_required_matches_remain_missing_instead_of_generic_fill(self):
        plan={'selectedSpeech':[],'foodPlacements':[]}
        with patch('script_shots.apply_script_shots',return_value=(plan,{'requirements':[]})),patch('broll.enhance',return_value=plan) as extra:
            supporting_pictures(plan,[],{'shotRequirements':[{'text':'特写'}]},
                {'unattended':True,'visionModel':'local'},Path('/unused'),lambda *a:None)
            extra.assert_not_called()
            self.assertEqual(plan['foodPlacements'],[])
            self.assertEqual(plan['brollSummary']['selectionBasis'],'explicit-script-requirements')

    def test_no_script_uses_existing_optional_path(self):
        plan={'selectedSpeech':[],'foodPlacements':[]}
        with patch('script_shots.apply_script_shots') as shots,patch('broll.enhance',return_value=plan) as extra:
            supporting_pictures(plan,[],{}, {'unattended':True,'visionModel':'local'},Path('/unused'),lambda *a:None)
            shots.assert_not_called()
            self.assertFalse(extra.call_args.kwargs['preserve_existing'])

    def test_invalid_required_protection_is_not_silently_ignored(self):
        for at,end in ((-1,2),(float('nan'),2),(1,0)):
            with self.subTest(at=at,end=end),self.assertRaises(ValueError):
                required_main_ranges({'requirements':[{'status':'covered-main',
                    'placement':{'track':1,'at':at,'start':0,'end':end}}]})

    def test_nonempty_unparseable_script_stops_before_models_in_all_modes(self):
        with tempfile.TemporaryDirectory() as temporary:
            path=Path(temporary)/'input.json'
            for mode in ('strict','balanced','expressive'):
                with self.subTest(mode=mode):
                    write(path,{'assets':[],'unattended':True,'editMode':mode,
                        'script':'画面：后厨制作\n备注：保留反应'})
                    with patch('workflow.analyze') as analyze,patch('workflow.editor_startup.prepare') as editors:
                        with self.assertRaisesRegex(ValueError,'不会把全部口播'):
                            run(path)
                        analyze.assert_not_called();editors.assert_not_called()


if __name__=='__main__':unittest.main()
