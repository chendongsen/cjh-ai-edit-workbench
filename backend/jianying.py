"""Local Resolve-to-Jianying adapter. Only creates new drafts; never edits existing ones."""
import hashlib
import importlib.util
import math
import os
import plistlib
import re
import shutil
import subprocess
import sys
import time
import uuid
import jianying_handoff
from pathlib import Path
from common import DATA, ROOT, RESOLVE, read, write


def find_app():
    for parent in (Path('/Applications'), Path.home() / 'Applications'):
        for app in parent.glob('*.app'):
            try:
                info = plistlib.loads((app / 'Contents/Info.plist').read_bytes())
                if info.get('CFBundleIdentifier') == 'com.lemon.lvpro':
                    return {'path': str(app), 'version': info.get('CFBundleShortVersionString', '')}
            except (OSError, ValueError, plistlib.InvalidFileException):
                continue
    return None


def draft_root():
    return Path.home() / 'Movies/JianyingPro/User Data/Projects/com.lveditor.draft'


def status():
    return {'app': find_app(), 'available': importlib.util.find_spec('pyJianYingDraft') is not None,
            'handoff': jianying_handoff.status(),
            'draftRoot': str(draft_root()), 'draftRootExists': draft_root().is_dir(),
            'note': '本机新建草稿；不调用剪映云端 AI。转换结果需在剪映审片。'}


def micros(value):
    value = float(value)
    if not math.isfinite(value) or value < 0:
        raise ValueError('时间必须为非负有限数')
    return round(value * 1_000_000)


def safe_name(name, limit=60):
    name = re.sub(r'[\\/:*?"<>|\x00-\x1f]', '_', str(name)).strip(' .')[:limit]
    return name or '达芬奇粗剪'


def bounded_duration(start, duration, material_duration, fps, at=0):
    """Remove only a sub-frame source-tail overrun; never stretch source audio.

    Resolve can quantize a VFR source's tail outward to the timeline frame grid.
    Retain every available source microsecond at 1x and leave following positions
    unchanged. Larger overruns (or a start outside the source) are real errors.
    """
    available = int(material_duration) - start
    if available <= 0:
        raise ValueError('素材入点已超出实际时长，不能导出')
    overrun = max(0, duration - available)
    if overrun > math.ceil(1_000_000 / fps) + 2:
        raise ValueError(f'素材出点超出实际时长 {overrun / 1e6:.6f} 秒，超过一帧容差；未裁剪或变速')
    if overrun:
        # Integer-microsecond conversion may land 1 us beyond a frame boundary.
        # Ignore only that rounding noise when finding the last output sample.
        last_sample = math.ceil((at + duration) * fps / 1e6 - 0.0001) - 1
        if last_sample * 1e6 / fps >= at + available:
            raise ValueError('源尾调整会丢失时间线采样帧，不能自动缩短；请复核末帧')
    return min(duration, available), overrun


