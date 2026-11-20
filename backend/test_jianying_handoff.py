import json
import unittest
from unittest.mock import patch, Mock
import jianying_handoff as handoff


class Handoff(unittest.TestCase):
    def test_permission_missing_preserves_draft_without_clicks(self):
        with patch.object(handoff, 'status', return_value={'enabled':True,'installed':True,'trusted':False}), patch.object(handoff.subprocess,'run') as run:
            result=handoff.open_draft('/fixture')
            self.assertFalse(result['draftOpened'])
            self.assertEqual(result['openState'],'permission-required')
            run.assert_not_called()

    def test_disabled_never_controls_app(self):
        with patch.object(handoff,'status',return_value={'enabled':False}), patch.object(handoff.subprocess,'run') as run:
            self.assertFalse(handoff.open_draft('/fixture')['draftOpened'])
            run.assert_not_called()

    def test_success_requires_explicit_helper_result(self):
        ready={'enabled':True,'installed':True,'trusted':True}
        with patch.object(handoff,'status',return_value=ready), patch.object(handoff.subprocess,'run',return_value=Mock(stdout=json.dumps({'draftOpened':True,'editorVerified':True,'openState':'draft-opened'}))) as run:
            result=handoff.open_draft('/path with spaces/草稿')
            self.assertTrue(result['draftOpened']);self.assertTrue(result['editorVerified'])
            self.assertEqual(run.call_args.args[0][-1],'/path with spaces/草稿')

    def test_invalid_receipt_never_claims_success(self):
        with patch.object(handoff,'status',return_value={'enabled':True,'installed':True,'trusted':True}), patch.object(handoff.subprocess,'run',return_value=Mock(stdout='not JSON')):
            self.assertFalse(handoff.open_draft('/fixture')['draftOpened'])

    def test_busy_editor_remains_pending(self):
        with patch.object(handoff,'status',return_value={'enabled':True,'installed':True,'trusted':True}), patch.object(handoff.subprocess,'run',return_value=Mock(stdout='{"draftOpened":false,"openState":"editor-busy"}')):
            result=handoff.open_draft('/fixture')
            self.assertFalse(result['editorVerified'])
            self.assertEqual(result['openState'],'editor-busy')
