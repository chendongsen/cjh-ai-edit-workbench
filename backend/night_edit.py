"""Auditable script retrieval, conservative voice guards, and a morning review list.

Script timecodes are deliberately ignored: selection is recomputed from ASR.
No user identity inference, cloud call, or source media modification here.
"""
import copy
import math
import re
from difflib import SequenceMatcher
from pathlib import Path
from functools import lru_cache
from editorial import norm, main_candidate, explicit_broll_folder
from voice_guard import guarded_speech, COMMAND
from edit_policy import policy
from script_normalize import normalize_script
from speech_sentences import sentence_spans, script_spans


def targets(script):
    # Intake preview, API validation and actual retrieval share one parser.
    # Only extract existing words; never generate dialogue from shot directions.
    return normalize_script(script)['targets']


def merge(units):
    """Join intact required takes, and never play overlapping handles twice.

    The mandatory speech join is mode-independent. Optional long dialogue
    pauses still follow the selected reaction policy, never a removed command.
    """
    result=[]
    for unit in units:
        row=copy.deepcopy(unit)
        dialogue_gap=bool(result and row.get('dialogueProtected') and result[-1].get('dialogueProtected')
                          and row.get('scriptTarget')==result[-1].get('scriptTarget'))
        gap=min(row.get('dialogueGapLimit',8),result[-1].get('dialogueGapLimit',8)) if dialogue_gap else 0
        same_take=bool(result and row.get('requiredTakeId') and
                       row.get('requiredTakeId')==result[-1].get('requiredTakeId'))
        if same_take:gap=max(gap,policy()['requiredJoinGap'])
        barrier=row.get('blockedBefore',False)
        if result and row['assetId']==result[-1]['assetId']:
            left_end=result[-1].get('speechEnd',result[-1]['end'])
            right_start=row.get('speechStart',row['start'])
            # A shared requiredTakeId is not permission to reinsert a complete
            # off-script sentence between two selected sentences.
            barrier=barrier or any(a>=left_end-1e-6 and b<=right_start+1e-6 and b>a
                for a,b in row.get('scriptBoundaryStops',[])+result[-1].get('scriptBoundaryStops',[]))
        if result and not barrier and row['assetId']==result[-1]['assetId'] and result[-1]['start']<=row['start']<=result[-1]['end']+gap:
            last=result[-1]
            last['end']=max(last['end'],row['end'])
            last['speechStart']=min(last.get('speechStart',last['start']),row.get('speechStart',row['start']))
            last['speechEnd']=max(last.get('speechEnd',last['end']),row.get('speechEnd',row['end']))
            # Identical words at different source times are not the same token.
            # Deduplicate source units by provenance, never by string containment.
            seen={(s['index'],s['start'],s['end']) for s in last.get('sourceSegments',[])}
            incoming=row.get('sourceSegments',[])
            if not incoming or any((s['index'],s['start'],s['end']) not in seen for s in incoming):
                last['text']+=' '+row['text']
            last['words']+=row.get('words',[])
            last.setdefault('sourceSegments',[]).extend(s for s in incoming if (s['index'],s['start'],s['end']) not in seen)
            _merge_evidence(last,row)
            if row.get('requiredTakeId'):last['requiredTakeId']=row['requiredTakeId']
            last['protected']=last.get('protected') or row.get('protected')
            last['boundaryReviewed']=last.get('boundaryReviewed') and row.get('boundaryReviewed')
            if row.get('reactionProtection'):last['reactionProtection']=row['reactionProtection']
            if row.get('scriptVisualReview'):
                last.update(scriptVisualReview=True,visualClassReview=row['visualClassReview'],
                            sourceVisualKind=row.get('sourceVisualKind'))
            if row.get('policyReview'):
                last['policyReview']='；'.join(dict.fromkeys(filter(None,[last.get('policyReview'),row['policyReview']])))
            if last.get('voiceRole')!=row.get('voiceRole'):last['voiceRole']='mixed';last['role']='混合'
        else:result.append(row)
    return result


