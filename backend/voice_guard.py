"""Offline voice turns and reaction guards for unattended rough cuts.

Silero VAD + official CAM++ embeddings; SenseVoice ONNX event hints.
This is a conservative fallback, NOT pyannote overlap separation.
"""
import copy
import hashlib
import json
import telemetry
import math
import os
import re
import time
from difflib import SequenceMatcher
from pathlib import Path

import numpy as np
from common import DATA, read, write
from editorial import norm, main_candidate
from speaker import Embedder, MODEL, SR, THRESHOLD, MARGIN, extract, folder, wav_read, overlaps

REVISION = 'voice-guard-1'
SENSE = DATA / 'models/sherpa-onnx-sense-voice-zh-en-ja-ko-yue-int8-2024-07-17'
COMMAND = re.compile(r'^(?:(?:好|来|对|ok))*[三3][二2][一1]|再来一遍|再拍一遍|重新拍|看镜头|你就.{0,4}说|然后说|你可以这样说|表情管理|慢一点|慢点|你先拿|再夹一下|再咬一口|可以可以了来来来')


def confirmed_profiles():
    profiles=[]
    for p in (DATA/'jobs').glob('*/status.json'):
        j=read(p,{})
        if j.get('kind')!='speaker' or j.get('status')!='succeeded':
            continue
        c=read(p.parent/'input.json',{})
        if c.get('acknowledged') is not True:
            continue
        try:
            path=folder(c['batch'])/'manifest.json'
            if hashlib.sha256(path.read_bytes()).hexdigest()!=c['manifestSha256']:
                continue
            m=read(path)
            refs=[dict(r,role=c['labels'][r['id']]) for r in m['references']
                  if c['labels'].get(r['id']) in ('talent','director','friend') and r.get('embedding')]
            if not {'talent','director','friend'} <= {r['role'] for r in refs}:
                continue
            scope=os.path.commonpath([str(Path(r['source']).parent) for r in refs])
            profiles.append({'id':j['id'],'projectId':j['projectId'],'name':m['name'],
                'scope':scope,'confirmedAt':c['confirmedAt'],'references':refs,'model':m['model']})
        except (KeyError,ValueError,OSError):
            continue
    return sorted(profiles,key=lambda p:p['confirmedAt'],reverse=True)


def choose_profile(assets, project_id):
    for p in confirmed_profiles():
        if p['projectId']==project_id or (assets and all(Path(a['path']).is_relative_to(p['scope']) for a in assets)):
            return p
    return None  # New people do not stop the queue; unknown audio stays.


def health():
    return {'speaker':'3D-Speaker CAM++ + Silero VAD','speakerReady':MODEL.is_file(),
            'senseVoiceReady':(SENSE/'model.int8.onnx').is_file(),
            'overlapModel':'not-installed','profiles':[{'id':p['id'],'name':p['name'],
            'projectId':p['projectId'],'scope':p['scope'],'confirmedAt':p['confirmedAt']} for p in confirmed_profiles()],
            'policy':'无需逐批确认；未知、混合、笑声、剧情接话保留待审'}


class Events:
    @telemetry.measured('sensevoice')
    def __init__(self):
        import sherpa_onnx
        self.recognizer=sherpa_onnx.OfflineRecognizer.from_sense_voice(
            model=str(SENSE/'model.int8.onnx'),tokens=str(SENSE/'tokens.txt'),
            num_threads=2,use_itn=True,language='zh',provider='cpu')

    def __call__(self, pcm):
        stream=self.recognizer.create_stream()
        stream.accept_waveform(SR,pcm.astype(np.float32)/32768)
        self.recognizer.decode_stream(stream)
        r=stream.result
        return {'text':r.text,'emotion':getattr(r,'emotion',''),
                'event':getattr(r,'event',''),'language':getattr(r,'lang',''),
                'tokens':list(getattr(r,'tokens',[])),'timestamps':list(getattr(r,'timestamps',[]))}


def classify(vector, profile):
    if vector is None or not profile:
        return 'unknown',{}
    scores={role:max((float(np.dot(vector,r['embedding'])) for r in profile['references'] if r['role']==role),default=-1)
            for role in ('talent','director','friend')}
    ranked=sorted(scores,key=scores.get,reverse=True)
    role=ranked[0] if scores[ranked[0]]>=THRESHOLD and scores[ranked[0]]-scores[ranked[1]]>=MARGIN else 'unknown'
    return role,scores


def interval_role(start,end,windows):
    hits=[w for w in windows if overlaps([start,end],[w['start'],w['end']])]
    if not hits:return 'unknown'
    known={w['role'] for w in hits if w['role']!='unknown'}
    if len(known)>1:return 'mixed'
    coverage=sum(max(0,min(end,w['end'])-max(start,w['start'])) for w in hits)
    if coverage<.5*(end-start) or any(w['role']=='unknown' for w in hits):return 'unknown'
    return next(iter(known),'unknown')


