"""Local ASR arbitration and anonymous speaker turns, separate from raw evidence.

No script text is supplied to either decoder. A correction requires agreement
between SenseVoice and a fresh, audio-only Whisper pass. AHC labels are NOT
roles, and never propagate a director identity to unknown speech.
"""
import copy
import hashlib
import json
import telemetry
import re
from difflib import SequenceMatcher
from pathlib import Path
import numpy as np
from common import DATA, read, write
from editorial import norm
from speaker import SR, Embedder, wav_read, overlaps
from voice_guard import Events, classify

REVISION = 'speech-refine-1'


def ahc(vectors, threshold=.55):
    """Average-link cosine clustering, no forced speaker count/minority removal."""
    if not vectors: return []
    x=np.asarray(vectors,dtype=float)
    x=x/np.maximum(np.linalg.norm(x,axis=1,keepdims=True),1e-8)
    sim=x@x.T; groups=[[i] for i in range(len(x))]; sizes=np.ones(len(x))
    np.fill_diagonal(sim,-np.inf)
    while len(groups)>1:
        a,b=np.unravel_index(np.argmax(sim),sim.shape)
        if sim[a,b]<threshold:break
        if a>b:a,b=b,a
        sim[a,:]=(sim[a,:]*sizes[a]+sim[b,:]*sizes[b])/(sizes[a]+sizes[b])
        sim[:,a]=sim[a,:];sim[a,a]=-np.inf
        groups[a]+=groups[b];sizes[a]+=sizes[b]
        groups.pop(b);sizes=np.delete(sizes,b)
        sim=np.delete(np.delete(sim,b,axis=0),b,axis=1)
        np.fill_diagonal(sim,-np.inf)
    labels=[0]*len(x)
    for n,g in enumerate(sorted(groups,key=lambda g:min(g))):
        for i in g:labels[i]=n
    return labels


def low_confidence(segment):
    probs=[w.get('probability',1) for w in segment.get('words',[]) if norm(w.get('text',''))]
    return not probs or min(probs)<.55 or sum(probs)/len(probs)<.82


def arbitrate(original, secondary, third):
    """Never regard matching the script as evidence of what was spoken."""
    a,b,c=map(norm,(original,secondary,third))
    if a==b:return original,'agreement'
    if b and b==c and len(b)>=2:return secondary,'audio-consensus-correction'
    return original,'disagreement-kept-original'


def split_words(segment, boundaries):
    """Split only at real ASR word boundaries; keep every word and short turn."""
    words=segment.get('words',[])
    if len(words)<2:return [dict(segment)]
    indices=set()
    for t in boundaries:
        candidates=[(abs((a['end']+b['start'])/2-t),i+1)
                    for i,(a,b) in enumerate(zip(words,words[1:]))
                    if b['start']>=a['end']-.04]
        if candidates:
            distance,index=min(candidates)
            if distance<=.45:indices.add(index)
    # Acoustic word gaps also make useful units, but not arbitrary fixed seconds.
    indices.update(i+1 for i,(a,b) in enumerate(zip(words,words[1:])) if b['start']-a['end']>=.4)
    if not indices:return [dict(segment)]
    spans=[0,*sorted(indices),len(words)];rows=[]
    for a,b in zip(spans,spans[1:]):
        ws=words[a:b]
        start=segment['start'] if a==0 else ws[0]['start']
        end=segment['end'] if b==len(words) else ws[-1]['end']
        if end<=start:continue
        rows.append(dict(segment,start=start,end=end,text=''.join(w['text'] for w in ws),words=ws,
                         splitBasis='声学停顿/匿名声纹变化，吸附到真实词边界'))
    return rows or [dict(segment)]


def ctc_units(decoded, offset, end):
    """Use acoustic CTC token timestamps, never uniform text/time interpolation."""
    tokens=decoded.get('tokens',[]);times=decoded.get('timestamps',[])
    if len(tokens)!=len(times) or not times:return []
    if times!=sorted(times) or any(not np.isfinite(t) or t<0 for t in times):return []
    words=[]
    for i,(text,t) in enumerate(zip(tokens,times)):
        if not norm(text):
            if words:words[-1]['punctuation']=True
            continue
        start=offset+t
        stop=min(end,start+.24,offset+times[i+1] if i+1<len(times) else end)
        if stop>start:words.append({'start':round(start,3),'end':round(stop,3),'text':text,'timing':'SenseVoice CTC'})
    groups=[];current=[]
    for word in words:
        if current and (word['start']-current[-1]['end']>=.32 or current[-1].get('punctuation')):
            groups.append(current);current=[]
        current.append(word)
    if current:groups.append(current)
    return [{'start':max(offset,g[0]['start']-.08),'end':min(end,g[-1]['end']+.12),
             'words':g,'text':''.join(w['text'] for w in g)} for g in groups]


