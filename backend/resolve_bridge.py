"""Runs in Resolve's own Python. Only creates NEW projects/timelines."""
import json
import math
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import DaVinciResolveScript as dvr
from resolve_lifecycle import preserve_current
from project_naming import make_project_naming
from resolve_frames import append_verified, timeline_items, read_item


def connect():
    r = dvr.scriptapp('Resolve')
    if not r:
        raise RuntimeError('无法连接达芬奇。请打开 Studio，在偏好设置→系统→常规→外部脚本选择 Local（本地）。')
    return r


def status():
    r = connect()
    p = r.GetProjectManager().GetCurrentProject()
    return {'connected': True, 'version': r.GetVersionString(),
            'project': p.GetName() if p else None}


def execute(data):
    r = connect()
    pm = r.GetProjectManager()
    # Isolate every execution, including test jobs, from the editor's current project.
    name = data.get('projectName') or make_project_naming(
        [{'path':c['path']} for c in data.get('clips',[])], data['jobId'])['projectName']
    preserved = preserve_current(pm,name)
    print('自动创建达芬奇项目：'+name, flush=True)
    if preserved.get('renamed'):
        print('原未命名项目已自动命名保留：'+preserved['preservedName'],flush=True)
    p = pm.CreateProject(name)
    if not p:
        raise RuntimeError('无法创建独立项目；没有更改原项目')
    fps = int(data.get('fps', 30))
    for key, value in [('timelineFrameRate', str(fps)), ('timelineResolutionWidth', str(data['width'])),
                       ('timelineResolutionHeight', str(data['height']))]:
        if not p.SetSetting(key, value):
            raise RuntimeError('达芬奇拒绝项目设置：' + key)
    pool = p.GetMediaPool()
    timeline = pool.CreateEmptyTimeline(name)
    if not timeline or not p.SetCurrentTimeline(timeline):
        raise RuntimeError('无法创建新时间线')
    timeline.SetStartTimecode('00:00:00:00')
    while timeline.GetTrackCount('video') < 4:
        if not timeline.AddTrack('video'):
            raise RuntimeError('无法创建视频轨道')
    while timeline.GetTrackCount('audio') < 2:
        if not timeline.AddTrack('audio', 'stereo'):
            raise RuntimeError('无法创建音频轨道')
    for i, title in enumerate(['人物主线', '食物与环境补充', '人物强调', '综艺花字'], 1):
        timeline.SetTrackName('video', i, title)
    cache, actual, warnings = {}, [], list(data.get('warnings', []))
    frame_receipts = []
    origin = timeline.GetStartFrame()

    def ensure_current():
        current = pm.GetCurrentProject()
        current_timeline = current.GetCurrentTimeline() if current else None
        if (not current or current.GetUniqueId() != p.GetUniqueId() or
                not current_timeline or current_timeline.GetUniqueId() != timeline.GetUniqueId()):
            raise RuntimeError('达芬奇项目或时间线已切换；停止写入，未修改其他项目')

    def append(clip):
        path = clip['path']
        if path not in cache:
            imported = pool.ImportMedia([path])
            if not imported:
                raise RuntimeError('达芬奇导入失败：' + Path(path).name)
            cache[path] = imported[0]
        item = cache[path]
        props = item.GetClipProperty()
        source_fps = float(props.get('FPS') or fps)
        begin = round(clip['start'] * source_fps)
        length = max(1, round(clip['duration'] * source_fps))
        args = {'mediaPoolItem': item, 'startFrame': begin, 'endFrame': begin + length,
                'recordFrame': origin + round(clip['at'] * fps), 'trackIndex': clip['track']}
        if clip.get('mediaType'):
            args['mediaType'] = clip['mediaType']
        expected = round(clip['duration'] * fps)
        receipt = {'file':path, 'sourceFPSRaw':props.get('FPS')}
        frame_receipts.append(receipt)
        source_frames = int(props.get('Frames') or 0) if props.get('Frames') else None
        timeline_item = append_verified(pool, timeline, args, expected, source_fps, fps,
                                        source_frames, ensure_current, receipt)
        if receipt['adjustedSourceFrames']:
            print(f'帧率边界已校正：{Path(path).name}，音视频均已核对为 {expected} 帧', flush=True)
        if clip.get('zoom'):
            if not timeline_item.SetProperties({'ZoomX': clip['zoom'], 'ZoomY': clip['zoom']}):
                raise RuntimeError('画面已写入，但放大参数设置失败')
        if clip.get('volume') is not None:
            if not timeline_item.SetProperties({'AudioVolumeEnabled': True, 'AudioVolume': clip['volume']}):
                warnings.append('音效已加入，但音量设置未确认，请人工核对：' + Path(path).name)
        duration = float(timeline_item.GetDuration(True))
        actual.append({'kind': clip['kind'], 'file': Path(path).name, 'at': clip['at'],
                       'durationFrames': duration, 'track': clip['track'], 'effectId': clip.get('effectId')})
        print(f'已写入 {clip["kind"]}：{Path(path).name}', flush=True)

    try:
        for clip in data['clips']:
            append(clip)
        # Later inserts must not have trimmed/overwritten an earlier verified clip.
        ensure_current()
        final_items = timeline_items(timeline)
        verified_items = {row['id']:row for receipt in frame_receipts for row in receipt['verifiedItems']}
        if final_items.keys() != verified_items.keys():
            raise RuntimeError('最终音视频片段数量不符，未确认本次粗剪完成')
        for identifier, expected_row in verified_items.items():
            row = read_item(final_items[identifier])
            if any(row[key] != expected_row[key] for key in ('track','start','end','duration','sourceStartFrame','sourceEndFrame')):
                raise RuntimeError('后续写入改变了先前切点，已保留独立项目供检查')
        for mark in data.get('markers', []):
            if not timeline.AddMarker(round(mark['at'] * fps), 'Yellow', mark['type'],
                                      mark['text'], max(1, round(mark['duration'] * fps))):
                warnings.append('标记未写入：' + mark['text'])
        current=pm.GetCurrentProject()
        if not current or current.GetUniqueId()!=p.GetUniqueId():
            raise RuntimeError('写入期间达芬奇项目已切换，未保存其他项目；请检查本次粗剪')
        if not pm.SaveProject():
            raise RuntimeError('时间线已生成，但项目保存失败')
        current=pm.GetCurrentProject()
        if not current or current.GetUniqueId()!=p.GetUniqueId():
            raise RuntimeError('保存期间达芬奇项目已切换，未确认本次粗剪完成')
        r.OpenPage('edit')
        track_items = {kind: [len(timeline.GetItemListInTrack(kind, i) or []) for i in range(1, timeline.GetTrackCount(kind) + 1)] for kind in ('video', 'audio')}
        return {'project': name, 'timeline': timeline.GetName(), 'projectId': p.GetUniqueId(),
                'timelineId': timeline.GetUniqueId(), 'fps': fps, 'actual': actual,
                'warnings': warnings, 'preservedCurrentProject':preserved,
                'frameVerification':frame_receipts,
                'verified': {'videoTracks': timeline.GetTrackCount('video'),
                'audioTracks': timeline.GetTrackCount('audio'), 'itemsPerTrack': track_items, 'durationFrames': timeline.GetEndFrame() - origin},
                'note': '结构与片段长度已回读，声音/构图/笑点仍需播放验收；尚未导出成片。'}
    except Exception:
        current=pm.GetCurrentProject()
        if current and current.GetUniqueId()==p.GetUniqueId():
            pm.SaveProject()
        raise
    finally:
        if data.get('_frameDiagnosticsPath'):
            Path(data['_frameDiagnosticsPath']).write_text(
                json.dumps(frame_receipts,ensure_ascii=False,indent=2),encoding='utf-8')


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'status':
            print(json.dumps(status(), ensure_ascii=False))
        else:
            path = Path(sys.argv[1])
            data=json.loads(path.read_text(encoding='utf-8'))
            if not data.get('projectName'):
                # Also upgrade older installed workers without regenerating the
                # name on a later retry. The existing request timestamp is stable.
                naming=make_project_naming([{'path':c['path']} for c in data.get('clips',[])],
                    data['jobId'],path.stat().st_mtime)
                data.update(projectName=naming['projectName'],projectNaming=naming)
                temporary=path.with_suffix('.naming.tmp')
                temporary.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
                temporary.replace(path)
            data['_frameDiagnosticsPath'] = str(path.parent / 'frame-verification.json')
            result = execute(data)
            result_path = path.parent / 'result.json'
            temporary = result_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(result_path)
    except Exception as e:
        print(str(e), file=sys.stderr, flush=True)
        sys.exit(1)