def dialogue_files(script):
    """Use explicit script roles, never inferred sex/name/face identity."""
    protected=set(); previous=''
    for line in script.splitlines():
        if line.startswith('素材：') and re.search('闺蜜|朋友|互动|接话',previous):
            protected.add(line.split('：',1)[1].split('｜')[0].strip())
        previous=line
    return protected


def analyze_voice(items, request, output):
    enriched=copy.deepcopy(items)
    profile=request.get('voiceProfile')
    if profile:write(output/'voice-profile.json',profile)
    cache=DATA/'voice-cache';cache.mkdir(exist_ok=True)
    engine=Embedder();events=Events()
    model_hash=hashlib.sha256(MODEL.read_bytes()).hexdigest()
    sense_hash=hashlib.sha256((SENSE/'model.int8.onnx').read_bytes()).hexdigest()
    explicit_dialogues=dialogue_files(request.get('editingScript') or request.get('script',''))
    people=[i for i in enriched if main_candidate(i) and i.get('audio')]
    report={'revision':REVISION,'profileId':profile['id'] if profile else None,
            'modelSha256':model_hash,'senseVoiceSha256':sense_hash,
            'overlapDetection':False,'items':[],'warnings':[]}
    if not profile:report['warnings'].append('本项目没有已确认声纹；不按未知声音删除，任务继续。')
    for index,item in enumerate(people,1):
        stat=Path(item['source']).stat()
        key=hashlib.sha256(json.dumps([REVISION,item.get('fingerprint'),stat.st_size,stat.st_mtime_ns,
            model_hash,sense_hash,profile['id'] if profile else None,Path(item['source']).name in explicit_dialogues],sort_keys=True).encode()).hexdigest()
        file=cache/(key+'.json');cached=read(file)
        telemetry.event('cache', 'voice_hit' if cached else 'voice_miss', count=1)
        print(f'声音分段和笑声保护 {index}/{len(people)}：{Path(item["source"]).name}'+('（缓存）' if cached else ''),flush=True)
        if not cached:
            from analyze import probe
            duration=probe(item['source'])['usableDuration']
            wav=cache/(key+'.wav');extract(item['source'],0,duration,wav)
            pcm=wav_read(wav)
            from faster_whisper.vad import get_speech_timestamps,VadOptions
            vad=get_speech_timestamps(pcm.astype(np.float32)/32768,VadOptions(min_silence_duration_ms=250,speech_pad_ms=80))
            windows=[];centres=[]
            for v in vad:
                a,b=v['start']/SR,v['end']/SR
                for start in np.arange(a,max(a+.01,b-.45),.75):
                    end=min(start+1.5,b)
                    vector=engine(pcm[round(start*SR):round(end*SR)])
                    role,scores=classify(vector,profile)
                    anonymous='short'
                    if vector:
                        sim=[np.dot(vector,c) for c in centres]
                        if sim and max(sim)>=.72:label=int(np.argmax(sim))
                        else:label=len(centres);centres.append(vector)
                        anonymous=f'local_{label:02d}'
                    windows.append({'start':round(float(start),3),'end':round(float(end),3),
                                    'role':role,'similarities':scores,'anonymous':anonymous})
            hints=[];protected=[]
            # Scan audio including ASR-silent gaps; VAD is not allowed to discard laughs.
            for start in np.arange(0,max(.01,duration-1),4):
                end=min(float(start)+5,duration)
                hint=events(pcm[round(start*SR):round(end*SR)])
                hint.update(start=float(start),end=end);hints.append(hint)
                label=(hint['event']+' '+hint['emotion']+' '+hint['text']).lower()
                if any(t in label for t in ('laughter','laugh','surprised','cry','哈哈','哇哦')):
                    protected.append([max(0,float(start)-.3),min(duration,end+.5),'SenseVoice 反应候选（窗口级）'])
            if Path(item['source']).name in explicit_dialogues:
                protected.append([0,duration,'脚本明确闺蜜互动，整段保护'])
            for left,right in zip(windows,windows[1:]):
                if left['role']!=right['role'] and 'unknown' not in (left['role'],right['role']):
                    protected.append([max(0,right['start']-.2),min(duration,left['end']+.2),'疑似说话人交替，边界保护'])
            cached={'source':item['source'],'assetId':item['assetId'],'usableDuration':duration,
                    'windows':windows,'events':hints,'protected':protected,'fingerprint':key}
            write(file,cached)
        if 'utterances' not in cached:
            pcm=wav_read(cache/(key+'.wav'))
            cached['utterances']=[]
            for segment in item.get('segments',[]):
                start,end=segment['start'],min(segment['end'],cached['usableDuration'])
                # Longer semantic units improve short-window stability without lowering gates.
                vector=engine(pcm[round(start*SR):round(end*SR)]) if end-start>=1.5 else None
                role,scores=classify(vector,profile)
                known={w['role'] for w in cached['windows'] if w['role']!='unknown' and overlaps([start,end],[w['start'],w['end']])}
                if any(k!=role for k in known) and role!='unknown':role='mixed'
                cached['utterances'].append({'start':start,'end':end,'role':role,'similarities':scores})
            write(file,cached)
        item['voiceGuard']=cached
        item['usableDuration']=cached['usableDuration']
        report['items'].append(cached)
        write(output/'voice-report.json',report)
    return enriched