def repair_warped_timing(item, cached, pcm, sense, engine, profile):
    """Long stretched ASR words hide retakes. Recover only with two-engine support."""
    if cached.get('alignmentRevision')=='ctc-2':return cached
    for original in item['segments']:
        if not any(w['end']-w['start']>1.8 and len(norm(w['text']))<=3 for w in original.get('words',[])):continue
        evidence=next((r for r in cached['audit'] if abs(r['start']-original['start'])<.001),None)
        if not evidence or not evidence.get('whisperRetry'):continue
        a=max(0,original['start']-.12);b=min(item['usableDuration'],original['end']+.12)
        decoded=sense(pcm[round(a*SR):round(b*SR)])
        agreement=SequenceMatcher(None,norm(decoded['text']),norm(evidence['whisperRetry'])).ratio()
        if agreement<.8:continue
        replacement=ctc_units(decoded,a,b)
        if not replacement:continue
        for row in replacement:
            x,y=row['start'],row['end']
            role,scores=classify(engine(pcm[round(x*SR):round(y*SR)]) if y-x>=1.5 else None,profile)
            known={w['role'] for w in cached['windows'] if w['role']!='unknown' and overlaps([x,y],[w['start'],w['end']])}
            if role!='unknown' and any(k!=role for k in known):role='mixed'
            row.update(voiceRole=role,similarities=scores,voiceStable=False,speaker='CTC realignment / uncertain',
                originalText=original['text'],asrReview=dict(evidence,decision='audio-consensus-realignment',
                    chosen=row['text'],agreement=round(agreement,3),needsListeningReview=True))
        cached['segments']=[s for s in cached['segments'] if not (s.get('asrReview') and
            abs(s['asrReview']['start']-original['start'])<.001)] + replacement
        evidence.update(decision='audio-consensus-realignment',agreement=round(agreement,3),chosen=decoded['text'],
                        needsListeningReview=True)
    cached['segments'].sort(key=lambda s:s['start']);cached['alignmentRevision']='ctc-2'
    return cached


