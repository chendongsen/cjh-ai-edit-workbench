"""Auditable local multi-frame review of BOTH boundaries of every selected take.

Only extends existing selections. A visual model cannot remove audio, assign a
speaker identity, or trim a selected reaction. Original and candidate cut points
are saved with the exact source-frame timestamps and a contact sheet.
"""
import base64
import copy
import hashlib
import json
import telemetry
import math
import subprocess
import urllib.request
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont
import imageio_ffmpeg
from common import DATA, OLLAMA_URL, read, write, local_urlopen
from night_edit import merge
from edit_policy import policy, cap_visual_extension

REVISION='reaction-review-3'
STEP=.25


def sample_times(cut, before, after, duration):
    return sorted({round(min(duration-.04,max(0,cut+i*STEP)),3)
                   for i in range(-round(before/STEP),round(after/STEP)+1)})


def validate(result, starts, ends):
    if not isinstance(result,dict):raise ValueError('切点视觉结果不是对象')
    for key,count in [('startIndex',len(starts)),('endIndex',len(ends))]:
        n=result.get(key)
        if type(n) is not int or not 0<=n<count:raise ValueError('切点视觉帧序号无效')
    for key in ['startContinuous','endContinuous']:
        if type(result.get(key)) is not bool:raise ValueError('切点视觉连续性字段无效')
    if type(result.get('confidence')) not in (int,float) or not 0<=result['confidence']<=1:
        raise ValueError('切点视觉置信度无效')
    return result


def safe_extension(row, suggestion, item, fps):
    """Do not import a chopped next utterance or cross a removed speech boundary."""
    a,b=row['start'],row['end'];duration=min(item['duration'],item.get('usableDuration',item['duration']))
    start=max(0,min(a,suggestion[0]));end=min(duration,max(b,suggestion[1]))
    speech_start=row.get('speechStart',a);speech_end=row.get('speechEnd',b)
    for segment in item.get('segments',[]):
        x,y=segment['start'],segment['end']
        if y<=speech_start+.01 and y>a-.8:start=max(start,min(a,y))
        if x>=speech_end-.01 and x<b+3.1:
            # This is a different utterance, not evidence of silence. Stop before
            # it; already-selected audio is never shortened by a vision decision.
            end=min(end,max(b,x))
    # Semantic sentence boundaries inside a long ASR block are not visible in
    # the original segment list. Never restore an excluded sentence as a visual
    # handle, including when expressive mode scans past a long silent pause.
    for x,y in row.get('scriptBoundaryStops',[]):
        if y<=speech_start+1e-6:
            start=max(start,min(a,math.ceil((y-1e-8)*fps)/fps))
        if x>=speech_end-1e-6:
            end=min(end,max(b,math.floor((x+1e-8)*fps)/fps))
    # The final media frame can be fractional in container seconds. Round the
    # output frame outward rather than shaving the last audible syllable.
    return max(0,math.floor(start*fps)/fps),min(math.ceil((duration-1e-8)*fps)/fps,math.ceil((end-1e-8)*fps)/fps)


