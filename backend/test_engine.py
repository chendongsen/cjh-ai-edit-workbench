"""Unit safety checks. Run: .venv/bin/python -m unittest discover -s backend -p 'test_*.py'."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from planner import build, number, title_image
from analyze import frames
from PIL import Image


class PlannerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        self.assets = {'one': {'id': 'one', 'name': 'one.mp4', 'path': str(self.folder / 'one.mp4')}}
        self.plan = {'project': 'test', 'selectedSpeech': [{'assetId': 'one', 'start': 1, 'end': 5, 'role': '达人'}], 'foodPlacements': [], 'packaging': []}

    def tearDown(self):
        self.temp.cleanup()

    def build(self):
        with patch('planner.asset_for', side_effect=lambda assets, id: assets[id]), patch('planner.probe', return_value={'video': True, 'audio': True, 'duration': 10}):
            return build({'plan': self.plan}, self.assets, self.folder, 'test-job')

    def test_nan_rejected(self):
        for bad in ('nan', 'inf', -1):
            with self.assertRaises(ValueError):
                number(bad, 'test')

    def test_roles_required(self):
        self.plan['selectedSpeech'][0]['role'] = '待确认'
        with self.assertRaises(ValueError):
            self.build()

    def test_source_bounds(self):
        self.plan['selectedSpeech'][0]['end'] = 50
        with self.assertRaises(ValueError):
            self.build()

    def test_primary_and_food(self):
        self.plan['foodPlacements'] = [{'assetId': 'one', 'start': 2, 'end': 3, 'at': 1}]
        clips = self.build()['clips']
        self.assertEqual(clips[0]['duration'], 4)
        self.assertEqual(clips[1]['track'], 2)
        self.assertEqual(clips[1]['mediaType'], 1)

    def test_overlap_rejected(self):
        self.plan['foodPlacements'] = [{'assetId': 'one', 'start': 2, 'end': 4, 'at': 0}, {'assetId': 'one', 'start': 2, 'end': 4, 'at': 1}]
        with self.assertRaises(ValueError):
            self.build()

    def test_unsupported_reported_not_executed(self):
        self.plan['packaging'] = [{'id': 'x', 'type': '回放', 'at': 1, 'duration': 1, 'text': '再看一遍'}]
        result = self.build()
        self.assertEqual(len(result['clips']), 1)
        self.assertTrue(result['warnings'])

    def test_overlay_is_real_image(self):
        path = self.folder / 'test.png'
        title_image('工作台测试', path, 1080, 1920)
        self.assertGreater(path.stat().st_size, 1000)

    def test_static_image_frames_exist_without_video_seek(self):
        path = self.folder / 'portrait.jpg'
        Image.new('RGB', (100, 200), 'red').save(path)
        output = self.folder / 'frames'
        output.mkdir()
        with patch('analyze.DATA', self.folder), patch('analyze.subprocess.run') as command:
            result = frames(path, 0, output)
        self.assertEqual(len(result), 3)
        command.assert_not_called()
        for frame in result:
            self.assertTrue((self.folder / frame['file']).is_file())
            self.assertEqual(frame['at'], 0)


if __name__ == '__main__':
    unittest.main()
