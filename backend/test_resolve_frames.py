"""Regression tests for exact Resolve cut lengths and bounded A/V retries.

The fake models the measured 59.94 -> 30 result without assuming a universal
inclusive/exclusive source-end rule. Append returns video only, while the
timeline contains a linked audio item as the real Resolve API does.
"""
import unittest

from resolve_frames import append_verified, timeline_items


class Item:
    def __init__(self, identifier, kind, at, duration, source_start=16,
                 source_end=941, end_offset=0, speed=100):
        self.identifier = identifier
        self.kind = kind
        self.at = at
        self.duration = duration
        self.source_start = source_start
        self.source_end = source_end
        self.end_offset = end_offset
        self.speed = speed

    def GetUniqueId(self):
        return self.identifier

    def GetTrackTypeAndIndex(self):
        return [self.kind, 1]

    def GetStart(self, subframe=False):
        return self.at

    def GetEnd(self, subframe=False):
        return self.at + self.duration + self.end_offset

    def GetDuration(self, subframe=False):
        return self.duration

    def GetSourceStartFrame(self):
        return self.source_start

    def GetSourceEndFrame(self):
        return self.source_end

    def GetSpeed(self):
        return {'Percentage': self.speed}


class Timeline:
    def __init__(self):
        self.items = [Item('earlier', 'video', 0, 200),
                      Item('later', 'video', 1000, 50)]
        self.deleted = []
        self.delete_ok = True
        self.leave_audio = False

    def GetTrackCount(self, kind):
        return 1

    def GetItemListInTrack(self, kind, index):
        return [item for item in self.items if item.kind == kind]

    def DeleteClips(self, items, ripple=False):
        ids = {item.GetUniqueId() for item in items}
        self.deleted.append((ids, ripple))
        if not self.delete_ok:
            return False
        self.items = [item for item in self.items
                      if item.GetUniqueId() not in ids
                      or (self.leave_audio and item.kind == 'audio')]
        return True


class Pool:
    def __init__(self, timeline, lengths, audio_delta=0, position_delta=0,
                 end_offset=0, speed=100):
        self.timeline = timeline
        self.lengths = lengths
        self.audio_delta = audio_delta
        self.position_delta = position_delta
        self.end_offset = end_offset
        self.speed = speed
        self.calls = []
        self.return_empty = False
        self.return_existing = False
        self.drop_existing = False

    def AppendToTimeline(self, requests):
        request = dict(requests[0])
        self.calls.append(request)
        if self.return_empty:
            return []
        if self.return_existing:
            return [self.timeline.items[0]]
        if self.drop_existing:
            self.timeline.items.pop(0)
        duration = self.lengths(request['endFrame'])
        number = len(self.calls)
        created = []
        kinds = ('video', 'audio') if not request.get('mediaType') else (
            'video' if request['mediaType'] == 1 else 'audio',)
        for kind in kinds:
            item = Item(f'{kind}-{number}', kind,
                        request['recordFrame'] + self.position_delta,
                        duration + (self.audio_delta if kind == 'audio' else 0),
                        request['startFrame'], request['endFrame'],
                        self.end_offset, self.speed)
            self.timeline.items.append(item)
            created.append(item)
        return created[:1]


