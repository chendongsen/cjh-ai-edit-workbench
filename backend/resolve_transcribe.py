"""Resolve 21.1 transcription with anonymous speaker labels; no identity inference."""
import json
import sys
import time
import uuid
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import DaVinciResolveScript as dvr
from resolve_lifecycle import preserve_current
from project_naming import make_project_naming


def seconds(tc, fps):
    # Drop-frame inputs require explicit handling; reject rather than shift edits.
    if ';' in tc:
        raise ValueError('暂不支持丢帧时间码转写，请选 Whisper 引擎')
    h, m, s, f = map(int, tc.split(':'))
    return h * 3600 + m * 60 + s + f / fps


def run(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    resolve = dvr.scriptapp('Resolve')
    if not resolve:
        raise RuntimeError('达芬奇未连接，请打开 Studio 并启用 Local 外部脚本')
    pm = resolve.GetProjectManager()
    naming=data.get('projectNaming') or make_project_naming(data['assets'],path.parent.name,path.stat().st_mtime)
    name=naming['projectName']+'_转写'
    preserve_current(pm,name)
    p = pm.CreateProject(name)
    if not p:
        raise RuntimeError('无法创建独立转写项目')
    results = {}
    for a in data['assets']:
        clips = p.GetMediaPool().ImportMedia([a['path']])
        if not clips:
            raise RuntimeError('达芬奇导入失败：' + a['name'])
        clip = clips[0]
        print('达芬奇正在转写并检测说话人：' + a['name'], flush=True)
        if not clip.TranscribeAudio(True):
            raise RuntimeError('达芬奇未接受转写任务；请检查语音模型和软件设置')
        deadline = time.time() + 3600
        while time.time() < deadline:
            status = p.GetTranscribeAudioStatus()
            phase = status.get('JobStatus', 'Unknown')
            print('达芬奇转写：' + phase + ' ' + str(status.get('CompletionPercentage', '')) + '%', flush=True)
            if phase == 'Complete':
                break
            if phase in ('Failed', 'Cancelled'):
                raise RuntimeError('达芬奇转写失败：' + str(status.get('Error', phase)))
            time.sleep(2)
        else:
            raise TimeoutError('达芬奇转写超时，请在达芬奇检查任务')
        transcript = clip.GetTranscription()
        if not transcript:
            raise RuntimeError('达芬奇报告完成，但未返回转写内容')
        properties = clip.GetClipProperty()
        fps = float(properties['FPS'])
        offset = seconds(properties.get('Start TC', '00:00:00:00'), fps)
        rows = []
        for segment in transcript.get('segments', []):
            if not segment.get('text') or segment['text'].strip() == '(...)':
                continue
            start, end = seconds(segment['start'], fps) - offset, seconds(segment['end'], fps) - offset
            if start < -.05 or end <= start:
                raise ValueError('转写时间码与素材起始时间不符，已停止以避免错剪')
            rows.append({'id': str(uuid.uuid4()), 'assetId': a['id'], 'source': a['path'],
                'speaker': segment.get('speaker') or '待确认', 'start': max(0, round(start, 3)),
                'end': round(end, 3), 'text': segment['text'], 'decision': '待确认'})
        results[a['id']] = rows
        current=pm.GetCurrentProject()
        if not current or current.GetUniqueId()!=p.GetUniqueId():
            raise RuntimeError('转写期间项目已切换，未保存其他项目')
        if not pm.SaveProject():
            raise RuntimeError('转写项目保存失败；未报告完成')
        current=pm.GetCurrentProject()
        if not current or current.GetUniqueId()!=p.GetUniqueId():
            raise RuntimeError('保存期间达芬奇项目已切换，未确认本次转写完成')
    (path.parent / 'resolve-transcript.json').write_text(json.dumps(results, ensure_ascii=False), encoding='utf-8')


if __name__ == '__main__':
    try:
        run(Path(sys.argv[1]))
    except Exception as e:
        print(str(e), file=sys.stderr, flush=True)
        sys.exit(1)