def build_draft(snapshot, folder):
    import pyJianYingDraft as draft
    import imageio_ffmpeg
    fps = float(snapshot['fps'])
    if not fps.is_integer() or not 1 <= fps <= 120:
        raise ValueError('当前转换仅支持整数帧率；请勿将 29.97 等帧率直接四舍五入')
    folder = Path(folder)
    # Fail rather than reusing any existing draft, including after a partial run.
    folder.mkdir(parents=True, exist_ok=False)
    media_dir = folder / 'audio'
    media_dir.mkdir()
    script = draft.ScriptFile(snapshot['width'], snapshot['height'], int(fps), False)
    warnings = list(snapshot.get('warnings', [])) + [
        '这是多轨粗剪转换，不是跨软件无损工程迁移：调色、转场、OFX、关键帧、音频插件、轨道混音和标记不迁移。',
        '保留素材路径、切点、轨道位置、静态缩放/位移/旋转和片段音量；预渲染花字仍是视频，不变成剪映可编辑文字。',
        '视频在草稿 media 目录保留本机副本（同盘优先硬链接，不重复占用视频空间）；原声独立提取到 audio。请勿删除草稿内素材。']
    cache = {}
    count = 0
    tail_adjustments = []
    for track in snapshot['tracks']:
        kind = track['kind']
        if kind not in ('video', 'audio'):
            raise ValueError('不支持的轨道类型：' + kind)
        name = f'{kind[0].upper()}{track["index"]} · {track["name"]}'
        track_ref = script.append_track(draft.TrackSpec(getattr(draft.TrackType, kind), name=name))
        previous_end = 0
        for clip in sorted(track['clips'], key=lambda c: c['at']):
            at, end = micros(clip['at']), micros(clip['at'] + clip['duration'])
            start = micros(clip['sourceStart'])
            if end <= at or at < previous_end - 1:
                raise ValueError('片段时长无效或同轨重叠（转场需先处理）：' + clip['name'])
            previous_end = end
            source = Path(clip['path']).resolve()
            if not source.is_file():
                raise ValueError('素材不存在：' + str(source))
            key = (kind, str(source))
            props = clip.get('properties', {})
            if key not in cache:
                if kind == 'audio':
                    # Extract the COMPLETE source once: source in-points stay editable.
                    audio = media_dir / (hashlib.sha256(str(source).encode()).hexdigest()[:16] + '.wav')
                    print('准备独立音频：' + source.name, flush=True)
                    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-nostdin',
                                    '-i', str(source), '-map', '0:a:0', '-vn', '-c:a', 'pcm_s16le',
                                    '-ar', '48000', str(audio)], check=True, timeout=600)
                    cache[key] = draft.AudioMaterial(str(audio), material_name=source.name)
                else:
                    # macOS media bookmarks may reject paths never imported via its picker.
                    # Keep assets inside the app's own draft directory; no root index edits.
                    local_media = folder / 'media'
                    local_media.mkdir(exist_ok=True)
                    linked = local_media / (hashlib.sha256(str(source).encode()).hexdigest()[:16] + source.suffix)
                    try:
                        os.link(source, linked)
                    except OSError:
                        shutil.copy2(source, linked)
                    cache[key] = draft.VideoMaterial(str(linked), material_name=source.name)
                    from pymediainfo import MediaInfo
                    info = MediaInfo.parse(str(linked))
                    if info.video_tracks and round(float(info.video_tracks[0].rotation or 0)) % 180 == 90:
                        cache[key].width, cache[key].height = cache[key].height, cache[key].width
            length, overrun = bounded_duration(start, end-at, cache[key].duration, fps, at)
            if overrun:
                tail_adjustments.append({'kind': kind, 'source': str(source), 'at': at / 1e6,
                    'sourceStart': start / 1e6, 'requestedDuration': (end-at) / 1e6,
                    'actualDuration': length / 1e6, 'unavailableTailSeconds': overrun / 1e6,
                    'speed': 1, 'followingPositionsUnchanged': True})
                warnings.append(f'{source.name}（{kind}）文件尾部不足一帧：去掉文件外 {overrun / 1e6:.6f} 秒；'
                    '保留全部可用源片段、原速及后续切点，未拉伸声音。')
            # Both ranges must change together: the library otherwise silently
            # computes a non-1x speed from source_duration / target_duration.
            target_range = draft.Timerange(at, length)
            source_range = draft.Timerange(start, length)
            if kind == 'video':
                transform = props.get('TransformEnabled', True)
                settings = draft.ClipSettings(
                    scale_x=float(props.get('ZoomX', 1)) if transform else 1,
                    scale_y=float(props.get('ZoomY', 1)) if transform else 1,
                    transform_x=2*float(props.get('Pan', 0))/snapshot['width'] if transform else 0,
                    transform_y=2*float(props.get('Tilt', 0))/snapshot['height'] if transform else 0,
                    rotation=float(props.get('RotationAngle', 0)) if transform else 0,
                    flip_horizontal=bool(props.get('FlipX', False)) if transform else False,
                    flip_vertical=bool(props.get('FlipY', False)) if transform else False,
                    alpha=float(props.get('Opacity', 100))/100 if props.get('CompositeEnabled', True) else 1)
                segment = draft.VideoSegment(cache[key], target_range, source_timerange=source_range,
                                             volume=0, clip_settings=settings)
            else:
                db = float(props.get('AudioVolume', 0)) if props.get('AudioVolumeEnabled', True) else 0
                segment = draft.AudioSegment(cache[key], target_range, source_timerange=source_range,
                                             volume=10**(db/20))
            script.add_segment(segment, track_ref)
            count += 1
    script.content['id'] = str(uuid.uuid4()).upper()
    script.content['name'] = folder.name
    content_path = folder / 'draft_content.json'
    script.dump(str(content_path))
    # macOS uses draft_info.json; keep the conventional filename for interoperability.
    shutil.copyfile(content_path, folder / 'draft_info.json')
    from pyJianYingDraft import assets
    meta = read(Path(assets.get_asset_path('DRAFT_META_TEMPLATE')))
    meta.update(draft_id=script.content['id'], draft_name=folder.name, draft_fold_path=str(folder),
                draft_root_path=str(folder.parent), tm_duration=script.duration,
                tm_draft_create=int(time.time()*1e6), tm_draft_modified=int(time.time()*1e6))
    write(folder / 'draft_meta_info.json', meta)
    actual = read(content_path)
    counts = [len(t['segments']) for t in actual['tracks']]
    if sum(counts) != count:
        raise ValueError('草稿回读片段数量不一致')
    result = {'draftName': folder.name, 'draftPath': str(folder), 'source': {k: snapshot[k] for k in ('project', 'timeline', 'timelineId')},
              'duration': actual['duration']/1e6, 'fps': fps, 'clipCount': count,
              'tracks': [{'kind': t['type'], 'name': t['name'], 'count': len(t['segments'])} for t in actual['tracks']],
              'warnings': warnings, 'tailAdjustments': tail_adjustments,
              'opened': False, 'editorVerified': False}
    write(folder / 'CJH转换报告.json', result)
    return result


