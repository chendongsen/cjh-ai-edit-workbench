"""Script preparation reaches strict selection without models or NLE writes."""
import io
import json
import unittest
from unittest.mock import Mock, patch

import test_edit_policy as edit_tests
from night_edit import targets, select
from workflow import automatic_plan


class ScriptSubmissionTests(edit_tests.EditModeSubmissionTests):
    def test_raw_script_is_retained_and_server_prepares_it(self):
        original = '1. 达人（拿起虾饺）：这个虾饺真好吃（花字：好吃）'
        response = self.server.submit(dict(self.request, editMode='strict',
            editingScript=original, scriptPreparation={'targets': [{'text': '客户端伪造'}]}))
        saved = json.loads((self.root/'jobs'/response['id']/'input.json').read_text())
        self.assertEqual(saved['editingScript'], original)
        preparation = saved['scriptPreparation']
        self.assertEqual(preparation['sourceText'], original)
        self.assertTrue(preparation['strictReady'])
        self.assertEqual(preparation['count'], 1)
        self.assertEqual(preparation['targets'][0]['text'], '这个虾饺真好吃')
        self.assertNotIn('客户端伪造', preparation['normalizedText'])
        selected, _, _, missing, _ = select([edit_tests.fixture()], original, 30, 'strict')
        self.assertFalse(missing)
        self.assertEqual(len(selected), 1)

    def test_client_readiness_cannot_override_no_dialogue(self):
        with self.assertRaisesRegex(ValueError, '没有找到明确台词'):
            self.server.submit(dict(self.request, editMode='strict',
                editingScript='画面：食物特写\n备注：保留反应和笑点',
                scriptPreparation={'strictReady': True, 'targets': [{'text': '好吃'}]}))
        self.assertFalse(self.jobs)
        self.assertEqual(list((self.root/'jobs').iterdir()), [])

    def test_zero_match_message_is_not_a_format_error(self):
        with self.assertRaisesRegex(ValueError, '没有在素材转写中找到可靠匹配'):
            select([edit_tests.fixture()], '达人（微笑）：今天我们来挑选冬天的衣服', 30, 'strict')


class RecoveredScriptCandidateTests(unittest.TestCase):
    def prepared(self):
        return {'assetId':'a','source':'/synthetic/a.mov','start':2,'end':4,'text':'把电池扣下来',
                'words':[],'voiceRole':'unknown','scriptVisualReview':True,'scriptTargetIds':[1]}

    def test_recovered_script_dialogue_survives_timeline_assembly(self):
        item=edit_tests.fixture('把电池扣下来')
        item['vision']={'kind':'环境','summary':'树木与路面'}
        plan=automatic_plan([item],'test',30,[self.prepared()])
        self.assertEqual(len(plan['selectedSpeech']),1)
        self.assertEqual(plan['selectedSpeech'][0]['text'],'把电池扣下来')
        self.assertEqual(plan['duration'],2)

    def test_unmarked_or_explicit_broll_is_not_promoted(self):
        item=edit_tests.fixture('把电池扣下来')
        item['vision']={'kind':'环境'}
        for scenario in ('no-proof','explicit-broll','no-video'):
            with self.subTest(scenario=scenario):
                media=dict(item); row=self.prepared()
                if scenario=='no-proof':row.pop('scriptVisualReview')
                elif scenario=='explicit-broll':media['source']='/synthetic/空镜/a.mov'
                else:media['video']=False
                with self.assertRaises(ValueError):automatic_plan([media],'test',30,[row])

    def test_mixed_selection_cannot_silently_drop_one_required_take(self):
        first=edit_tests.fixture('把电池扣下来')
        second=dict(first,assetId='b',source='/synthetic/b.mov',vision={'kind':'环境'})
        rows=[dict(self.prepared(),scriptVisualReview=False),
              dict(self.prepared(),assetId='b',source=second['source'],scriptVisualReview=False)]
        with self.assertRaisesRegex(ValueError,'组装遗漏'):
            automatic_plan([first,second],'test',30,rows)


class PreparationEndpointTests(unittest.TestCase):
    def post(self, text, authenticated=True):
        import server
        handler = object.__new__(server.Handler)
        raw = json.dumps({'text': text}, ensure_ascii=False).encode()
        handler.headers = {'Content-Length': str(len(raw))}
        handler.path = '/api/intake/prepare'
        handler.rfile = io.BytesIO(raw)
        handler.auth = Mock(return_value=authenticated)
        handler.reply = Mock()
        with patch.object(server, 'submit', side_effect=AssertionError('No editing task may start')):
            handler.do_POST()
        return handler.reply.call_args.args

    def test_preparation_uses_same_targets_as_actual_selection(self):
        original = '1、达人（微笑）：这个虾饺真好吃\n闺蜜：“给我也来一个！”'
        status, body = self.post(original)
        self.assertEqual(status, 200)
        self.assertEqual(body['sourceText'], original)
        self.assertEqual(body['targets'], targets(original))
        self.assertEqual(body['count'], 2)

    def test_endpoint_still_requires_authentication(self):
        self.assertEqual(self.post('达人：你好', authenticated=False)[0], 401)

    def test_directions_return_explanation_not_invented_dialogue(self):
        status, body = self.post('镜头：达人试吃\n拍摄要求：突出好吃，保留互动')
        self.assertEqual(status, 200)
        self.assertFalse(body['strictReady'])
        self.assertFalse(body['targets'])
        self.assertTrue(body['warnings'])

    def test_invalid_text_is_rejected(self):
        for text in ({}, ['达人：你好'], 123, None, '字' * 100001):
            with self.subTest(value=type(text).__name__):
                self.assertEqual(self.post(text)[0], 400)


if __name__ == '__main__':
    unittest.main()
