"""Local 3D-Speaker CAM++ audition lab. Never changes source media or NLE drafts.

Voice identity is NOT editorial intent. Only curated instruction candidates can
be attenuated; reactions, scene dialogue and uncertain/short turns stay intact.
"""
import hashlib
import json
import telemetry
import math
import re
import subprocess
import sys
import time
import uuid
import wave
from pathlib import Path

import numpy as np
from common import DATA, ROOT, VENDOR_DIR, read, write

BATCH = 'local-audition'
BASE = DATA / 'speaker-tests'
MODEL = DATA / 'models/3d-speaker/campplus_cn_common.bin'
VENDOR = VENDOR_DIR
SR = 16000
ROLES = ('talent', 'director', 'friend', 'exclude')
THRESHOLD, MARGIN = .65, .18  # Conservative starting gates, not calibrated accuracy.


def folder(batch):
    if not isinstance(batch, str) or not re.fullmatch(r'[a-z0-9-]{1,64}', batch):
        raise ValueError('无效测试批次')
    return BASE / batch


def media_path(identifier):
    parts = identifier.split(':')
    if len(parts) != 3 or parts[0] != 'spk' or not re.fullmatch(r'[a-z0-9-]+\.wav', parts[2]):
        raise ValueError('无效声音链接')
    p = folder(parts[1]) / 'media' / parts[2]
    if not p.is_file() or not p.resolve().is_relative_to(BASE.resolve()):
        raise ValueError('声音不存在')
    return p


def wav_read(path):
    with wave.open(str(path), 'rb') as w:
        if (w.getnchannels(), w.getframerate(), w.getsampwidth()) != (1, SR, 2):
            raise ValueError('需要 16kHz 单声道 PCM16')
        return np.frombuffer(w.readframes(w.getnframes()), dtype='<i2').copy()


def wav_write(path, pcm):
    with wave.open(str(path), 'wb') as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(SR)
        w.writeframes(np.asarray(pcm, dtype='<i2').tobytes())


def extract(source, start, end, target):
    import imageio_ffmpeg
    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-nostdin',
        '-ss', str(start), '-i', str(source), '-t', str(end-start), '-vn',
        '-ac', '1', '-ar', str(SR), '-c:a', 'pcm_s16le', '-threads', '1',
        '-y', str(target)], check=True, capture_output=True)
    pcm = wav_read(target)
    if abs(len(pcm)/SR-(end-start)) > .15:
        raise ValueError('抽取音频长度与素材范围不符：' + str(target))


class Embedder:
    @telemetry.measured('campplus')
    def __init__(self):
        import torch
        from torchaudio.compliance.kaldi import fbank
        sys.path.insert(0, str(VENDOR))
        from speakerlab.models.campplus.DTDNN import CAMPPlus
        torch.set_num_threads(2)
        self.torch, self.fbank = torch, fbank
        self.model = CAMPPlus(feat_dim=80, embedding_size=192)
        self.model.load_state_dict(torch.load(MODEL, map_location='cpu', weights_only=True))
        self.model.eval()

    def __call__(self, pcm):
        if len(pcm) < int(.5*SR) or np.sqrt(np.mean((pcm.astype(float)/32768)**2)) < .0001:
            return None
        t = self.torch
        wav = t.from_numpy(pcm.astype(np.float32)/32768).unsqueeze(0)
        # Same 80-bin Kaldi filterbank + temporal mean normalization as official FBank.
        feat = self.fbank(wav, num_mel_bins=80, sample_frequency=SR, dither=0)
        feat = feat-feat.mean(0, keepdim=True)
        with t.inference_mode():
            emb = self.model(feat.unsqueeze(0)).squeeze(0).numpy()
        norm = np.linalg.norm(emb)
        if not np.isfinite(emb).all() or norm < 1e-8:
            raise ValueError('声纹向量无效')
        return (emb/norm).tolist()


def overlaps(a, b):
    return a[0] < b[1] and b[0] < a[1]


def decide(candidate, scores, protected, confirmed):
    """Scores are cosine similarity, never probability. Fail closed (keep)."""
    if not confirmed:
        return False, '参考声音尚未确认，保持原声'
    if candidate.get('intent') != 'instruction':
        return False, '不是明确指导语候选，保留'
    if candidate.get('overlap') or any(overlaps([candidate['start']+.08, candidate['end']-.08], p[:2]) for p in protected):
        return False, '反应／笑声／闺蜜接话或混合说话保护'
    if candidate['end']-candidate['start'] < 1.5:
        return False, '声音太短，保留待审'
    if not all(k in scores and math.isfinite(scores[k]) for k in ('talent', 'director', 'friend')):
        return False, '有效声纹不足，保留待审'
    other = max(scores['talent'], scores['friend'])
    if scores['director'] < THRESHOLD or scores['director']-other < MARGIN:
        return False, '编导相似度或区分差距不足，保留待审'
    if not candidate.get('stable', False):
        return False, '前后声纹不一致／不能排除换人，保留待审'
    return True, '已确认编导参考＋指导语候选＋前后窗口一致，仅试听副本衰减'