class VerifiedAppendTests(unittest.TestCase):
    def setUp(self):
        self.timeline = Timeline()
        self.args = {'mediaPoolItem': object(), 'startFrame': 16,
                     'endFrame': 941, 'recordFrame': 383, 'trackIndex': 1}
        self.receipt = {}
        self.checks = 0

    def ensure_current(self):
        self.checks += 1

    def append(self, pool, expected=463, source_fps=59.94,
               timeline_fps=30, source_frames=1236, ensure=None):
        return append_verified(pool, self.timeline, self.args, expected,
                               source_fps, timeline_fps, source_frames,
                               ensure or self.ensure_current, self.receipt)

    def test_measured_5994_to_30_short_frame_retries_complete_av_group(self):
        original = [(i.identifier, i.at, i.duration) for i in self.timeline.items]
        pool = Pool(self.timeline, lambda end: 462 if end == 941 else 463)
        primary = self.append(pool)
        self.assertEqual(primary.GetDuration(True), 463)
        self.assertEqual([c['endFrame'] for c in pool.calls], [941, 942])
        self.assertEqual(self.timeline.deleted,
                         [({'video-1', 'audio-1'}, False)])
        self.assertEqual({i.identifier for i in self.timeline.items},
                         {'earlier', 'later', 'video-2', 'audio-2'})
        self.assertEqual([(i.identifier, i.at, i.duration)
                          for i in self.timeline.items[:2]], original)
        for call in pool.calls:
            self.assertEqual(call['recordFrame'], 383)
            self.assertEqual(call['startFrame'], 16)
            self.assertEqual(call['trackIndex'], 1)
        self.assertEqual(self.receipt['adjustedSourceFrames'], 1)
        self.assertEqual(len(self.receipt['attempts']), 2)
        self.assertEqual(len(self.receipt['verifiedItems']), 2)
        self.assertTrue(all(row['duration'] == 463
                            for row in self.receipt['verifiedItems']))
        self.assertEqual(self.args['endFrame'], 941, 'caller request is immutable')

    def test_same_rate_one_extra_frame_retries_negative_endpoint(self):
        self.args.update(startFrame=0, endFrame=30)
        pool = Pool(self.timeline, lambda end: end + 1)
        self.append(pool, expected=30, source_fps=30, source_frames=90)
        self.assertEqual([c['endFrame'] for c in pool.calls], [30, 29])
        self.assertEqual(self.receipt['adjustedSourceFrames'], -1)
        self.assertEqual(len(self.timeline.deleted), 1)

    def test_correct_half_rate_result_needs_no_retry(self):
        self.args.update(startFrame=4, endFrame=254)
        pool = Pool(self.timeline, lambda end: 125)
        self.append(pool, expected=125, source_fps=60, source_frames=252)
        self.assertEqual(len(pool.calls), 1)
        self.assertEqual(self.timeline.deleted, [])
        self.assertEqual(self.receipt['adjustedSourceFrames'], 0)

    def test_existing_eof_allowance_does_not_keep_extending_source(self):
        self.args.update(startFrame=4, endFrame=254)
        pool = Pool(self.timeline, lambda end: 124)
        with self.assertRaisesRegex(RuntimeError, '素材边界'):
            self.append(pool, expected=125, source_fps=60, source_frames=252)
        self.assertEqual(len(pool.calls), 1)
        self.assertEqual(self.timeline.deleted, [], 'last attempt is preserved')

    def test_invalid_initial_eof_stops_before_mutation(self):
        self.args.update(startFrame=4, endFrame=255)
        pool = Pool(self.timeline, lambda end: 125)
        with self.assertRaisesRegex(ValueError, '素材尾部'):
            self.append(pool, expected=125, source_fps=60, source_frames=252)
        self.assertEqual(pool.calls, [])

    def test_nonconvergence_is_bounded_and_leaves_one_diagnostic_attempt(self):
        pool = Pool(self.timeline, lambda end: 462)
        with self.assertRaisesRegex(RuntimeError, '有限帧数补偿'):
            self.append(pool)
        self.assertLessEqual(len(pool.calls), 32)
        self.assertEqual(len(self.timeline.deleted), len(pool.calls) - 1)
        self.assertEqual(len(self.timeline.items), 4)

    def test_adjacent_endpoint_oscillation_stops_without_duplicate_av(self):
        pool = Pool(self.timeline, lambda end: 462 if end == 941 else 464)
        with self.assertRaisesRegex(RuntimeError, '素材边界'):
            self.append(pool)
        self.assertEqual([c['endFrame'] for c in pool.calls], [941, 942])
        self.assertEqual(len(self.timeline.items), 4)

    def test_delete_failure_cannot_cause_second_append_or_duplicate_audio(self):
        self.timeline.delete_ok = False
        pool = Pool(self.timeline, lambda end: 462)
        with self.assertRaisesRegex(RuntimeError, '无法清除'):
            self.append(pool)
        self.assertEqual(len(pool.calls), 1)
        self.assertEqual(len(self.timeline.items), 4)

    def test_delete_that_leaves_audio_is_detected_before_retry(self):
        self.timeline.leave_audio = True
        pool = Pool(self.timeline, lambda end: 462)
        with self.assertRaisesRegex(RuntimeError, '清理回读不一致'):
            self.append(pool)
        self.assertEqual(len(pool.calls), 1)

    def test_project_switch_before_cleanup_stops_without_deletion(self):
        def guard():
            self.checks += 1
            if self.checks == 4:
                raise RuntimeError('项目已切换')
        pool = Pool(self.timeline, lambda end: 462)
        with self.assertRaisesRegex(RuntimeError, '项目已切换'):
            self.append(pool, ensure=guard)
        self.assertEqual(len(pool.calls), 1)
        self.assertEqual(self.timeline.deleted, [])

    def test_project_switch_after_append_stops_without_any_cleanup(self):
        def guard():
            self.checks += 1
            if self.checks == 3:
                raise RuntimeError('项目已切换')
        pool = Pool(self.timeline, lambda end: 462)
        with self.assertRaisesRegex(RuntimeError, '项目已切换'):
            self.append(pool, ensure=guard)
        self.assertEqual(len(pool.calls), 1)
        self.assertEqual(self.timeline.deleted, [])

    def test_empty_append_fails_without_retries(self):
        pool = Pool(self.timeline, lambda end: 463)
        pool.return_empty = True
        with self.assertRaisesRegex(RuntimeError, '写入时间线失败'):
            self.append(pool)
        self.assertEqual(len(pool.calls), 1)

    def test_disappearance_of_existing_clip_stops(self):
        pool = Pool(self.timeline, lambda end: 463)
        pool.drop_existing = True
        with self.assertRaisesRegex(RuntimeError, '已有片段'):
            self.append(pool)
        self.assertEqual(self.timeline.deleted, [])

    def test_position_mismatch_is_not_fixed_by_changing_source_end(self):
        pool = Pool(self.timeline, lambda end: 463, position_delta=1)
        with self.assertRaisesRegex(RuntimeError, '位置不符'):
            self.append(pool)
        self.assertEqual(len(pool.calls), 1)

    def test_audio_mismatch_cannot_be_accepted_when_video_is_correct(self):
        pool = Pool(self.timeline, lambda end: 463, audio_delta=-1)
        with self.assertRaisesRegex(RuntimeError, '音视频帧数不一致'):
            self.append(pool)
        self.assertNotIn('verifiedItems', self.receipt)

    def test_correct_duration_with_wrong_end_is_rejected(self):
        pool = Pool(self.timeline, lambda end: 463, end_offset=1)
        with self.assertRaises(RuntimeError):
            self.append(pool)
        self.assertNotIn('verifiedItems', self.receipt)

    def test_speed_change_is_never_accepted_as_frame_compensation(self):
        pool = Pool(self.timeline, lambda end: 463, speed=99.8)
        with self.assertRaises(RuntimeError):
            self.append(pool)
        self.assertNotIn('verifiedItems', self.receipt)

    def test_half_frame_error_is_not_silently_tolerated(self):
        pool = Pool(self.timeline, lambda end: 462.6)
        with self.assertRaises(RuntimeError):
            self.append(pool)
        self.assertNotIn('verifiedItems', self.receipt)

    def test_large_frame_error_stops_without_adjusting_source(self):
        pool = Pool(self.timeline, lambda end: 460)
        with self.assertRaisesRegex(RuntimeError, '偏差过大'):
            self.append(pool)
        self.assertEqual(len(pool.calls), 1)

    def test_explicit_video_only_or_audio_only_does_not_invent_other_track(self):
        for media_type, kind in ((1, 'video'), (2, 'audio')):
            with self.subTest(media_type=media_type):
                self.timeline = Timeline()
                self.args['mediaType'] = media_type
                pool = Pool(self.timeline, lambda end: 462 if end == 941 else 463)
                self.append(pool)
                self.assertEqual(self.timeline.deleted, [({f'{kind}-1'}, False)])
                self.assertEqual(len(self.receipt['verifiedItems']), 1)
                self.assertEqual(self.receipt['verifiedItems'][0]['track'][0], kind)

    def test_invalid_rate_or_length_is_rejected_before_append(self):
        for rate, expected in ((0, 463), (-30, 463), (float('nan'), 463),
                               (float('inf'), 463), (59.94, 0)):
            with self.subTest(rate=rate, expected=expected):
                pool = Pool(self.timeline, lambda end: 463)
                with self.assertRaises(ValueError):
                    self.append(pool, source_fps=rate, expected=expected)
                self.assertEqual(pool.calls, [])


if __name__ == '__main__':
    unittest.main()
