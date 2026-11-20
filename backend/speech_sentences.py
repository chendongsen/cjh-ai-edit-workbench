"""Evidenced whole-sentence boundaries within one long ASR block.

Never align a script onto approximate word times. A split is usable only when
the complete ASR text and complete ordered word sequence agree, and every
sentence boundary is also a word boundary. This module never assigns speakers.
"""
import math
import re
from editorial import norm

REVISION = 'speech-sentences-1'
SCRIPT_REVISION = 'script-word-boundaries-1'


def script_spans(segment, phrases, normalize=norm):
    """Expose complete script phrases at real word edges, never invent timing.

    A raw ASR block is not necessarily one take: it can hold a countdown,
    several attempts and unrelated speech without punctuation. Only exact
    normalized full-phrase matches and an explicit leading countdown create
    additional edges. Approximate/phonetic matches cannot invent an edge.
    All text and time values continue to come from the recorded word sequence.
    """
    original, reason = sentence_spans(segment)
    words = segment.get('words') or []
    text = segment.get('text', '')
    start, end = segment.get('start'), segment.get('end')
    if (not words or not isinstance(text,str) or
            any(type(v) not in (int,float) or not math.isfinite(v) for v in (start,end))):
        return original, reason
    previous=start
    for word in words:
        if not isinstance(word,dict) or not isinstance(word.get('text'),str):return original,reason
        a,b=word.get('start'),word.get('end')
        if (any(type(v) not in (int,float) or not math.isfinite(v) for v in (a,b)) or
                not start-1e-6<=a<b<=end+1e-6 or a<previous-1e-6):return original,reason
        previous=b
    if norm(''.join(w['text'] for w in words))!=norm(text):return original,reason
    edges={0,len(words)}
    for span in original:edges.update((span['wordStartIndex'],span['wordEndIndex']))
    expected={normalize(p) for p in phrases if len(normalize(p))>=4}
    for a in range(len(words)):
        candidate=''
        for b in range(a,len(words)):
            candidate+=words[b]['text']
            value=normalize(candidate)
            if value in expected:edges.update((a,b+1))
            if len(value)>max(map(len,expected),default=0)+8:break
    # Preserve any countdown explicitly required by the script itself.
    if not any(re.search(r'(?:321|三二一)',normalize(p)) for p in phrases):
        prefix=''
        for i,w in enumerate(words):
            prefix+=norm(w['text'])
            if re.fullmatch(r'(?:好|来|对|ok)*(?:321|三二一)',prefix):
                edges.add(i+1);break
            if len(prefix)>10:break
    if len(edges)<=2:return original,reason
    pairs=list(zip(sorted(edges),sorted(edges)[1:]))
    if original and set(edges)=={i for s in original for i in (s['wordStartIndex'],s['wordEndIndex'])}:
        return original,reason
    result=[]
    for index,(a,b) in enumerate(pairs):
        selected=words[a:b]
        value=''.join(w['text'] for w in selected).strip()
        if not norm(value):continue
        result.append({'text':value,'start':selected[0]['start'],'end':selected[-1]['end'],
            'words':selected,'wordStartIndex':a,'wordEndIndex':b,'sentenceIndex':index,
            'revision':SCRIPT_REVISION})
    return result,None


def sentence_spans(segment):
    """Return (complete sentence spans, reason when a split cannot be proved)."""
    text = segment.get('text', '')
    if not isinstance(text, str):
        return [], '转写文字无效，未细分'
    # Do not split commas, decimal numbers or the interior of an English word.
    ends = [m.end() for m in re.finditer(r'[。！？!?；;]+[”」』\"]*|\.(?=\s+[A-Z]|\s*$)', text)]
    if not ends or ends[-1] != len(text):
        ends.append(len(text))
    pieces, begin = [], 0
    for end in ends:
        part = text[begin:end].strip()
        if norm(part):
            pieces.append(part)
        begin = end
    if len(pieces) < 2:
        return [], None
    words = segment.get('words')
    if not isinstance(words, list) or not words:
        return [], '完整句之间没有可靠词时间戳，保留原语音块待听审'
    start, end = segment.get('start'), segment.get('end')
    if any(type(v) not in (int, float) or not math.isfinite(v) for v in (start, end)) or end <= start:
        return [], '语音块时间无效，未细分'
    previous = start
    for word in words:
        if not isinstance(word, dict) or not isinstance(word.get('text'), str):
            return [], '词转写不完整，未细分'
        a, b = word.get('start'), word.get('end')
        if (any(type(v) not in (int, float) or not math.isfinite(v) for v in (a, b))
                or not start-1e-6 <= a < b <= end+1e-6 or a < previous-1e-6):
            return [], '词时间戳重叠、越界或无效，保留原语音块待听审'
        if word.get('probability') is not None and (type(word['probability']) not in (int, float)
                or not math.isfinite(word['probability']) or word['probability'] < .5):
            return [], '句内存在低置信度词，保留原语音块待听审'
        previous = b
    if norm(''.join(w['text'] for w in words)) != norm(text):
        return [], '词转写没有完整覆盖原文，保留原语音块待听审'
    offsets, count = {0: 0}, 0
    for index, word in enumerate(words, 1):
        count += len(norm(word['text']))
        offsets[count] = index
    spans, offset, first_word = [], 0, 0
    for index, piece in enumerate(pieces):
        offset += len(norm(piece))
        next_word = offsets.get(offset)
        if next_word is None or next_word <= first_word:
            return [], '句界落在词内部，保留原语音块待听审'
        selected = words[first_word:next_word]
        spans.append({'text':piece, 'start':start if index==0 else selected[0]['start'],
                      'end':end if index==len(pieces)-1 else selected[-1]['end'],
                      'words':selected, 'wordStartIndex':first_word, 'wordEndIndex':next_word,
                      'sentenceIndex':index, 'revision':REVISION})
        first_word = next_word
    return spans, None
