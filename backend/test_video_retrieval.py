import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import video_retrieval as retrieval


class VideoRetrievalTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.output = self.root/'job'
        self.output.mkdir()
        self.main = self.item('main', '场次/人物/主线.mov')
        self.food = self.item('food', '场次/菜品/IMG_0001.mov')
        self.other = self.item('other', '另一家餐厅/菜品/IMG_0002.mov')
        self.items = [self.main, self.food, self.other]
        self.block = {'item': self.main, 'clip': {'text': '厨师现烤，上菜很快'},
                      'tags': set(), 'generic': False, 'topics': ['后厨制作', '上菜过程']}

    def tearDown(self):
        self.tmp.cleanup()

    def item(self, identifier, relative, duration=15.1):
        path = self.root/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'placeholder media for non-model tests')
        return {'assetId': identifier, 'source': str(path), 'video': True,
                'duration': duration, 'fps': 30, 'vision': {'kind': '人物'}}

    def report(self, text, scores=None):
        windows = [w for i in self.items for w in retrieval.windows_for(i)]
        for w in windows:
            w['frames'] = [str(self.root/'frame.jpg')]*4
        scores = scores or {i['assetId']: .7 for i in self.items}
        hits = [{'windowId': w['id'], 'score': scores[w['assetId']]} for w in windows]
        hits.sort(key=lambda h: -h['score'])
        report = {'status': 'complete', 'revision': retrieval.REVISION,
                  'windows': windows, 'queries': [{'id': retrieval._key(text),
                                                   'text': text, 'hits': hits}]}
        (self.output/retrieval.REPORT).write_text(json.dumps(report))
        return report

    def test_index_covers_unlabelled_static_food_and_tail(self):
        windows = retrieval.windows_for(self.food)
        self.assertEqual(windows[0]['start'], 0)
        self.assertEqual(windows[-1]['end'], self.food['duration'])
        for a, b in zip(windows, windows[1:]):
            self.assertLessEqual(b['start'], a['end'])
        for w in windows:
            self.assertEqual(len(w['frameTimes']), 4)
            self.assertTrue(all(w['start'] <= t < w['end'] for t in w['frameTimes']))
            self.assertEqual(w['frameTimes'], sorted(set(w['frameTimes'])))

    def test_invalid_duration_or_sampling_does_not_create_fake_windows(self):
        for duration in (float('nan'), float('inf'), -1, 0):
            with self.assertRaises(ValueError):
                retrieval.windows_for(dict(self.food, duration=duration))
        with self.assertRaises(ValueError):
            retrieval.windows_for(self.food, stride=8)
        with self.assertRaises(ValueError):
            retrieval.windows_for(self.food, frames=1)
        self.assertEqual(retrieval.windows_for(dict(self.food, excluded=True)), [])
        self.assertEqual(retrieval.windows_for(dict(self.food, duration=1)), [])

    def test_same_scene_retrieval_expands_unknown_kind_but_never_confirms(self):
        self.report(retrieval.broll_query(self.block), {'main': .99, 'food': .8, 'other': .95})
        options = retrieval.retrieved_options(self.block, self.items, {}, 30, self.output)
        self.assertTrue(options)
        self.assertTrue(all(o['item']['assetId'] == 'food' for o in options))
        self.assertTrue(all(o['semanticRetrieval'] and o['confidence'] == 0 for o in options))
        self.assertTrue(all('confirmed' not in o for o in options))
        self.assertTrue(all(o['retrievalQuery'] == retrieval.broll_query(self.block) for o in options))

    def test_occupied_and_manually_reviewed_windows_are_not_reintroduced(self):
        self.report(retrieval.broll_query(self.block))
        self.assertEqual(retrieval.retrieved_options(self.block, self.items,
                         {'food': [(0, 20)]}, 30, self.output), [])
        self.food['foodReview'] = {'reviewedBy': 'editor'}
        self.assertEqual(retrieval.retrieved_options(self.block, self.items, {}, 30, self.output), [])

    def test_explicit_conflicting_dish_name_is_not_overridden(self):
        self.food['source'] = str(self.root/'场次/菜品/虾饺.mov')
        Path(self.food['source']).write_bytes(b'other dish')
        self.block['tags'] = {'凤爪'}
        self.report(retrieval.broll_query(self.block))
        self.assertEqual(retrieval.retrieved_options(self.block, self.items, {}, 30, self.output), [])

    def test_script_retrieval_remains_broad_but_uses_temporal_windows(self):
        text = '厨师在后厨给烤串刷酱'
        self.report(text, {'main': .5, 'food': .8, 'other': .7})
        options = retrieval.script_options({'text': text}, self.items, 30, 2.4, self.output)
        self.assertEqual([o['item']['assetId'] for o in options[:2]], ['food', 'other'])
        self.assertTrue(all(abs(o['end']-o['start']-2.4) < 1/30+.001 for o in options if o['start'] < 12))
        self.assertTrue(all(o['semanticRetrieval'] for o in options))

    def test_retrieved_video_windows_try_center_front_and_tail_with_bounded_checks(self):
        text = '厨师在后半段刷酱'
        self.report(text, {'main': .5, 'food': .8, 'other': .7})
        options = retrieval.script_options({'text': text}, self.items, 30, 2.4, self.output)
        self.assertLessEqual(len(options), 18)
        top = [o for o in options[:6] if o['item']['assetId'] == 'food']
        self.assertEqual([o['retrievalEvidence']['placement'] for o in top], ['center', 'front', 'tail'])
        self.assertEqual([round(o['start'], 2) for o in top], [1.8, 0, 3.6])
        for option in options:
            evidence = option['retrievalEvidence']
            self.assertGreaterEqual(option['start'], evidence['sourceStart'])
            self.assertLessEqual(option['end'], evidence['sourceEnd'])
            self.assertEqual(evidence['query'], text)
            self.assertEqual(len(evidence['frameTimes']), 4)

    def test_missing_query_failed_job_and_source_change_fail_explicitly(self):
        self.report('first query')
        with self.assertRaisesRegex(ValueError, '查询没有完成'):
            retrieval.script_options({'text': 'second query'}, self.items, 30, 2.4, self.output)
        Path(self.food['source']).write_bytes(b'media changed')
        with self.assertRaisesRegex(ValueError, '素材在视频检索后发生变化'):
            retrieval.script_options({'text': 'first query'}, self.items, 30, 2.4, self.output)
        (self.output/retrieval.REPORT).write_text(json.dumps({'status': 'failed'}))
        with self.assertRaisesRegex(ValueError, '视频检索未完成'):
            retrieval.script_options({'text': 'first query'}, self.items, 30, 2.4, self.output)

    def test_queries_include_literal_script_components_and_actual_selected_speech(self):
        plan = {'selectedSpeech': [{'assetId': 'main', 'source': self.main['source'],
                'text': '现场现烤的肉串', 'start': 0, 'end': 6, 'at': 0}]}
        preparation = {'shotRequirements': [{'id': 'shot-1', 'text': '厨师拿起烤串，服务员递给顾客'}]}
        queries = retrieval.collect_queries(preparation, plan, self.items)
        texts = {q['text'] for q in queries}
        self.assertIn('厨师拿起烤串', texts)
        self.assertIn('服务员递给顾客', texts)
        self.assertTrue(any(t.startswith('现场现烤的肉串') for t in texts))
        self.assertEqual(len(queries), len({q['id'] for q in queries}))

    def test_enabled_missing_models_is_a_failure_not_old_algorithm_success(self):
        config = self.root/'config.json'
        config.write_text(json.dumps({'videoRetrieval': True, 'python': '/missing/python',
                                      'embeddingModel': '/missing/model'}))
        with patch.object(retrieval, 'CONFIG', config):
            with self.assertRaisesRegex(ValueError, '未就绪'):
                retrieval.prepare(self.items, {}, {}, self.output)
        report = json.loads((self.output/retrieval.REPORT).read_text())
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['editorialVerified'])

    def test_disabled_does_not_spawn_worker_or_write_new_report(self):
        with patch.object(retrieval, 'CONFIG', self.root/'absent.json'), patch.object(retrieval, '_worker') as worker:
            result = retrieval.prepare(self.items, {}, {'videoRetrieval': False}, self.output)
        self.assertEqual(result['status'], 'disabled')
        worker.assert_not_called()
        self.assertFalse((self.output/retrieval.REPORT).exists())


if __name__ == '__main__':
    unittest.main()