def attenuate(pcm, intervals):
    gain = np.ones(len(pcm), dtype=np.float64)
    for start, end in intervals:
        a, b = max(0, round(start*SR)), min(len(pcm), round(end*SR))
        if b <= a:
            continue
        n = min(round(.02*SR), (b-a)//2)
        g = np.full(b-a, .001)  # -60 dB, not source separation.
        if n:
            g[:n] = np.linspace(1, .001, n)
            g[-n:] = np.linspace(.001, 1, n)
        gain[a:b] = np.minimum(gain[a:b], g)
    return np.rint(pcm.astype(float)*gain).clip(-32768, 32767).astype('<i2')


def validate_confirmation(batch, labels, acknowledged):
    manifest = read(folder(batch) / 'manifest.json')
    if not manifest or not manifest.get('modelReady'):
        raise ValueError('真实声纹分析尚未完成')
    ids = {r['id'] for r in manifest['references']}
    if acknowledged is not True or not isinstance(labels, dict) or set(labels) != ids or any(v not in ROLES for v in labels.values()):
        raise ValueError('请逐条试听并选择角色；不清楚的参考请排除')
    for role in ('talent', 'director', 'friend'):
        if role not in labels.values():
            raise ValueError('达人、编导、闺蜜各需至少一条已确认参考；不能确认时请先不要运行')
    return {'batch': batch, 'labels': labels, 'acknowledged': True,
            'manifestSha256': hashlib.sha256((folder(batch)/'manifest.json').read_bytes()).hexdigest(),
            'confirmedAt': time.time()}


def prepare():
    """Private curated voice references are not distributed."""
    raise ValueError('公开源码不包含个人声纹测试素材或预设选段；'
                     '请使用自行取得授权的参考数据，未知声音仍保留。')


def public_manifest(batch=BATCH):
    m = read(folder(batch)/'manifest.json')
    if not m:
        return {'id':batch,'status':'not_prepared','modelReady':False}
    m=json.loads(json.dumps(m))
    for r in m['references']:
        r.pop('embedding',None)
    for s in m['samples']:
        for c in s['candidates']:
            c.pop('embedding',None); c.pop('windows',None)
    return m


def run(request, output):
    batch=request['batch']; dest=folder(batch)
    confirmation=validate_confirmation(batch,request.get('labels'),request.get('acknowledged'))
    if confirmation['manifestSha256'] != request.get('manifestSha256'):
        raise ValueError('参考素材已变化，请重新确认')
    m=read(dest/'manifest.json')
    refs={role:[r for r in m['references'] if request['labels'][r['id']]==role and r['embedding']] for role in ROLES[:3]}
    if not all(refs.values()):
        raise ValueError('参考音频没有有效声纹，请重新选择')
    def score(e, sample, candidate):
        if not e:
            return {}
        scores={}
        for role, vectors in refs.items():
            # Do not count matching a reference against itself as a successful test.
            eligible=[r for r in vectors if r['source']!=sample['source'] or not overlaps(
                [r['start'],r['end']], [candidate['start'],candidate['end']])]
            if eligible:
                scores[role]=float(max(np.dot(e,r['embedding']) for r in eligible))
        return scores
    rows=[]; prefix=str(uuid.uuid4())
    for s in m['samples']:
        pcm=wav_read(media_path(s['before'])); masks=[]; decisions=[]
        for original in s['candidates']:
            c=dict(original); scores=score(c['embedding'],s,c)
            window_scores=[score(w,s,c) for w in c['windows']]
            c['stable']=all(w.get('director',-1)>=THRESHOLD and w.get('director',-1)-max(w.get('talent',1),w.get('friend',1))>=MARGIN for w in window_scores)
            yes,reason=decide(c,scores,s['protected'],True)
            # Leave 120ms guards at both edges; never expand a mute into adjacent turns.
            if yes:
                masks.append([c['start']-s['start']+.12,c['end']-s['start']-.12])
            decisions.append({'start':c['start'],'end':c['end'],'attenuate':yes,'reason':reason,
                              'similarities':scores,'windowSimilarities':window_scores})
        after=attenuate(pcm,masks)
        changed=after!=pcm
        for a,b,_ in s['protected']:
            if changed[max(0,round((a-s['start'])*SR)):min(len(pcm),round((b-s['start'])*SR))].any():
                raise AssertionError('保护区出现改动，拒绝输出')
        name=f'{prefix}-{s["id"].lower()}.wav'; wav_write(dest/'media'/name,after)
        rows.append({'id':s['id'],'title':s['title'],'before':s['before'],'after':f'spk:{batch}:{name}',
                     'decisions':decisions,'attenuatedSeconds':round(sum(b-a for a,b in masks),3),
                     'protectedUnchanged':True,'sameLength':len(after)==len(pcm),
                     'identical':bool(np.array_equal(after,pcm))})
        print('试听副本完成：'+s['title'],flush=True)
    result={'batch':batch,'model':m['model'],'confirmation':request,'samples':rows,
            'attenuatedSeconds':round(sum(s['attenuatedSeconds'] for s in rows),3),
            'changedSamples':sum(not s['identical'] for s in rows),
            'note':'仅生成等长试听副本；没有修改视频、达芬奇或剪映草稿。非重叠说话分离，不能代表全批准确率。'}
    write(output/'result.json',result)


if __name__=='__main__':
    if len(sys.argv)>1 and sys.argv[1]=='prepare':
        prepare()
    else:
        p=Path(sys.argv[1]); run(read(p),p.parent)