def make_board(source, starts, ends, directory, old):
    directory.mkdir(parents=True,exist_ok=True)
    times=sorted(set(starts+ends))
    def frame(t):
        path=directory/f'{t:.3f}.jpg'
        if not path.exists():
            subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-nostdin','-y','-ss',str(t),'-i',source,
                '-frames:v','1','-vf','scale=320:480:force_original_aspect_ratio=decrease',
                '-threads','1','-q:v','3',str(path)],check=True,capture_output=True,timeout=60)
        return path
    paths=dict(zip(times,ThreadPoolExecutor(max_workers=3).map(frame,times)))
    entries=[('S',n,t) for n,t in enumerate(starts)]+[('E',n,t) for n,t in enumerate(ends)]
    board=Image.new('RGB',(1200,50+math.ceil(len(entries)/6)*280),'#151515');d=ImageDraw.Draw(board)
    font=ImageFont.truetype('/System/Library/Fonts/Menlo.ttc',14)
    d.text((8,10),f'S = start boundary, E = end boundary | original {old[0]:.3f} - {old[1]:.3f}s',font=font,fill='white')
    for k,(label,n,t) in enumerate(entries):
        x=(k%6)*200;y=50+(k//6)*280
        with Image.open(paths[t]) as im:
            im.thumbnail((200,250));board.paste(im,(x+(200-im.width)//2,y))
        d.text((x+3,y+253),f'{label}{n:02d} {t:.3f}s',font=font,fill='#ffb45c' if t<old[0] or t>=old[1] else '#74ed99')
    file=directory/'contact.jpg';board.save(file,quality=90)
    return file,[{'at':t,'file':str(paths[t].relative_to(DATA))} for t in times]


def infer(board, starts, ends, model, old):
    if not model or 'cloud' in model.lower():raise ValueError('切点复核需要已安装的本地视觉模型')
    labels=['bite','chew','smile','gesture','handoff','look_shift','neutral','unclear']
    times=starts+ends
    schema={'type':'object','properties':{'frames':{'type':'array','minItems':len(times),'maxItems':len(times),
        'items':{'type':'object','properties':{'index':{'type':'integer'},'action':{'type':'string','enum':labels},
                    'confidence':{'type':'number'}},'required':['index','action','confidence']}}},'required':['frames']}
    prompt=('按给定顺序逐张观察独立视频帧。每张只标一个清楚可见的动作：bite咬下/chew咀嚼/'
        'smile微笑/gesture手势/handoff递接物/look_shift转头看向别处/neutral无明显动作/unclear看不清。'
        '不猜人物身份或内心情绪，不把所有张嘴都当咀嚼，不根据之前图片复制结论。'
        '返回frames数组，每帧index从0开始、action、confidence(0到1)。没有额外文字。'
        f'前{len(starts)}张是入点序列，后{len(ends)}张是出点序列。共{len(times)}张，逐张独立判断。')
    rows=[]
    # Qwen image tokens count toward context. Bounded batches avoid silently
    # overflowing the local model on 23 or more source frames.
    for offset in range(0,len(times),6):
        batch=times[offset:offset+6]
        print(f'  本地逐帧动作识别 {offset+1}—{offset+len(batch)}/{len(times)}',flush=True)
        schema['properties']['frames'].update(minItems=len(batch),maxItems=len(batch))
        batch_prompt=prompt.split('前')[0]+f'本次只有{len(batch)}张，index严格从0到{len(batch)-1}。'
        payload={'model':model,'stream':False,'format':schema,'options':{'temperature':0,'num_ctx':12288,'num_predict':768},
            'messages':[{'role':'user','content':batch_prompt,'images':[
                base64.b64encode((board.parent/f'{t:.3f}.jpg').read_bytes()).decode() for t in batch]}]}
        req=urllib.request.Request(OLLAMA_URL + '/api/chat',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
        partial=read(board.parent/f'batch-{offset}.json')
        if partial is None:
            try:
                with local_urlopen(req,timeout=180) as r:body=telemetry.ollama(json.loads(r.read()))
            except urllib.error.HTTPError as e:
                raise ValueError('本地模型请求失败：'+e.read().decode()[:200]) from e
            partial=json.loads(body['message']['content']).get('frames',[])
        if sorted(r.get('index',-1) for r in partial)!=list(range(len(batch))):raise ValueError('逐帧动作序号不完整')
        write(board.parent/f'batch-{offset}.json',partial)
        rows.extend(dict(r,index=r['index']+offset) for r in partial)
    rows=sorted(rows,key=lambda r:r['index'])
    for r in rows:
        if r.get('action') not in labels or type(r.get('confidence')) not in (int,float) or not 0<=r['confidence']<=1:
            raise ValueError('逐帧动作结构无效')
    def active(r):return r['confidence']>=.75 and r['action'] not in ('neutral','unclear')
    si=min(range(len(starts)),key=lambda i:abs(starts[i]-old[0]))
    ei=min(range(len(ends)),key=lambda i:abs(ends[i]-old[1]))
    left=si;right=ei;ss=rows[:len(starts)];ee=rows[len(starts):]
    # At least two neighboring observations, not one model score. Conservative
    # bounded extensions; model output alone cannot carry a reaction indefinitely.
    while left>0 and active(ss[left]) and active(ss[left-1]):left-=1
    while right+1<len(ends) and active(ee[right]) and active(ee[right+1]):
        limit=3 if ee[right]['action'] in ('chew','bite','handoff') else 1.5
        if ends[right+1]-old[1]>limit:break
        right+=1
    observed={'startIndex':left,'endIndex':right,'startContinuous':left<si,'endContinuous':right>ei,
        'confidence':min([r['confidence'] for r in ss[left:si+1]+ee[ei:right+1]] or [0]),
        'observation':'逐帧动作候选：'+','.join(sorted({r['action'] for r in ss[left:si+1]+ee[ei:right+1]})),
        'frameActions':rows}
    return validate(observed,starts,ends)


def review_boundaries(selection, items, request, output):
    settings=policy(request.get('editMode'))
    byid={i['assetId']:i for i in items};rows=[];reviews=[];warnings=[]
    cache=DATA/'reaction-cache';cache.mkdir(exist_ok=True)
    fps=int(request.get('fps',30));model=request.get('visionModel')
    for n,original in enumerate(selection,1):
        row=copy.deepcopy(original)
        # An explicit scripted exchange can include a silent handoff. This is
        # required content; omitted speech still blocks merging across the gap.
        row['dialogueGapLimit']=8 if row.get('dialogueProtected') else settings['dialogueGap']
        item=byid[row['assetId']]
        duration=min(item['duration'],item.get('usableDuration',item['duration']))
        starts=sample_times(row['start'],.75,.75,duration)
        ends=sample_times(row['end'],.75,3,duration)
        stat=Path(item['source']).stat()
        key=hashlib.sha256(json.dumps([REVISION,settings,model,item['source'],stat.st_size,stat.st_mtime_ns,starts,ends]).encode()).hexdigest()
        folder=cache/key;folder.mkdir(exist_ok=True);result=read(folder/'result.json')
        telemetry.event('cache', 'boundary_hit' if result else 'boundary_miss', count=1)
        print(f'切点前后多帧复核 {n}/{len(selection)}：{Path(item["source"]).name}',flush=True)
        evidence={'assetId':row['assetId'],'source':item['source'],'text':row['text'],
            'before':[row['start'],row['end']],'step':STEP,'startTimes':starts,'endTimes':ends,
            'editMode':settings['mode'],'policyRevision':settings['revision']}
        try:
            if not result:
                board,frames=make_board(item['source'],starts,ends,folder,(row['start'],row['end']))
                observed=infer(board,starts,ends,model,(row['start'],row['end']))
                result={'observed':observed,'frames':frames,'contactSheet':str(board.relative_to(DATA)),
                        'continuations':[]}
                edge=ends[observed['endIndex']]
                for step in range(settings['continuation']):
                    last_actions=observed.get('frameActions',[])[-2:]
                    # A bite/handoff still running at the review-window edge needs
                    # another window, not a blind fixed tail or premature cut.
                    if not (observed['endContinuous'] and edge>=ends[-1]-.26 and len(last_actions)==2
                            and all(r['action'] in ('bite','chew','handoff') and r['confidence']>=.75 for r in last_actions)):
                        break
                    limit=min(duration-.04,row['end']+settings['visualAfter'])
                    _,allowed=safe_extension(row,(row['start'],limit),item,fps)
                    limit=min(limit,allowed)
                    if limit<=edge+.25:break
                    follow_ends=[t for t in sample_times(edge,.5,3,duration) if t<=limit]
                    follow_starts=[row['start']]
                    if len(follow_ends)<3:break
                    follow_board,follow_frames=make_board(item['source'],follow_starts,follow_ends,folder/f'continuation-{step+1}',(row['start'],edge))
                    observed=infer(follow_board,follow_starts,follow_ends,model,(row['start'],edge))
                    result['continuations'].append({'observed':observed,'endTimes':follow_ends,
                        'contactSheet':str(follow_board.relative_to(DATA))})
                    result['frames']+=follow_frames
                    if observed['confidence']>=.7 and observed['endContinuous']:
                        edge=follow_ends[observed['endIndex']]
                        result['extendedEnd']=edge+.25
                    else:break
                    ends_for_edge=follow_ends
                    if edge<ends_for_edge[-1]-.26:break
                write(folder/'result.json',result)
            obs=validate(result['observed'],starts,ends)
            a,b=row['start'],row['end']
            if obs['confidence']>=.7:
                if obs['startContinuous']:a=min(a,starts[obs['startIndex']])
                if obs['endContinuous']:b=max(b,ends[obs['endIndex']]+.25)
            if result.get('extendedEnd'):b=max(b,result['extendedEnd'])
            actions=[r for r in obs.get('frameActions',[])[len(starts):]]
            for continuation in result.get('continuations',[]):
                actions+=continuation['observed'].get('frameActions',[])[1:]
            # A model may call a wide-eyed held reaction "neutral". After a bite
            # or handoff, retain 1.2s settling time instead of trusting that label
            # as an exact expression endpoint. Still bounded by the next speech.
            settle=any(r['action'] in ('bite','chew','handoff') and r['confidence']>=.75 for r in actions)
            if settle and b>row['end']+.1:
                b+=settings['settle']
                evidence['settleHandleSeconds']=settings['settle']
            suggestion=(a,b)
            a,b=cap_visual_extension(row,suggestion,settings,fps)
            evidence['policyLimited']=a>suggestion[0]+1/fps or b<suggestion[1]-1/fps
            if evidence['policyLimited']:
                evidence['policyReview']='已限制台词之外的额外留量；若此处动作未完成，请在剪映回拉原片复核'
                row['policyReview']=evidence['policyReview']
            a,b=safe_extension(row,(a,b),item,fps)
            row.update(start=a,end=b,boundaryReviewed=True)
            if a<original['start']-1/fps or b>original['end']+1/fps:
                row['protected']=True
                row['reactionProtection']={'start':a,'end':b,'reason':obs['observation'],'method':REVISION}
            evidence.update(result,status='reviewed',after=[a,b])
        except Exception as e:
            evidence.update(status='failed-kept-original',error=str(e),after=evidence['before'])
            warnings.append(f'第{n}段切点视觉未完成，保留原切点：{e}')
        rows.append(row);reviews.append(evidence)
        write(output/'boundary-review.json',{'revision':REVISION,'editPolicy':settings,'step':STEP,'clips':reviews,'warnings':warnings})
    merged=merge(rows)
    report={'revision':REVISION,'editPolicy':settings,'step':STEP,'clips':reviews,'warnings':warnings,
        'policyLimitedClips':sum(r.get('policyLimited',False) for r in reviews),
        'reviewedClips':sum(r['status']=='reviewed' for r in reviews),
        'extendedClips':sum(r['after']!=r['before'] for r in reviews),
        'frameCount':sum(len(r.get('frames',[])) for r in reviews),
        'beforeSeconds':round(sum(r['end']-r['start'] for r in selection),3),
        'afterSeconds':round(sum(r['end']-r['start'] for r in merged),3)}
    write(output/'boundary-review.json',report)
    return merged,report