def _merge_evidence(row, other):
    for key in ('scriptTargetIds','scriptScenes'):
        if key in other:row[key]=list(dict.fromkeys(row.get(key,[])+other[key]))
    if other.get('scriptCoverage'):
        evidence={r['target']:r for r in row.get('scriptCoverage',[])}
        for record in other['scriptCoverage']:
            previous=evidence.get(record['target'])
            if previous is None or record['ratio']>previous['ratio']:evidence[record['target']]=copy.deepcopy(record)
        row['scriptCoverage']=list(evidence.values())
        row['scriptStatus']='partial' if any(r['status']!='complete' for r in evidence.values()) else 'complete'
    if other.get('scriptBoundaryStops'):
        row['scriptBoundaryStops']=list({tuple(span) for span in row.get('scriptBoundaryStops',[])+other['scriptBoundaryStops']})
    if other.get('scriptSegmentationReview'):
        row['scriptSegmentationReview']='；'.join(dict.fromkeys(filter(None,
            [row.get('scriptSegmentationReview'),other['scriptSegmentationReview']])))
    if other.get('audioProtectionEvidence'):
        evidence={(r['start'],r['end'],r['reason']):r for r in row.get('audioProtectionEvidence',[])}
        evidence.update({(r['start'],r['end'],r['reason']):copy.deepcopy(r) for r in other['audioProtectionEvidence']})
        row['audioProtectionEvidence']=list(evidence.values())
    if other.get('reactionEdgeProtection'):
        evidence={(r['start'],r['end'],r['reason']):r for r in row.get('reactionEdgeProtection',[])}
        evidence.update({(r['start'],r['end'],r['reason']):copy.deepcopy(r) for r in other['reactionEdgeProtection']})
        row['reactionEdgeProtection']=list(evidence.values())
    if other.get('reactionBasis'):
        row['reactionBasis']='；'.join(dict.fromkeys(filter(None,[row.get('reactionBasis'),other['reactionBasis']])))
    if other.get('reactionRecoveryReview'):
        row['reactionRecoveryReview']='；'.join(dict.fromkeys(filter(None,
            [row.get('reactionRecoveryReview'),other['reactionRecoveryReview']])))


_DIGITS={c:i for i,c in enumerate('零一二三四五六七八九')}
_DIGITS.update({'〇':0,'两':2})
_UNITS={'十':10,'百':100,'千':1000,'万':10000,'亿':100000000}
_MEASURE=r'公里|千米|厘米|毫米|米|公斤|千克|克|小时|分钟|秒|元|块|个|片|岁|度|倍|天|年|次|点'


def _number(value):
    """Conservative Chinese numeral parsing for measured quantities only."""
    if '点' in value:
        integer,decimal=value.split('点',1)
        return _number(integer)+'.'+''.join(str(_DIGITS[c]) for c in decimal)
    if not any(c in _UNITS for c in value):return ''.join(str(_DIGITS[c]) for c in value)
    total=section=number=0
    for char in value:
        if char in _DIGITS:number=_DIGITS[char]
        elif _UNITS[char]<10000:
            section+=(number or 1)*_UNITS[char];number=0
        else:
            total+=(section+number)*_UNITS[char];section=number=0
    return str(total+section+number)


def match_norm(text):
    """Retrieval-only equivalence: 18 km == 十八公里, never rewrite ASR."""
    text=norm(re.sub(r'(?<=\d)[.．](?=\d)','小数点',text)).replace('小数点','.')
    aliases={'kilometers':'公里','kilometer':'公里','km':'公里','千米':'公里',
             '厘米':'厘米','cm':'厘米','毫米':'毫米','mm':'毫米','kg':'公斤','千克':'公斤',
             '小时':'小时','hours':'小时','hour':'小时','分钟':'分钟','min':'分钟','m':'米'}
    for source,destination in aliases.items():
        text=re.sub(r'(?<=[0-9])'+re.escape(source)+r'(?![a-z])',destination,text)
    text=text.replace('千米','公里').replace('千克','公斤')
    return re.sub(r'[零〇一二两三四五六七八九十百千万亿]+(?:点[零〇一二两三四五六七八九]+)?(?=(?:'+_MEASURE+r'))',
                  lambda m:_number(m[0]),text)


def similarity(a,b):
    a=match_norm(re.sub(r'(达人|闺蜜|朋友)[：:]', '',a)); b=match_norm(b)
    if not a or not b:return 0
    ratio=SequenceMatcher(None,a,b,autojunk=False).ratio()
    # Containment cannot reward a very long, unrelated take.
    if a in b:ratio=max(ratio,min(.97,len(a)/max(len(b),1)+.15))
    if a in b and len(a)>=4 and len(b)<=3*len(a):ratio=max(ratio,.72)
    return ratio


