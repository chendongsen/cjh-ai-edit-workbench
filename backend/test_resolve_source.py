"""Offline source timebase regressions; never connect to Resolve."""
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from resolve_source import relative_source_start, source_rate, timecode_origin


def mp4_properties(**changes):
    props = {'FPS': 59.94, 'Start': '0', 'Frames': '1236',
             'Start TC': '22:53:08;50', 'Drop frame': '1'}
    props.update(changes)
    return props


class SourceTimeTests(unittest.TestCase):
    def test_actual_probe_df_origin_matches_source_zero_readback(self):
        props = mp4_properties()
        self.assertAlmostEqual(timecode_origin(props), 82388.73976666667, places=9)
        self.assertAlmostEqual(relative_source_start(82388.73976666667, 0, props), 0, places=9)

    def test_actual_probe_preserves_time_precision_instead_of_integer_frame(self):
        value = relative_source_start(82389.0067, 15, mp4_properties())
        self.assertAlmostEqual(value, 0.2669333333333333, places=9)
        self.assertGreater(value, 15 / float(source_rate(59.94)))

    def test_already_relative_readback_is_not_offset_twice(self):
        value = relative_source_start(0.2669333333333333, 15, mp4_properties())
        self.assertEqual(value, 0.2669333333333333)

    def test_zero_tc_mov_retains_original_time(self):
        props = {'FPS': 60, 'Start': '0', 'Frames': '252', 'Start TC': '00:00:00:00'}
        self.assertEqual(relative_source_start(4 / 60, 4, props), 4 / 60)

    def test_audio_without_video_fps_keeps_subframe_source_time(self):
        value = 1.23456789
        self.assertEqual(relative_source_start(value, None, {'Start TC': '00:00:00:00'}), value)

    def test_fractional_rate_ndf_uses_elapsed_frames_not_clock_seconds(self):
        props = {'FPS': '23.976', 'Start TC': '00:01:00:00', 'Drop frame': '0',
                 'Frames': '1000', 'Start': '0'}
        self.assertEqual(timecode_origin(props), 60.06)
        self.assertAlmostEqual(relative_source_start(61.061, 24, props), 1.001, places=10)

    def test_2997_drop_frame_minute_and_tenth_minute(self):
        self.assertAlmostEqual(timecode_origin({'FPS': '29.97', 'Start TC': '00:01:00;02'}), 60.06)
        self.assertAlmostEqual(timecode_origin({'FPS': '29.97', 'Start TC': '00:10:00;00'}), 599.9994)
        self.assertAlmostEqual(timecode_origin({'FPS': '29.97', 'Start TC': '01:00:00;00'}), 3599.9964)

    def test_5994_drop_flag_with_colon_is_honored(self):
        self.assertEqual(timecode_origin(mp4_properties(**{'Start TC': '22:53:08:50'})),
                         82388.73976666667)

    def test_numeric_start_is_not_subtracted_from_timecode_seconds(self):
        props = mp4_properties(Start='90000')
        self.assertAlmostEqual(relative_source_start(82389.0067, 15, props),
                               0.2669333333333333, places=9)

    def test_absolute_frame_coordinate_requires_file_bounds_evidence(self):
        props = mp4_properties(Start='90000')
        self.assertAlmostEqual(relative_source_start(82389.0067, 90015, props),
                               0.2669333333333333, places=9)

    def test_unknown_or_ambiguous_timebase_fails_instead_of_clamping(self):
        with self.assertRaisesRegex(ValueError, '时间基'):
            relative_source_start(500, 15, mp4_properties())
        with self.assertRaisesRegex(ValueError, '时间基'):
            relative_source_start(1, 60, {'FPS': 60, 'Start TC': '00:00:00:01', 'Frames': 600})
        with self.assertRaisesRegex(ValueError, '缺少'):
            relative_source_start(82389.0067, None, mp4_properties())

    def test_out_of_file_start_is_rejected_without_trimming(self):
        with self.assertRaisesRegex(ValueError, '范围'):
            relative_source_start(82388.73976666667 + 50, 3000, mp4_properties())

    def test_invalid_drop_frame_timecodes_are_not_silently_interpreted(self):
        for props in ({'FPS': 29.97, 'Start TC': '00:01:00;00'},
                      {'FPS': 59.94, 'Start TC': '00:01:00;03'},
                      {'FPS': 24, 'Start TC': '01:00:00;00'},
                      {'FPS': 59.94, 'Start TC': '22:53:08;50', 'Drop frame': '0'}):
            with self.subTest(props=props), self.assertRaises(ValueError):
                timecode_origin(props)

    def test_arbitrary_average_rate_is_not_snapped_to_standard_rate(self):
        self.assertEqual(float(source_rate('59.95241871530531')), 59.95241871530531)


class SnapshotIntegrationTests(unittest.TestCase):
    def test_linked_video_audio_keep_same_precise_source_start_and_duration(self):
        # Import with a stubbed bridge so this test cannot load the Resolve API.
        spec = importlib.util.spec_from_file_location('snapshot_under_test',
                                                     Path(__file__).with_name('resolve_snapshot.py'))
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {'resolve_bridge': SimpleNamespace(connect=Mock())}):
            spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / '2-1.MP4'
            source.touch()
            media_props = mp4_properties(**{'File Path': str(source)})
            media = Mock()
            media.GetClipProperty.side_effect = lambda key=None: media_props.get(key) if key else media_props
            item = Mock()
            item.GetClipEnabled.return_value = True
            item.GetMediaPoolItem.return_value = media
            item.GetName.return_value = '2-1.MP4'
            item.GetSpeed.return_value = {'Percentage': 100}
            item.GetProperties.return_value = {}
            item.GetFusionCompCount.return_value = 0
            item.GetStart.return_value = 383
            item.GetDuration.return_value = 463
            item.GetSourceStartTime.return_value = 82389.0067
            item.GetSourceStartFrame.return_value = 15
            timeline = Mock()
            timeline.GetName.return_value = 'timeline'
            timeline.GetUniqueId.return_value = 'timeline-id'
            timeline.GetSetting.side_effect = {'timelineFrameRate': '30', 'timelineResolutionWidth': '1080',
                                               'timelineResolutionHeight': '1920'}.get
            timeline.GetStartFrame.return_value = 0
            timeline.GetEndFrame.return_value = 846
            timeline.GetTrackCount.side_effect = lambda kind: 0 if kind == 'subtitle' else 1
            timeline.GetIsTrackEnabled.return_value = True
            timeline.GetItemListInTrack.return_value = [item]
            timeline.GetTrackName.return_value = 'track'
            project = Mock()
            project.GetName.return_value = 'project'
            project.GetUniqueId.return_value = 'project-id'
            with patch.object(module, 'current', return_value=(None, project, timeline)):
                result = module.snapshot({'projectId': 'project-id', 'timelineId': 'timeline-id'})
            video, audio = [track['clips'][0] for track in result['tracks']]
            self.assertAlmostEqual(video['sourceStart'], 0.2669333333333333, places=9)
            self.assertEqual(video['sourceStart'], audio['sourceStart'])
            self.assertEqual(video['duration'], 463 / 30)
            self.assertEqual(audio['duration'], 463 / 30)
            self.assertEqual(video['at'], 383 / 30)
            timeline.DeleteClips.assert_not_called()


if __name__ == '__main__':
    unittest.main()