def launch_app(draft_path=None):
    app = find_app()
    if not app:
        raise ValueError('未找到剪映专业版。草稿仍保留在本机，可安装剪映后重试打开。')
    subprocess.run(['/usr/bin/open', '-a', app['path']], check=True, timeout=15)
    name = Path(draft_path).name if draft_path else '对应草稿'
    if draft_path:
        result = jianying_handoff.open_draft(draft_path)
        return dict(result, opened=True)
    return {'opened': True, 'draftOpened': False, 'openState': 'application-launched',
            'note': '剪映已请求启动；请在首页打开「' + name + '」。应用启动不代表已进入草稿；不会自动强退编辑器。'}


def run(source):
    data = read(source)
    proc = subprocess.run([RESOLVE, str(ROOT / 'backend/resolve_snapshot.py'), str(source)])
    if proc.returncode:
        raise ValueError('读取实际时间线失败；未创建剪映草稿')
    snapshot = read(source.parent / 'timeline-snapshot.json')
    root = draft_root()
    if not root.is_dir():
        raise ValueError('未找到剪映默认草稿目录。请先启动剪映创建一个本地草稿；如用了自定义目录，请先配置转换器。')
    auto_name=data.get('autoDraftName')
    if not auto_name and re.fullmatch(r'\d{8}_.+_粗剪_\d{6}_[a-f0-9]{12}', snapshot.get('project','')):
        auto_name=snapshot['project']+'_剪映'
    if auto_name:
        name = safe_name(auto_name,limit=110)
        # The native opener intentionally accepts only workbench-owned drafts.
        if not name.startswith('CJH_'):
            name='CJH_'+name
    else:
        name = 'CJH_' + safe_name(data.get('name') or snapshot['timeline']) + '_' + time.strftime('%m%d_%H%M%S') + '_' + uuid.uuid4().hex[:6]
    original_name=name
    suffix=2
    while (root/name).exists():
        name=original_name+f'_{suffix:02d}'
        suffix+=1
    folder = root / name
    print('生成多轨剪映草稿：' + name, flush=True)
    result = build_draft(snapshot, folder)
    if data.get('openAfter'):
        try:
            result.update(launch_app(result['draftPath']))
        except Exception as e:
            result['warnings'].append('草稿已导出，但打开剪映失败：' + str(e))
    write(source.parent / 'result.json', result)
    print('草稿已导出并在剪映编辑页打开：' + name if result.get('draftOpened') else
          '草稿已导出；' + result.get('note', '请在剪映首页打开「' + name + '」核对。'), flush=True)


if __name__ == '__main__':
    try:
        run(Path(sys.argv[1]))
    except Exception as e:
        print(str(e), file=sys.stderr, flush=True)
        sys.exit(1)