def text_coverage(expected, observed, phonetic_match=False):
    """Character evidence, not a claim of semantic equivalence or speaker ID.

    Even a high-ranking candidate can be partial. Missing words and quantities
    stay visible instead of turning a retrieval hit into a completed sentence.
    """
    a,b=match_norm(expected),match_norm(observed)
    matcher=SequenceMatcher(None,a,b,autojunk=False)
    blocks=matcher.get_matching_blocks()
    matched=sum(block.size for block in blocks)
    missing=[a[i:j] for kind,i,j,_,_ in matcher.get_opcodes() if kind!='equal' and j>i]
    quantity_pattern=r'\d+(?:\.\d+)?(?:'+_MEASURE+r')'
    quantities=re.findall(quantity_pattern,a)
    observed_quantities=set(re.findall(quantity_pattern,b))
    absent=[value for value in quantities if value not in observed_quantities]
    ratio=matched/max(1,len(a))
    # Exact normalized containment is positive evidence. For noisy ASR, allow
    # only tiny interior substitutions; never waive a missing beginning/tail.
    edge_missing=bool(a and (not blocks[0].size or blocks[0].a>0 or
        (len(blocks)>1 and blocks[-2].a+blocks[-2].size<len(a))))
    complete=bool(a and not phonetic_match and not absent and
                  (a in b or (ratio>=.94 and not edge_missing and max(map(len,missing),default=0)<=1)))
    return {'status':'complete' if complete else 'partial','ratio':round(ratio,4),
            'missingText':missing,'missingQuantities':absent,
            'expectedText':expected,'matchedText':observed,
            'basis':'同源读音候选，文字覆盖待听审' if phonetic_match else '数字单位归一化后的文字覆盖（未改写转写）'}


@lru_cache(maxsize=8192)
def phonetic(text):
    from pypinyin import lazy_pinyin
    return ''.join(lazy_pinyin(norm(text).strip('啊呀哦啦吧呢')))


def phonetic_similarity(target,text):
    """Retrieval only. Never replace transcript with script or remove audio."""
    a=norm(re.sub(r'(达人|闺蜜|朋友)[：:]', '',target));b=norm(text)
    if len(a)<6 or not .65<=len(b)/len(a)<=1.6:return 0
    try:return SequenceMatcher(None,phonetic(a),phonetic(b)).ratio()
    except ImportError:return 0


def _joinable(left,right,dialogue=False):
    if left['assetId']!=right['assetId'] or right.get('blockedBefore'):return False
    if right['speechStart']<left['speechStart']:return False
    same_block=bool(left.get('sentenceParentId') and left.get('sentenceParentId')==right.get('sentenceParentId'))
    if not same_block and right['speechStart']-left['speechEnd']>(8 if dialogue else 3):return False
    # Anonymous clusters are not reliable identities; do not split a sentence
    # on their labels. A known director/performance transition is a take edge.
    known={left.get('voiceRole'),right.get('voiceRole')}
    if not dialogue and 'director' in known and known & {'talent','friend'}:return False
    if not dialogue and any(COMMAND.search(norm(r.get('text',''))) for r in (left,right)):return False
    return True


def _sentence_key(row):
    if row.get('semanticUnitId'):return ('sentence',row['assetId'],row['semanticUnitId'])
    review=row.get('asrReview') or {}
    if review.get('decision')=='audio-consensus-realignment':
        # These are already independently timed utterances. The review's
        # start/end identifies the ORIGINAL batch, not a sentence to restore.
        return ('realigned',row['assetId'],row['speechStart'],row['speechEnd'])
    if row.get('sourceSegmentId'):return ('id',row['assetId'],row['sourceSegmentId'])
    if review.get('original') and 'start' in review and 'end' in review:
        return ('asr',row['assetId'],review['start'],review['end'])
    return None


def _audio_protection(start, end, windows):
    return [{'start':a,'end':b,'reason':reason} for a,b,reason in windows
            if a<end and b>start]


def _sentence_reaction_edges(span, before, after, protected_windows):
    """Keep observed short reactions in a sentence's adjacent silent gaps.

    Coarse windows are not precise reaction times. A preserved edge is clipped
    before the next spoken core, so it cannot restore an omitted sentence.
    """
    edges=[]
    for a,b,reason in protected_windows:
        if b<=a or b-a>5.5 or re.search(r'窗口级|整段|全片|整片|整条',str(reason)):
            continue
        if a<span['start'] and b>=span['start']-.8:
            x,y=max(before,a),min(span['start'],b)
            if y>x:edges.append({'start':x,'end':y,'reason':str(reason)})
        if b>span['end'] and a<=span['end']+.8:
            x,y=max(span['end'],a),min(after,b)
            if y>x:edges.append({'start':x,'end':y,'reason':str(reason)})
    return edges


