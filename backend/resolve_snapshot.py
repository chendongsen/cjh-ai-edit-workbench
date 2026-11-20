"""Read the CURRENT Resolve timeline. No project switches or timeline mutations."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from resolve_bridge import connect
from resolve_source import relative_source_start


def current():
    r = connect()
    p = r.GetProjectManager().GetCurrentProject()
    t = p.GetCurrentTimeline() if p else None
    if not t:
        raise ValueError('请先在达芬奇打开粗剪项目，并选中要导出的时间线')
    return r, p, t


def identity(p, t):
    return {'project': p.GetName(), 'timeline': t.GetName(), 'timelineId': t.GetUniqueId(),
            'projectId': p.GetUniqueId()}


def snapshot(expected=None):
    r, p, t = current()
    result = identity(p, t)
    if expected and any(result[k] != expected.get(k) for k in ('projectId', 'timelineId')):
        raise ValueError('当前达芬奇项目或时间线已切换，请重新读取并确认后导出')
    fps = float(t.GetSetting('timelineFrameRate') or p.GetSetting('timelineFrameRate'))
    origin = float(t.GetStartFrame())
    result.update(fps=fps, width=int(t.GetSetting('timelineResolutionWidth') or p.GetSetting('timelineResolutionWidth')),
                  height=int(t.GetSetting('timelineResolutionHeight') or p.GetSetting('timelineResolutionHeight')),
                  duration=(float(t.GetEndFrame())-origin)/fps, tracks=[], warnings=[])
    for kind in ('video', 'audio'):
        for index in range(1, t.GetTrackCount(kind)+1):
            if not t.GetIsTrackEnabled(kind, index):
                result['warnings'].append(f'未导出已关闭的 {kind} {index} 轨')
                continue
            clips = []
            for item in t.GetItemListInTrack(kind, index) or []:
                if not item.GetClipEnabled():
                    result['warnings'].append('未导出已禁用片段：' + item.GetName())
                    continue
                media = item.GetMediaPoolItem()
                if not media or not media.GetClipProperty('File Path'):
                    raise ValueError('无法转换生成器、复合片段或离线素材：' + item.GetName() + '。请先渲染为独立素材。')
                path = media.GetClipProperty('File Path')
                if not Path(path).is_file():
                    raise ValueError('原素材已移动或离线：' + path)
                speed = item.GetSpeed() or {}
                if abs(float(speed.get('Percentage', 100))-100) > .001:
                    raise ValueError('暂不转换变速或倒放片段：' + item.GetName() + '。请先渲染该片段。')
                props = item.GetProperties() or {}
                if item.GetFusionCompCount():
                    raise ValueError('含未烘焙 Fusion 效果：' + item.GetName() + '。请先渲染该片段。')
                if props.get('DynamicZoomEnabled'):
                    raise ValueError('暂不转换动态缩放：' + item.GetName())
                if kind == 'video' and any(float(props.get(k, 0)) != 0 for k in (
                        'CropLeft', 'CropRight', 'CropTop', 'CropBottom', 'AnchorPointX',
                        'AnchorPointY', 'Pitch', 'Yaw', 'Distortion', 'CompositeMode', 'Scaling')):
                    raise ValueError('包含未支持的裁剪/锚点/混合或缩放模式：' + item.GetName() + '。请先渲染为素材。')
                try:
                    source_start = relative_source_start(
                        item.GetSourceStartTime(), item.GetSourceStartFrame(),
                        media.GetClipProperty() or {})
                except ValueError as error:
                    raise ValueError(f'{item.GetName()}：{error}') from error
                clips.append({'name': item.GetName(), 'path': path,
                              'at': (float(item.GetStart(True))-origin)/fps,
                              'duration': float(item.GetDuration(True))/fps,
                              'sourceStart': source_start,
                              'properties': props})
            if clips:
                result['tracks'].append({'kind': kind, 'index': index,
                                         'name': t.GetTrackName(kind, index), 'clips': clips})
    if not result['tracks']:
        raise ValueError('当前时间线没有可导出的启用片段')
    _, after_p, after_t = current()
    if identity(after_p, after_t) != identity(p, t):
        raise ValueError('读取期间项目或时间线发生切换，请重试')
    if t.GetTrackCount('subtitle'):
        result['warnings'].append('字幕轨未转换；请在剪映重新导入字幕文件。')
    return result


if __name__ == '__main__':
    try:
        if sys.argv[1] == 'current':
            _, p, t = current()
            print(json.dumps(identity(p, t), ensure_ascii=False))
        else:
            request = Path(sys.argv[1])
            result = snapshot(json.loads(request.read_text())['source'])
            target = request.parent / 'timeline-snapshot.json'
            target.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
            print('实际时间线已读取：' + result['timeline'], flush=True)
    except Exception as e:
        print(str(e), file=sys.stderr, flush=True)
        sys.exit(1)
