"""Verified frame writes. Retry only our newly inserted, non-ripple A/V group."""
import math


def timeline_items(timeline):
    return {item.GetUniqueId(): item
            for kind in ('video', 'audio')
            for index in range(1, timeline.GetTrackCount(kind) + 1)
            for item in (timeline.GetItemListInTrack(kind, index) or [])}


def read_item(item):
    return {'id': item.GetUniqueId(), 'track': item.GetTrackTypeAndIndex(),
            'start': float(item.GetStart(True)), 'end': float(item.GetEnd(True)),
            'duration': float(item.GetDuration(True)),
            'sourceStartFrame': item.GetSourceStartFrame(),
            'sourceEndFrame': item.GetSourceEndFrame(), 'speed': item.GetSpeed()}


def append_verified(pool, timeline, args, expected, source_fps, timeline_fps,
                    source_frames, ensure_current, receipt):
    """Fix small resampling boundary differences, never relax output checks.

    Resolve 21.1 floors some mixed-rate ranges (925 source frames at 59.94
    produce 462 frames at 30). Endpoint inclusion is not specified by its API.
    Search adjacent source endpoints and verify exact output instead of assuming
    a universal +/-1 rule. Keep source in-point, record position and speed fixed.
    """
    if not 0 < source_fps <= 1000 or expected < 1:
        raise ValueError('无效源帧率或目标片段长度')
    ratio = source_fps / timeline_fps
    original_end = args['endFrame']
    # One output-frame allowance at EOF is needed for Resolve's quantized tail.
    # The downstream exporter checks actual file duration and final sample too.
    limit = source_frames + math.ceil(ratio) if source_frames else None
    if limit and original_end > limit + 1e-6:
        raise ValueError('计划出点超出素材尾部一帧以上，不能自动补帧')
    ensure_current()
    before = timeline_items(timeline)
    attempts, tried = [], set()
    receipt.update(expectedFrames=expected, recordFrame=args['recordFrame'],
                   sourceFPS=source_fps, sourceFrames=source_frames, attempts=attempts)
    end = original_end
    budget = min(32, max(8, math.ceil(ratio * 4) + 3))
    for _ in range(budget):
        ensure_current()
        tried.add(end)
        request = dict(args, endFrame=end)
        returned = pool.AppendToTimeline([request])
        ensure_current()
        after = timeline_items(timeline)
        if not before.keys() <= after.keys():
            raise RuntimeError('写入影响了已有片段，已停止；独立项目保留供检查')
        added = {key: value for key, value in after.items() if key not in before}
        if not returned or not added:
            raise RuntimeError('达芬奇写入时间线失败，已停止')
        if any(item.GetUniqueId() not in added for item in returned):
            raise RuntimeError('达芬奇返回的片段不属于本次写入，已停止')
        rows = [read_item(item) for item in added.values()]
        attempts.append({'startFrame':args['startFrame'], 'endFrame':end, 'items':rows})
        primary = returned[0]
        actual = float(primary.GetDuration(True))
        correct = all(abs(row['start'] - args['recordFrame']) < 1e-4
                      and abs(row['duration'] - expected) < 1e-4
                      and abs(row['end'] - args['recordFrame'] - expected) < 1e-4
                      and abs(float((row['speed'] or {}).get('Percentage', 100)) - 100) < 1e-4
                      for row in rows)
        if correct:
            receipt['adjustedSourceFrames'] = end - original_end
            receipt['verifiedItems'] = rows
            return primary
        if any(abs(row['start'] - args['recordFrame']) > 1e-4 for row in rows):
            raise RuntimeError('达芬奇片段位置不符，已停止；未移动后续切点')
        delta = expected - actual
        if abs(delta) > 2 or abs(delta) < 1e-4:
            raise RuntimeError(f'音视频帧数不一致或偏差过大（期望 {expected}，实际 {actual}），已保留项目')
        direction = 1 if delta > 0 else -1
        candidate = end + direction
        if candidate in tried or candidate <= args['startFrame'] or (limit and candidate > limit):
            raise RuntimeError(f'素材边界无法满足 {expected} 帧；未变速或延长素材，已保留项目')
        if len(attempts) == budget:
            break
        ensure_current()
        # Delete exactly this attempt's added video AND audio, with no ripple.
        if timeline.DeleteClips(list(added.values()), False) is not True:
            raise RuntimeError('补偿前无法清除本次音视频片段，已停止避免叠音')
        ensure_current()
        if timeline_items(timeline).keys() != before.keys():
            raise RuntimeError('补偿清理回读不一致，已停止避免重复片段')
        end = candidate
    raise RuntimeError(f'有限帧数补偿未收敛（期望 {expected} 帧），已保留项目供检查')