def _script_sentence_units(rows, fps, settings, protected_windows, wanted=()):
    """Separate complete ASR sentences; never cut arbitrary script substrings."""
    result=[]
    for row in rows:
        row=dict(row,audioProtectionEvidence=_audio_protection(row['speechStart'],row['speechEnd'],protected_windows))
        source=dict(row,start=row['speechStart'],end=row['speechEnd'])
        phrases=[t['text'] for t in wanted if not t.get('sourceName') or
                 Path(row['source']).name==t['sourceName']]
        spans, reason=script_spans(source,phrases,match_norm)
        if not spans:
            if reason:row=dict(row,scriptSegmentationReview=reason)
            result.append(row)
            continue
        for index,span in enumerate(spans):
            part=copy.deepcopy(row)
            event_evidence=_audio_protection(span['start'],span['end'],protected_windows)
            stops=[[s['start'],s['end']] for s in spans if s['sentenceIndex']!=index]
            before=spans[index-1]['end'] if index else row['start']
            after=spans[index+1]['start'] if index+1<len(spans) else row['end']
            start=max(before,span['start']-settings['pre'])
            end=min(after,span['end']+settings['post'])
            reaction_edges=_sentence_reaction_edges(span,before,after,protected_windows)
            # The whole spoken core remains outward-rounded below. Extra
            # reaction frames are rounded outward only within the silent gap.
            for edge in reaction_edges:
                if edge['end']<=span['start']:
                    start=min(start,max(math.ceil((before-1e-8)*fps)/fps,
                                        math.floor((edge['start']+1e-8)*fps)/fps))
                if edge['start']>=span['end']:
                    end=max(end,min(math.floor((after+1e-8)*fps)/fps,
                                    math.ceil((edge['end']-1e-8)*fps)/fps))
            part.update(text=span['text'],words=copy.deepcopy(span['words']),
                speechStart=span['start'],speechEnd=span['end'],
                start=min(math.ceil((start-1e-8)*fps)/fps,math.floor((span['start']+1e-8)*fps)/fps),
                end=max(math.floor((end+1e-8)*fps)/fps,math.ceil((span['end']-1e-8)*fps)/fps),
                semanticUnitId=f'{row["sourceSegmentIndex"]}:{index}',
                sentenceParentId=f'{row["assetId"]}:{row["sourceSegmentIndex"]}',
                protected=bool(event_evidence or row.get('reactionProtection')),
                audioProtectionEvidence=event_evidence,
                reactionEdgeProtection=reaction_edges,
                sourceSegmentIndex=f'{row["sourceSegmentIndex"]}:{index}',
                scriptBoundaryStops=stops,
                sentenceSplitEvidence={k:span[k] for k in ('revision','wordStartIndex','wordEndIndex','sentenceIndex')},
                sourceSegments=[{'index':row['sourceSegmentIndex'],'id':row.get('sourceSegmentId'),
                    'start':span['start'],'end':span['end'],'text':span['text']}])
            result.append(part)
    return result


def _required_sentence_indices(units, indices, expected):
    """Omit only independent sentences proven unnecessary for script coverage.

    No substring cuts and no voice identity inference. A weak/partial match
    cannot justify this omission; all expected characters must remain evidenced.
    """
    selected=list(indices)
    for index in indices:
        row=units[index]
        if not row.get('semanticUnitId') or len(selected)<2:
            continue
        # Audio/visual event protection is stronger evidence than the ASR text.
        # A laugh or surprised reaction may be transcribed as ordinary words,
        # or just “笑声”; absence of “哈哈” is not permission to omit it.
        if row.get('protected') or row.get('reactionProtection'):
            continue
        nonverbal=bool(re.fullmatch(r'[嗯啊哇哦哈嘿呀诶哎唔]+',norm(row['text'])))
        friend_reply=(row.get('voiceRole')=='friend' and row['speechEnd']-row['speechStart']<=4
                      and len(norm(row['text']))<=18 and not COMMAND.search(norm(row['text'])))
        if nonverbal or friend_reply:
            continue
        candidate=[i for i in selected if i!=index]
        evidence=text_coverage(expected,' '.join(units[i]['text'] for i in candidate))
        if evidence['status']=='complete' and evidence['ratio']==1:
            selected=candidate
    return selected