def refine_speech(items, request, output):
    result=copy.deepcopy(items); engine=Embedder();sense=Events();whisper=None
    cache=DATA/'speech-refine';cache.mkdir(exist_ok=True)
    report={'revision':REVISION,'diarization':'CAM++ / Silero / average-link AHC (local fallback)',
            'overlapDetection':False,'files':[],'corrections':[],'disagreements':[],
            'policy':'匿名分段不等于身份；不删短反应，不猜重叠人声；纠错须双引擎声学一致，原转写保留。'}
    people=[i for i in result if i.get('voiceGuard',{}).get('fingerprint')]
    for number,item in enumerate(people,1):
        guard=item['voiceGuard'];duration=item['usableDuration']
        key=hashlib.sha256(json.dumps([REVISION,guard['fingerprint'],item['segments'],request.get('model','small')],
                                    sort_keys=True,ensure_ascii=False).encode()).hexdigest()
        path=cache/(key+'.json');cached=read(path)
        telemetry.event('cache', 'speech_hit' if cached else 'speech_miss', count=1)
        print(f'转写交叉复核与说话人分段 {number}/{len(people)}：{Path(item["source"]).name}'+('（缓存）' if cached else ''),flush=True)
        if not cached:
            pcm=wav_read(DATA/'voice-cache'/(guard['fingerprint']+'.wav'))
            fixed=[];audit=[]
            for raw in item.get('segments',[]):
                row=copy.deepcopy(raw)
                if low_confidence(row) or row['end']-row['start']>8:
                    start=max(0,row['start']-.12);end=min(duration,row['end']+.12)
                    chunk=pcm[round(start*SR):round(end*SR)]
                    alternative=sense(chunk)
                    third='';third_words=[]
                    if norm(alternative['text'])!=norm(row['text']):
                        if whisper is None:
                            from faster_whisper import WhisperModel
                            with telemetry.measure('whisper_recheck'):
                                whisper=WhisperModel(request.get('model','small'),device='cpu',compute_type='int8',
                                    cpu_threads=4,download_root=str(DATA/'models'),local_files_only=True)
                        decoded,_=whisper.transcribe(chunk.astype(np.float32)/32768,language='zh',
                            vad_filter=False,condition_on_previous_text=False,word_timestamps=True,beam_size=5)
                        for part in decoded:
                            third+=part.text.strip()
                            third_words += [{'start':round(start+w.start,3),'end':min(duration,round(start+w.end,3)),
                                             'text':w.word,'probability':round(w.probability,3)} for w in part.words or []]
                    text,decision=arbitrate(row['text'],alternative['text'],third)
                    evidence={'source':item['source'],'assetId':item['assetId'],'start':row['start'],'end':row['end'],
                        'original':row['text'],'senseVoice':alternative['text'],'whisperRetry':third,
                        'decision':decision,'chosen':text}
                    audit.append(evidence);row['asrReview']=evidence
                    if decision=='audio-consensus-correction' and third_words:
                        row.update(text=text,words=third_words,start=third_words[0]['start'],end=third_words[-1]['end'],
                                   originalText=raw['text'])
                fixed.append(row)
            from faster_whisper.vad import get_speech_timestamps,VadOptions
            vad=get_speech_timestamps(pcm.astype(np.float32)/32768,VadOptions(min_silence_duration_ms=250,speech_pad_ms=80))
            windows=[];vectors=[]
            for v in vad:
                a,b=v['start']/SR,v['end']/SR
                for start in np.arange(a,max(a+.01,b-.6),.75):
                    end=min(float(start)+1.5,b)
                    vector=engine(pcm[round(start*SR):round(end*SR)])
                    if vector is None:continue
                    role,scores=classify(vector,request.get('voiceProfile'))
                    windows.append({'start':round(float(start),3),'end':round(end,3),'role':role,'similarities':scores})
                    vectors.append(vector)
            labels=ahc(vectors)
            for window,label in zip(windows,labels):window['anonymous']=f'speaker_{label:02d}'
            changes=[]
            for n in range(1,len(windows)):
                left,right=windows[n-1:n+1]
                # Require persistence on both sides, except a clear VAD gap.
                stable=(n>=2 and n+1<len(labels) and labels[n-2]==labels[n-1] and labels[n]==labels[n+1])
                if labels[n]!=labels[n-1] and (stable or right['start']-left['end']>=.18):
                    changes.append(round((left['start']+left['end']+right['start']+right['end'])/4,3))
            turns=[]
            for segment in fixed:
                for row in split_words(segment,[t for t in changes if segment['start']<t<segment['end']]):
                    a,b=row['start'],row['end']
                    vector=engine(pcm[round(a*SR):round(b*SR)]) if b-a>=1.5 else None
                    role,scores=classify(vector,request.get('voiceProfile'))
                    hits=[w for w in windows if min(b,w['end'])-max(a,w['start'])>=.25]
                    identities={w['role'] for w in hits if w['role']!='unknown'}
                    if role!='unknown' and any(r!=role for r in identities):role='mixed'
                    anonymous={w['anonymous'] for w in hits}
                    # For deletion, two independent internal subwindows must support the role.
                    halves=[classify(engine(pcm[round(x*SR):round(y*SR)]),request.get('voiceProfile'))[0]
                            for x,y in [(a,(a+b)/2),((a+b)/2,b)]] if b-a>=2 else []
                    stable=role=='director' and halves==['director','director'] and len(anonymous)<=1
                    row.update(speaker=next(iter(anonymous)) if len(anonymous)==1 else 'mixed/uncertain',
                               voiceRole=role,voiceStable=stable,similarities=scores)
                    turns.append(row)
            cached={'segments':turns,'windows':windows,'changes':changes,'audit':audit,
                    'anonymousSpeakerCount':len(set(labels)),'originalSegmentCount':len(item['segments'])}
            write(path,cached)
        if cached.get('alignmentRevision')!='ctc-2':
            pcm=wav_read(DATA/'voice-cache'/(guard['fingerprint']+'.wav'))
            cached=repair_warped_timing(item,cached,pcm,sense,engine,request.get('voiceProfile'))
            write(path,cached)
        item['rawSegments']=item['segments'];item['segments']=cached['segments'];item['speechRefine']=cached
        guard['windows']=cached['windows'];guard['utterances']=[{'start':s['start'],'end':s['end'],
            'role':s['voiceRole'],'similarities':s['similarities'],'stable':s['voiceStable']} for s in cached['segments']]
        guard['requireStable']=True
        # Keep short non-verbal vocalizations and explicit friends, regardless of embeddings.
        for s in cached['segments']:
            if s['voiceRole']=='friend' or re.fullmatch(r'[嗯啊哇哦哈嘿呀诶哎唔]+',norm(s['text'])):
                guard['protected'].append([max(0,s['start']-.2),min(duration,s['end']+.8),'闺蜜/短反应保护'])
        report['files'].append({'source':item['source'],'assetId':item['assetId'],
            'originalSegments':cached['originalSegmentCount'],'turns':len(cached['segments']),
            'anonymousSpeakers':cached['anonymousSpeakerCount'],'changePoints':cached['changes']})
        report['corrections'] += [r for r in cached['audit'] if r['decision']=='audio-consensus-correction']
        report.setdefault('realignments',[]).extend(r for r in cached['audit'] if r['decision']=='audio-consensus-realignment')
        report['disagreements'] += [r for r in cached['audit'] if r['decision']=='disagreement-kept-original']
        write(output/'speech-review.json',report)
    write(output/'refined-items.json',{'items':result})
    return result,report
