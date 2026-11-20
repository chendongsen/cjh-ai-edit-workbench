"""Validate confirmed decisions, generate real overlay assets and a Resolve job."""
import math
import subprocess
import imageio_ffmpeg
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
from common import asset_for
from analyze import probe
from project_naming import make_project_naming


def number(value, name):
    n = float(value)
    if not math.isfinite(n) or n < 0:
        raise ValueError(name + '必须是非负有限数字')
    return n


def title_image(text, output, width, height):
    image = Image.new('RGBA', (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    font_path = next((p for p in ['/System/Library/Fonts/PingFang.ttc', '/System/Library/Fonts/STHeiti Medium.ttc'] if Path(p).exists()), None)
    if not font_path:
        raise ValueError('找不到中文字体，无法生成花字')
    font_size = int(width * .07)
    lines = [text[i:i + 13] for i in range(0, len(text), 13)][:3]
    font = ImageFont.truetype(font_path, font_size)
    y = int(height * .68)
    for line in lines:
        box = draw.textbbox((0, 0), line, font=font, stroke_width=3)
        x = (width - (box[2] - box[0])) / 2
        draw.rounded_rectangle((x - 16, y - 8, width - x + 16, y + font_size + 17), radius=14, fill=(22, 25, 20, 225))
        draw.text((x, y), line, font=font, fill=(252, 229, 78, 255), stroke_width=2, stroke_fill=(10, 10, 10, 255))
        y += font_size + 26
    image.save(output)


def build(data, assets, folder, job_id):
    plan = data['plan']
    speeches = plan.get('selectedSpeech', [])
    if not speeches:
        raise ValueError('请先确认并保留至少一条人物台词')
    clips, main, warnings, metadata = [], [], [], {}
    automatic = data.get('automaticDraft') is True
    if automatic:
        warnings.append('自动初剪：角色尚未区分，重复台词和编导声音可能保留；不是人工确认的成片。')
    fps = int(data.get('fps', 30))
    if fps not in (24, 25, 30, 50, 60):
        raise ValueError('帧率不支持')
    width, height = (1080, 1920) if data.get('vertical', True) else (1920, 1080)

    def resolve(identifier):
        a = asset_for(assets, identifier)
        if identifier not in metadata:
            metadata[identifier] = probe(a['path'])
        return a, metadata[identifier]

    def range_for(a, meta, start, end):
        start, end = number(start, '起点'), number(end, '终点')
        if end <= start or end > meta['duration'] + .05:
            raise ValueError('片段超出素材范围：' + a['name'])
        return start, (math.ceil((end - start) * fps - 1e-8) / fps)

    cursor = 0
    for s in speeches:
        if not automatic and s.get('role') in (None, '', '待确认'):
            raise ValueError('仍有保留台词未确认角色：' + s.get('text', ''))
        a, meta = resolve(s.get('assetId'))
        if not meta['video']:
            raise ValueError('主线暂仅支持含画面的人物素材')
        start, duration = range_for(a, meta, s['start'], s['end'])
        if duration < 1 / fps:
            raise ValueError('片段不足一帧')
        clip = {'kind': '人物主线', 'path': a['path'], 'start': start, 'duration': duration, 'at': cursor, 'track': 1}
        clips.append(clip)
        main.append(clip)
        cursor += duration
    for m in plan.get('foodPlacements', []):
        a, meta = resolve(m.get('assetId'))
        start, duration = range_for(a, meta, m['start'], m['end'])
        at = number(m['at'], '食物位置')
        if at + duration > cursor + .02:
            raise ValueError('食物片段超出粗剪总时长，请重设成片位置')
        if not meta['video']:
            raise ValueError('食物第二轨当前需要视频素材')
        clips.append({'kind': {'environment':'环境补充','script':'脚本镜头'}.get(m.get('brollKind'),'食物画面'), 'path': a['path'], 'start': start, 'duration': duration,
                      'at': at, 'track': 2, 'mediaType': 1})
    markers = []
    for e in plan.get('packaging', []):
        at, duration = number(e['at'], '包装位置'), number(e['duration'], '包装时长')
        if duration <= 0 or at + duration > cursor + .02:
            raise ValueError('包装时间超出粗剪范围：' + e.get('text', ''))
        shared = {'at': at, 'duration': duration, 'effectId': e['id'], 'mediaType': 1}
        if e['type'] == '花字':
            if not e.get('text', '').strip() or len(e['text']) > 39:
                raise ValueError('花字需要 1～39 个字符')
            image = folder / ('title-' + str(len(clips)) + '.png')
            title_image(e['text'], image, width, height)
            # Resolve ignores requested out-points on still imports (uses 5s default).
            # A bounded ProRes 4444 clip preserves alpha and gives frame-exact duration.
            movie = image.with_suffix('.mov')
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-hide_banner', '-loglevel', 'error',
                '-loop', '1', '-framerate', str(fps), '-i', str(image), '-t', str(duration),
                '-c:v', 'prores_ks', '-profile:v', '4', '-pix_fmt', 'yuva444p10le', '-threads', '2',
                str(movie)], check=True, timeout=90)
            clips.append(dict(shared, kind='花字', path=str(movie), start=0, track=4))
        elif e['type'] == '人物放大':
            end = at + duration
            for original in main:
                left, right = max(at, original['at']), min(end, original['at'] + original['duration'])
                if right > left:
                    clips.append(dict(shared, kind='人物放大', path=original['path'],
                        start=original['start'] + left - original['at'], duration=right - left,
                        at=left, track=3, zoom=1.18))
        elif e['type'] == '音效' and e.get('assetId'):
            a, meta = resolve(e['assetId'])
            if not meta['audio'] or duration > meta['duration']:
                raise ValueError('音效素材不含音频或时长不足')
            clips.append(dict(shared, kind='音效', path=a['path'], start=0, track=2, mediaType=2, volume=-12))
        else:
            warnings.append(e['type'] + '未自动执行，仅添加待处理标记：' + e.get('text', ''))
            markers.append(e)
    for media_type, track in [(1, 2), (1, 3), (1, 4), (2, 2)]:
        same = sorted([c for c in clips if c['track'] == track and c.get('mediaType') == media_type], key=lambda c: c['at'])
        for prev, nxt in zip(same, same[1:]):
            if nxt['at'] < prev['at'] + prev['duration'] - 1 / fps:
                raise ValueError('同轨片段重叠，请先错开位置：' + nxt['kind'])
    naming = data.get('projectNaming') or make_project_naming(
        [assets[s['assetId']] for s in speeches if s.get('assetId') in assets], job_id)
    return {'jobId': job_id, 'name': str(plan.get('project', '工作台粗剪')),
            'projectNaming':naming, 'projectName':naming['projectName'], 'fps': fps, 'width': width,
            'height': height, 'clips': clips, 'markers': markers, 'warnings': warnings}