def _reaction_recovery_basis(group, selected_text):
    """Positive evidence for adding a COMPLETE neighbouring reaction only.

    Whole-scene and rolling SenseVoice windows protect already selected audio;
    they do not prove that arbitrary speech inside them is a short reaction.
    """
    text=norm(''.join(row['text'] for row in group))
    start,end=group[0]['speechStart'],group[-1]['speechEnd']
    duration=end-start
    nonverbal=bool(re.fullmatch(r'[嗯啊哇哦哈嘿呀诶哎唔]+',text))
    evidence=[event for row in group for event in row.get('audioProtectionEvidence',[])]
    label=bool(re.fullmatch(r'笑声|大笑|笑|惊呼|惊叫|laughter|laughing',text,re.I))
    if duration<=3 and (nonverbal or (label and evidence)):
        return '相邻完整非言语短反应'
    # A retake already present in the selected wording is not an extra reaction.
    # This does not deduplicate script-required speech: it applies only here,
    # while considering an optional, previously unselected neighbouring unit.
    if len(text)>=2 and text in selected_text:
        return None
    precise_event=any(not re.search(r'窗口级|整段|全片|整片|整条',event['reason'])
        and event['start']>=start-.35 and event['end']<=end+.35
        and event['end']-event['start']<=duration+.7 for event in evidence)
    if precise_event and duration<=5.5:
        return '相邻精确音频事件窗口命中，保留完整短反应'
    friend_reply=(all(row.get('voiceRole')=='friend' for row in group) and duration<=4
                  and len(text)<=18 and not COMMAND.search(text))
    if friend_reply:
        return '相邻已确认闺蜜完整短接话'
    return None


def _whole_source_sentence(units,start,end,dialogue):
    """Reassemble splits of one recorded ASR sentence before emitting cuts."""
    while start and _sentence_key(units[start]) and _sentence_key(units[start-1])==_sentence_key(units[start]):
        if not _joinable(units[start-1],units[start],dialogue):break
        start-=1
    while end<len(units) and _sentence_key(units[end-1]) and _sentence_key(units[end])==_sentence_key(units[end-1]):
        if not _joinable(units[end-1],units[end],dialogue):break
        end+=1
    return start,end


def _script_audio_candidate(item):
    """A picture class is not an audio/dialogue class. Explicit B-roll stays out."""
    if (not item.get('video') or item.get('excluded') is True or
            explicit_broll_folder(item)):return False
    duration=item.get('duration')
    if type(duration) not in (int,float) or not math.isfinite(duration) or duration<=0:return False
    return any(isinstance(s,dict) and type(s.get('start')) in (int,float) and
               type(s.get('end')) in (int,float) and math.isfinite(s['start']) and
               math.isfinite(s['end']) and 0<=s['start']<min(s['end'],duration) and
               norm(s.get('text','')) for s in item.get('segments',[]))


def _production_instruction(text):
    """Only standalone production cues; a scripted request can still use them."""
    return bool(re.fullmatch(r'(?:好|来|对|ok)*(?:321|三二一)(?:开始|开拍)?|'
        r'再来(?:一遍)?|再拍(?:一遍)?|重新拍|看镜头|表情管理|cut|卡',norm(text),re.I))


def _link_existing_quantity_reply(selected, missing):
    """Audit already-selected words; never add media or infer a short response.

    A long ASR unit can contain both a product statement and a response. The
    response may fail whole-unit retrieval while its actual words are already
    in the edit. Require an exact measured quantity plus another continuous
    keyword group, and report only a partial association requiring listening.
    """
    quantity_pattern=r'\d+(?:\.\d+)?(?:'+_MEASURE+r')'
    for target in missing:
        if target.get('status')!='missing':continue
        expected=match_norm(target['text'])
        quantities=re.findall(quantity_pattern,expected)
        if len(expected)<8 or not quantities:continue
        rest=re.sub(quantity_pattern,'',expected)
        options=[]
        for row in selected:
            if target.get('sourceName') and Path(row['source']).name!=target['sourceName']:continue
            words=[w for w in row.get('words',[]) if isinstance(w,dict) and
                   type(w.get('start')) in (int,float) and type(w.get('end')) in (int,float) and
                   math.isfinite(w['start']) and math.isfinite(w['end']) and
                   row['start']<=w['start']<w['end']<=row['end'] and isinstance(w.get('text'),str)]
            words=sorted(words,key=lambda w:(w['start'],w['end']))
            for start in range(len(words)):
                text=''
                for end in range(start,min(len(words),start+2*len(expected))):
                    if end>start and words[end]['start']-words[end-1]['end']>1.2:break
                    text+=words[end]['text']
                    observed=match_norm(text)
                    if len(observed)>2*len(expected)+8:break
                    present=set(re.findall(quantity_pattern,observed))
                    if not set(quantities)<=present:continue
                    other=re.sub(quantity_pattern,'',observed)
                    longest=SequenceMatcher(None,rest,other,autojunk=False).find_longest_match().size
                    if longest<3:continue
                    evidence=text_coverage(target['text'],text)
                    if evidence['ratio']<.5:continue
                    score=similarity(target['text'],text)
                    options.append((score,evidence['ratio'],-len(observed),row,words[start]['start'],words[end]['end'],evidence))
        if not options:continue
        _,_,_,row,start,end,evidence=max(options,key=lambda candidate:candidate[:3])
        evidence.update(target=target['target'],status='partial',sourceStart=start,sourceEnd=end,
            basis='已入选原声中的数字单位＋连续关键词候选；未新增或改写片段，需听审')
        _merge_evidence(row,{'scriptTargetIds':[target['target']],
            'scriptScenes':[target['scene']] if target.get('scene') else [],'scriptCoverage':[evidence]})
        target.update(status='partial',coverage=evidence['ratio'],missingText=evidence['missingText'],
            evidence=copy.deepcopy(evidence),reason='相关词句已随长语音单元进入草稿，但尚未证实整条脚本；需听审，不再误报为完全未找到')