def guarded_speech(item,usable,fps,edit_mode=None):
    """Whole semantic segments only; do not word-chop uncertain mixed turns."""
    from edit_policy import policy
    settings=policy(edit_mode)
    voice=item['voiceGuard'];protected=voice['protected'];windows=voice['windows']
    units=[];removed=[];reviews=[]
    original=sorted(item.get('segments',[]),key=lambda s:s['start'])
    for i,s in enumerate(original):
        start,end=float(s['start']),min(float(s['end']),usable)
        if end<=start:continue
        text=s.get('text','');role=interval_role(start,end,windows)
        utterance=next((u for u in voice.get('utterances',[]) if abs(u['start']-start)<.01 and abs(u['end']-end)<.01),None)
        if utterance:
            role=utterance['role']  # Explicit mixed conflict remains protected; no lowered threshold.
        is_protected=any(overlaps([start,end],p[:2]) for p in protected)
        repeated_by_talent=any(0<=other['start']-end<=3 and interval_role(other['start'],other['end'],windows)=='talent'
            and SequenceMatcher(None,norm(text),norm(other.get('text',''))).ratio()>=.82 for other in original[i+1:i+3])
        instruction=bool(COMMAND.search(norm(text))) or repeated_by_talent
        # A second full-segment embedding isn't a calibrated probability; all window agreement is mandatory.
        stable=not voice.get('requireStable') or bool(utterance and utterance.get('stable'))
        safe=role=='director' and stable and end-start>=1.5 and instruction and not is_protected
        row={'assetId':item['assetId'],'source':item['source'],'start':start,'end':end,'text':text,
             'role':{'talent':'达人','director':'疑似编导','friend':'闺蜜','mixed':'混合','unknown':'待确认'}[role],
             'voiceRole':role,'words':s.get('words',[]),'protected':is_protected,
             'speaker':s.get('speaker','unknown'),'asrReview':s.get('asrReview'),
             'sourceSegmentIndex':i,'sourceSegmentId':s.get('id'),
             'sourceSegments':[{'index':i,'id':s.get('id'),'start':start,'end':end,'text':text}]}
        if utterance:row['similarities']=utterance['similarities']
        elif s.get('similarities'):row['similarities']=copy.deepcopy(s['similarities'])
        if safe:
            removed.append(dict(row,reason='已确认参考匹配＋指导语/紧邻达人复述；非只凭身份删除',method=REVISION,confirmed=False))
            continue
        units.append(row)
        if role in ('director','mixed','unknown'):
            reviews.append(dict(row,reason='身份/用途不确定，保留待审；不阻塞任务'))
    # Preferences change handles, never the voice-deletion gate above. Neighbour
    # speech must be selected as a whole unit, not smuggled in as half a sentence.
    for row in units:
        start=max(0,row['start']-settings['pre']);end=min(usable,row['end']+settings['post'])
        for a,b,_ in protected:
            if a<=end and b>=row['end'] and a<=row['end']+.8:
                end=min(usable,max(end,min(b,row['end']+settings['eventTail'])))
        for other in original:
            if other['end']<=row['start']:start=max(start,other['end'])
            if other['start']>=row['end']:end=min(end,other['start'])
        for cut in removed:
            if cut['end']<=row['start']:start=max(start,cut['end'])
            if cut['start']>=row['end']:end=min(end,cut['start'])
        row['speechStart'],row['speechEnd']=row['start'],row['end']
        # Padding may be rounded inward to avoid neighbours, but a selected
        # speech core must be rounded OUTWARD. Inward rounding used to lose a
        # frame at every anonymous-speaker split (and occasionally a tiny unit).
        row['start']=max(0,min(math.ceil((start-1e-8)*fps)/fps,
                              math.floor((row['speechStart']+1e-8)*fps)/fps))
        row['end']=min(usable,max(math.floor((end+1e-8)*fps)/fps,
                                 math.ceil((row['speechEnd']-1e-8)*fps)/fps))
        index=row['sourceSegmentIndex']
        row['blockedBefore']=any(c['end']<=row['speechStart'] and
            (index==0 or c['end']>original[index-1]['start']) for c in removed)
        row['dialogueGapLimit']=settings['dialogueGap']
        row['editMode']=settings['mode']
    return [u for u in units if u['end']>u['start']],removed,reviews


def fallback_guard(items):
    """Explicit degraded mode: no voice-based deletion, generous reaction handles."""
    result=copy.deepcopy(items)
    for i in result:
        if main_candidate(i):
            i['voiceGuard']={'windows':[],'protected':[],'degraded':True}
    return result