def select(items,script,fps,edit_mode=None):
    settings=policy(edit_mode)
    wanted=targets(script)
    if not wanted and script.strip():
        raise ValueError('已导入脚本，但没有找到明确台词。请检查是否选中了正确脚本；本次已停止，不会把全部口播当成按脚本粗剪。')
    if not wanted:
        raise ValueError('请导入含人物台词的剧本；没有剧本台词时不会生成全量口播。')
    units=[];removed=[];reviews=[]
    for item in sorted(items,key=lambda i:[int(p) if p.isdigit() else p.casefold() for p in re.split(r'(\d+)',i['source'])]):
        original_main=main_candidate(item)
        if not (_script_audio_candidate(item) if wanted else original_main):continue
        guarded_item=item
        if not item.get('voiceGuard'):
            guarded_item=dict(item,voiceGuard={'windows':[],'protected':[],'degraded':True})
        kept,cuts,checks=guarded_speech(guarded_item,min(item.get('usableDuration',item['duration']),item['duration']),fps,edit_mode)
        if wanted:kept=_script_sentence_units(kept,fps,settings,guarded_item['voiceGuard'].get('protected',[]),wanted)
        if wanted and not original_main:
            kind=item.get('vision',{}).get('kind','未分类')
            for row in kept:
                row.update(scriptVisualReview=True,sourceVisualKind=kind,
                    visualClassReview=f'脚本台词匹配入选；原画面分类为{kind}，需复核画面用途和声音身份；未按文件名推断角色')
        units.extend(kept);removed.extend(cuts);reviews.extend(checks)
    missing=[];selection=[]
    if not wanted:return merge(units),removed,reviews,[],['本次没有导入脚本，仅按素材顺序整理口播，不代表已按脚本删减。']
    used=set();selected_units={}
    for index,target in enumerate(wanted):
        options=[];phonetic_options=[]
        target_text=match_norm(target['text'])
        for i,row in enumerate(units):
            if target['sourceName'] and Path(row['source']).name!=target['sourceName']:continue
            group=[];observed=''
            # No six-unit or 24-second sentence truncation. Stop on actual
            # source/take boundaries; a generous text bound only prunes groups
            # already much longer than the target, never short ASR fragments.
            for end in range(i,len(units)):
                unit=units[end]
                if _production_instruction(unit['text']) and match_norm(unit['text']) not in target_text:
                    break
                if group and not _joinable(group[-1],unit,target['dialogue']):break
                group.append(unit);observed+=' '+unit['text']
                candidate_text=match_norm(observed)
                score=similarity(target['text'],observed)
                phonetic_match=False
                if score<(.65 if len(target_text)<5 else settings['minScore']):
                    candidate=phonetic_similarity(target['text'],observed) if target['sourceName'] else 0
                    if candidate<.85:
                        if len(candidate_text)>max(len(target_text)*2+24,60):break
                        continue
                    score=candidate*.8;phonetic_match=True
                coverage=text_coverage(target['text'],observed,phonetic_match)
                # Never label a script omission as a voice-based deletion.
                rank=score+.4*coverage['ratio']+(.12 if coverage['status']=='complete' else 0)
                rank+=.08 if all(g['voiceRole']=='talent' for g in group) else 0
                # Reference similarity is only a small tie-break between text
                # candidates, never an identity or a voice-deletion decision.
                voice=[g.get('similarities',{}) for g in group]
                margins=[v['talent']-max(v.get('director',-1),v.get('friend',-1))
                         for v in voice if 'talent' in v]
                if margins:rank+=max(-.04,min(.04,sum(margins)/len(margins)*.06))
                rank-=.35 if any(g['voiceRole']=='director' for g in group) and not target['dialogue'] else 0
                extra=max(0,len(candidate_text)-len(target_text))
                rank-=settings['extraTextPenalty']*extra/max(1,len(target_text))
                if any(n in used for n in range(i,end+1)):rank-=.12
                (phonetic_options if phonetic_match else options).append((rank,score,i,end+1,phonetic_match))
                if len(candidate_text)>max(len(target_text)*2+24,60):break
        match_basis='文字候选'
        if not options and phonetic_options:
            options=phonetic_options;match_basis='同源读音候选（未改写台词，需听审）'
        if not options:
            missing.append({'target':index+1,**target,'status':'missing','coverage':0,
                            'reason':'没有可靠转写匹配；未编造台词或套用旧切点'})
            continue
        _,score,start,end,is_phonetic=max(options,key=lambda x:x[0])
        start,end=_whole_source_sentence(units,start,end,target['dialogue'])
        indices=_required_sentence_indices(units,list(range(start,end)),target['text']) if not is_phonetic else list(range(start,end))
        group=[units[n] for n in indices]
        evidence=text_coverage(target['text'],' '.join(g['text'] for g in group),is_phonetic)
        evidence.update(target=index+1,sourceStart=group[0]['speechStart'],sourceEnd=group[-1]['speechEnd'])
        if len(indices)<end-start or any(g.get('sentenceSplitEvidence') for g in group):
            evidence['sourceSentenceSpans']=[{'index':g['sourceSegments'][0]['index'],
                'start':g['speechStart'],'end':g['speechEnd'],'text':g['text'],
                **g.get('sentenceSplitEvidence',{})} for g in group]
        if evidence['status']!='complete':
            missing.append({'target':index+1,**target,'status':'partial','coverage':evidence['ratio'],
                            'missingText':evidence['missingText'],'evidence':copy.deepcopy(evidence),
                            'reason':'找到候选但未证实整句覆盖；缺词/读音差异请听审，未按脚本改写原声'})
        take_id=f'{group[0]["assetId"]}:{index+1}:{start}:{end}'
        for n,row in zip(indices,group):
            metadata=dict(scriptTarget=index+1,scriptTargetIds=[index+1],scriptScore=round(score,3),
                          scriptCoverage=[copy.deepcopy(evidence)],scriptStatus=evidence['status'],
                          scriptScene=target.get('scene',''),scriptScenes=[target['scene']] if target.get('scene') else [],
                          scriptSourceLine=target.get('sourceLine'),scriptSpeaker=target.get('speaker'),
                          dialogueProtected=target['dialogue'],
                          dialogueGapLimit=8 if target['dialogue'] else settings['dialogueGap'],
                          scriptMatchBasis=match_basis,requiredTakeId=take_id)
            if n not in used:
                selected_units[n]=dict(row,**metadata);selection.append(selected_units[n]);used.add(n)
            else:
                _merge_evidence(selected_units[n],metadata)
    if not selection:
        raise ValueError('脚本台词已整理，但没有在素材转写中找到可靠匹配。请确认剧本与素材属于同一条视频；本次未生成草稿，也未改动原素材。')
    # Non-verbal responses have no script sentence to match. Restore adjacent
    # complete short reactions before mode-dependent handle merging. Their
    # selection and provenance therefore remain identical across all modes.
    expanded=[]
    selected_text={asset:norm(''.join(row['text'] for row in selection if row['assetId']==asset))
                   for asset in {row['assetId'] for row in selection}}
    for row in selection:
        expanded.append(row)
        n=next((n for n,u in enumerate(units) if u['assetId']==row['assetId'] and
                u['sourceSegmentIndex']==row['sourceSegmentIndex']),None)
        if n is not None and n+1<len(units):
            start,end=_whole_source_sentence(units,n+1,n+2,True)
            group=units[start:end]
            # Anonymous speaker splitting is not a sentence boundary. Restore
            # the whole original sentence or none, never just its first word.
            if (start!=n+1 or any(i in used for i in range(start,end)) or
                    group[0]['assetId']!=row['assetId'] or group[0].get('blockedBefore') or
                    not 0<=group[0]['speechStart']-row['speechEnd']<=.8):
                continue
            basis=_reaction_recovery_basis(group,selected_text[row['assetId']])
            if basis:
                for i,unit in zip(range(start,end),group):
                    restored=copy.deepcopy(unit)
                    restored.update(protected=True,requiredTakeId=row['requiredTakeId'],
                                    reactionBasis=basis+'；围绕入选台词保护')
                    _merge_evidence(restored,row)
                    expanded.append(restored);used.add(i)
            elif any(u.get('audioProtectionEvidence') or u.get('voiceRole')=='friend' for u in group):
                row['reactionRecoveryReview']='相邻语音未证实为完整短反应，未凭粗保护窗口或重复台词自动加入；请听审原句：'+''.join(u['text'] for u in group)
    # A later reaction/merge pass cannot put an omitted take or instruction
    # back between two selected phrases, even when they share an ASR parent.
    stops={}
    for i,u in enumerate(units):
        if i not in used:stops.setdefault(u['assetId'],[]).append([u['speechStart'],u['speechEnd']])
    for row in expanded:
        row['scriptBoundaryStops']=list({tuple(s) for s in
            row.get('scriptBoundaryStops',[])+stops.get(row['assetId'],[])})
    selected=merge(expanded)
    _link_existing_quantity_reply(selected,missing)
    # Retain unmatched material in the audit/source library, not a hidden destructive delete.
    omitted=[dict(u,reason='未入选脚本初剪；原片保留，可在逐句审核中取回') for i,u in enumerate(units) if i not in used]
    partial=sum(t['status']=='partial' for t in missing)
    return selected,removed,reviews,missing,[f'按剧本顺序选片，每句只取一遍，忽略旧源时间码；{partial} 条仅部分覆盖、{len(missing)-partial} 条未找到，均须复核；{len(omitted)} 个非入选语音单元留在分析库，不能据此认定为编导。']


def review_report(plan,items,request,warnings):
    rows=[]
    for s in plan['selectedSpeech']:
        reasons=[]
        if s.get('voiceRole') in ('unknown','mixed','director'):reasons.append('声音身份或用途不确定，原声保留')
        if s.get('protected'):reasons.append('反应/笑声/互动保护，需确认切点')
        if s.get('scriptScore',1)<.75:reasons.append('脚本文字匹配较弱')
        if s.get('scriptStatus')=='partial':reasons.append('脚本台词仅部分覆盖，候选匹配不代表整句已覆盖')
        if s.get('visualClassReview'):reasons.append(s['visualClassReview'])
        if s.get('asrReview',{}):
            if s['asrReview']['decision']=='disagreement-kept-original':reasons.append('双引擎转写不一致，保留原文待听审')
        if s.get('reactionProtection'):reasons.append('多帧复核延长了动作/反应尾巴')
        if s.get('policyReview'):reasons.append(s['policyReview'])
        if s.get('scriptMatchBasis','').startswith('同源读音'):reasons.append(s['scriptMatchBasis'])
        if s.get('scriptSegmentationReview'):reasons.append(s['scriptSegmentationReview'])
        if s.get('reactionRecoveryReview'):reasons.append(s['reactionRecoveryReview'])
        if s.get('reactionEdgeProtection'):reasons.append('相邻句间空隙内已有短反应保护，切点保留至下一句之前')
        if reasons:rows.append({'assetId':s['assetId'],'source':s['source'],'sourceStart':s['start'],
                'sourceEnd':s['end'],'timelineStart':s['at'],'timelineEnd':s['at']+s['end']-s['start'],
                'text':s['text'],'reasons':reasons,'role':s.get('voiceRole','unknown')})
    return {'mode':'本地无人值守初剪，不是审片通过的成片','editPolicy':policy(request.get('editMode')),
            'profileId':(request.get('voiceProfile') or {}).get('id'),
            'overlapDetection':False,'targetSeconds':request.get('targetSeconds',180),'duration':plan['duration'],
            'review':rows,'removed':plan['removedSpeech'],'missingScript':plan.get('missingScript',[]),
            'warnings':warnings,'policy':'未知、短反应、笑声和混合对话不按编导删除。未启用人声分离。原片不变。',
            'analyzedVoiceFiles':sum('voiceGuard' in i and not i['voiceGuard'].get('degraded') for i in items)}
