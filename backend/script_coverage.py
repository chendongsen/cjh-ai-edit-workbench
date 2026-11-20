"""Keep script omissions visible; an editable draft is not editorial approval."""
import copy
import math
import re
from difflib import SequenceMatcher
from decimal import Decimal
from pathlib import Path
from speech_sentences import sentence_spans, SCRIPT_REVISION
from editorial import norm

REVISION = 'script-coverage-5'


def _postproduction_obligations(requirement):
    """Recognize explicit editing instructions, not fast/slow real-world actions.

    Visual evidence cannot prove that an export applied these instructions. No
    postproduction execution receipt exists in the current coverage contract, so
    these remain pending even when the source picture has been verified.
    """
    text = requirement.get('text', '')
    if not isinstance(text, str):
        return []
    # Do not turn a person's quoted request into an instruction to the editor.
    # Keep offsets so the report can show the original clause without rewriting.
    probe = re.sub(r'“[^”]*”|「[^」]*」|『[^』]*』|"[^"\n]*"',
                   lambda match: ' ' * len(match[0]), text)
    patterns = {
        '加速': (r'(?:做|作|后期|剪辑(?:时)?|处理成)\s*(?:\d+(?:\.\d+)?倍)?加速(?=$|\s|[（(）)]|播放|处理|效果)|'
                 r'加速\s*(?:播放|处理|剪辑|效果)|\d+(?:\.\d+)?\s*倍速(?:播放)?'),
        '慢放': (r'(?<!慢)(?<!轻拿)慢放(?!下|手|进|入|到|开|置)|慢速播放|'
                 r'慢动作(?:回放|播放|处理|效果)|(?:做|后期|剪成|处理成)\s*慢动作'),
        '花字': (r'(?:叠加|加上|添加|打上|配上|补上|生成|弹出|制作|穿插|加|配|用)\s*'
                 r'[^，,。；;\n]{0,18}花字|(?:^|[（(:：])\s*(?:品牌|利润|价格)?花字'),
        '字幕': (r'(?:叠加|加上|添加|打上|配上|补上|生成|弹出|制作|加|打|配|用)\s*'
                 r'(?:[\u4e00-\u9fffA-Za-z0-9]{0,8})字幕|(?:^|[（(:：])\s*字幕(?=\s*(?:[：:]|$))|字幕(?:叠加|显示|出现|弹出)'),
        '定格': r'(?:做|后期|剪成|画面|镜头)\s*定格|定格(?:画面|效果|处理)',
        '倒放': r'倒放(?:画面|镜头|片段|视频|播放|处理)?',
    }
    obligations = []
    for clause in re.finditer(r'[^，,。；;\n]+', probe):
        effects = []
        for label, pattern in patterns.items():
            for match in re.finditer(pattern, clause[0]):
                before = clause[0][max(0, match.start()-8):match.start()]
                if re.search(r'(?:不(?:要|用|必|需(?:要)?|得|能)?|无须|无需|禁止|取消|去掉|避免)'
                             r'(?:再|额外|另行)?\s*$', before):
                    continue
                effects.append(label)
                break
        if effects:
            obligations.append({'text': text[clause.start():clause.end()].strip(),
                'kind': 'postproduction', 'status': 'needs-postproduction', 'effects': effects,
                'sourceLine': requirement.get('sourceLine'),
                'sourceColumn': requirement.get('sourceColumn'),
                'reason': '待完成／核验后期：' + '、'.join(effects) +
                    '。本次没有该要求的执行回执；找到画面不代表已执行后期处理。'})
    return obligations


def _with_postproduction_check(row, requirement):
    """Enrich a report copy without changing source text or visual evidence."""
    row = copy.deepcopy(row)
    obligations = _postproduction_obligations(requirement)
    if not obligations:
        return row
    pending = row.setdefault('nonVisualObligations', [])
    for obligation in obligations:
        if not any(isinstance(old, dict) and old.get('kind') == 'postproduction'
                   and old.get('text') == obligation['text'] for old in pending):
            pending.append(obligation)
    if row.get('status') in ('covered-main', 'inserted'):
        row['visualCoverage'] = {'status': row['status'], 'reason': row.get('reason', '')}
        row['status'] = 'partial'
    note = '尚有后期要求待完成／核验；已保留画面匹配证据，不能据此认定整项完成。'
    if note not in row.get('reason', ''):
        row['reason'] = (row.get('reason', '') + '；' + note).lstrip('；')
    return row


def _apply_manual_review(shots, manual_review):
    """Accept explicit report-only downgrades, never upgrades or editing data."""
    if manual_review is None:
        return shots
    if (not isinstance(manual_review, dict) or
            set(manual_review) != {'revision', 'reviews'} or
            manual_review.get('revision') != 'manual-shot-review-1' or
            not isinstance(manual_review.get('reviews'), list)):
        raise ValueError('补充复核文件格式无效，需要 manual-shot-review-1 和 reviews 数组。')
    lookup = {row.get('id'): row for row in shots if row.get('id')}
    checked, seen = [], set()
    required = {'requirementId', 'status', 'reason', 'source', 'reviewer'}
    for review in manual_review['reviews']:
        if (not isinstance(review, dict) or not required.issubset(review) or
                not set(review).issubset(required | {'reviewedAt'}) or
                any(not isinstance(value, str) or not value.strip() or len(value) > 4000
                    for value in review.values())):
            raise ValueError('每条补充复核需提供 requirementId、status、reason、source 和 reviewer；不得夹带剪辑修改。')
        identifier = review['requirementId']
        if identifier not in lookup:
            raise ValueError('补充复核引用未知镜头要求：' + identifier)
        if identifier in seen:
            raise ValueError('补充复核包含重复镜头要求：' + identifier)
        if review['status'] not in ('partial', 'uncertain'):
            raise ValueError('补充复核只允许降为 partial 或 uncertain，不允许升级或标记完成。')
        row = lookup[identifier]
        visual = row.get('visualCoverage') or {'status': row.get('status'), 'reason': row.get('reason', '')}
        if visual.get('status') not in ('covered-main', 'inserted'):
            raise ValueError('补充降级仅用于原本标记完成的镜头要求：' + identifier)
        checked.append((identifier, copy.deepcopy(review), copy.deepcopy(visual)))
        seen.add(identifier)
    result = copy.deepcopy(shots)
    output = {row.get('id'): row for row in result}
    for identifier, review, visual in checked:
        row = output[identifier]
        previous = (row.get('manualReview') or {}).get('previousCoverageStatus', row.get('status'))
        review.update(originalVisualStatus=visual['status'], previousCoverageStatus=previous)
        row['visualCoverage'] = visual
        row['manualReview'] = review
        row['status'] = review['status']
        row['reason'] = ('补充抽帧复核降级：' + review['reason'] + '；依据：' + review['source'] +
                         '（' + review['reviewer'] + '）。仅修正报告，未调整剪辑；原模型视觉结论和证据保留。')
    return result


def audit_script_conflicts(preparation):
    """Flag only explicit same-scene time/cost disagreements, never resolve them.

    A scene can legitimately mention several times or costs. If either side has
    multiple distinct values for a field, that is not enough evidence of a clash.
    """
    scenes = {}
    for kind, rows in (('dialogue', preparation.get('targets', [])),
                       ('shot', preparation.get('shotRequirements', []))):
        for index, row in enumerate(rows, 1):
            scene, text = row.get('scene'), row.get('text', '')
            if not scene or not isinstance(text, str):
                continue
            values = {'time': [f'{int(m[1]):02d}:{m[2]}' for m in re.finditer(
                r'(?<!\d)([01]?\d|2[0-3])[：:]([0-5]\d)(?!\d)', text)],
                'cost': [format(Decimal(m[1]).normalize(), 'f') for m in re.finditer(
                    r'成本\s*(?:[：:]|为|是|约)?\s*(\d+(?:\.\d+)?)', text)]}
            for field, found in values.items():
                for value in found:
                    slots = scenes.setdefault((scene, field), {'dialogue': {}, 'shot': {}})
                    slots[kind].setdefault(value, {'text': text, 'sourceLine': row.get('sourceLine'),
                        'value': value, **({'target': index} if kind == 'dialogue' else
                        {'id': row.get('id', f'shot-{index}'), 'sourceColumn': row.get('sourceColumn')})})
    conflicts = []
    for (scene, field), sides in scenes.items():
        if len(sides['dialogue']) != 1 or len(sides['shot']) != 1:
            continue
        spoken, visual = next(iter(sides['dialogue'].values())), next(iter(sides['shot'].values()))
        if spoken['value'] == visual['value']:
            continue
        conflicts.append({'id': f'conflict-{len(conflicts) + 1}', 'kind': field,
            'field': '时间' if field == 'time' else '成本', 'scene': scene, 'status': 'needs-review',
            'reason': '同场景台词与画面说明的明确数值不一致，请核对原脚本；未自动选值或改写。',
            'dialogue': spoken, 'shot': visual})
    return conflicts


def validate_dialogue_integrity(selection, fps=30):
    """A reaction/mode pass must never cut into a selected speech core."""
    for row in selection:
        a = row.get('speechStart', row['start'])
        b = row.get('speechEnd', row['end'])
        if row['start'] > a + 1e-6 or row['end'] < b - 1e-6:
            raise ValueError('完整台词切点校验失败，已停止编辑器写入：' +
                             Path(row.get('source', '')).name + ' ' + row.get('text', '')[:60])


def _valid_span(start, end):
    return (type(start) in (int,float) and type(end) in (int,float) and
            math.isfinite(start) and math.isfinite(end) and 0 <= start < end)


def _retained_interval(start, end, rows):
    """Require the complete interval, including syllables at either boundary."""
    cursor=start
    for row in rows:
        if not _valid_span(row.get('start'),row.get('end')):continue
        if row['end']<cursor:continue
        if row['start']>cursor+1e-6:return False
        cursor=max(cursor,row['end'])
        if cursor>=end-1e-6:return True
    return False


def _word_span(segment, row):
    """Reconstruct a script phrase from the original complete word evidence."""
    if row.get('revision') != SCRIPT_REVISION:return None
    words=segment.get('words') or []
    a,b=row.get('wordStartIndex'),row.get('wordEndIndex')
    if type(a) is not int or type(b) is not int or not 0<=a<b<=len(words):return None
    previous=segment['start']
    for word in words:
        if not isinstance(word,dict) or not isinstance(word.get('text'),str):return None
        if not _valid_span(word.get('start'),word.get('end')):return None
        if word['start']<previous-1e-6 or word['end']>segment['end']+1e-6:return None
        previous=word['end']
    if norm(''.join(w['text'] for w in words))!=norm(segment.get('text','')):return None
    return {'start':words[a]['start'],'end':words[b-1]['end'],
            'text':''.join(w['text'] for w in words[a:b])}


def _required_cores(record, item):
    """Use original refined ASR, never derive missing cores from selected rows.

    Missing/malformed or truncated ASR cannot prove that an omitted interval is
    silence. Such cases return None and retain the conservative envelope check.
    """
    if not item or item.get('source')!=record['source']:return None
    segments=item.get('segments')
    if not isinstance(segments,list) or not segments:return None
    if any(not isinstance(s,dict) or not _valid_span(s.get('start'),s.get('end')) for s in segments):return None
    segments=sorted(segments,key=lambda s:s['start'])
    start,end=record['sourceStart'],record['sourceEnd']
    requested=record.get('sourceSentenceSpans')
    if requested:
        proven=[]
        for row in requested:
            index=row.get('index')
            if type(index) is not int or not 0<=index<len(segments):return None
            segment=segments[index]
            spans,_=sentence_spans(segment)
            # Whole un-split original units are also valid, but partial intervals
            # must match a sentence independently reconstructed from word times.
            candidates=spans+[segment]
            word_span=_word_span(segment,row)
            if word_span:candidates.append(word_span)
            if not any(_valid_span(row.get('start'),row.get('end'))
                    and abs(s['start']-row['start'])<1e-6 and abs(s['end']-row['end'])<1e-6
                    and norm(s.get('text',''))==norm(row.get('text','')) for s in candidates):return None
            proven.append(row)
        if any(a['end']>b['start']+1e-6 for a,b in zip(proven,proven[1:])):return None
        if abs(proven[0]['start']-start)>1e-6 or abs(proven[-1]['end']-end)>1e-6:return None
        observed=norm(''.join(r['text'] for r in proven))
        expected=norm(record.get('expectedText',''))
        if not expected or observed!=norm(record.get('matchedText','')):return None
        if sum(b.size for b in SequenceMatcher(None,expected,observed,autojunk=False).get_matching_blocks())!=len(expected):return None
        return [(r['start'],r['end']) for r in proven]
    evidenced=[]
    for segment in segments:
        # Recompute from original complete word evidence. Do not trust a
        # selected row's claimed subrange to prove a whole sentence was kept.
        spans,_=sentence_spans(segment)
        evidenced.extend(spans or [segment])
    cores=sorted((s['start'],s['end']) for s in evidenced
                 if s['start']<end-1e-6 and s['end']>start+1e-6)
    # The evidence boundaries are speech boundaries, not handles. Requiring
    # original cores on both edges catches incomplete ASR lists and cut tails.
    if not cores or cores[0][0]>start+1e-6 or max(b for _,b in cores)<end-1e-6:return None
    return cores


def build_coverage(preparation, plan, shot_report=None, items=None, manual_review=None):
    """Check whole target evidence against final cuts and optional refined ASR.

    Pass the original refined ``items`` list to allow removal of silence between
    required utterances. Without it, a target's full source envelope is required.
    """
    selected = plan.get('selectedSpeech', [])
    source_items={i['assetId']:i for i in (items or []) if isinstance(i,dict) and 'assetId' in i}
    failures = {r['target']: r for r in plan.get('missingScript', [])}
    dialogue = []
    for index, target in enumerate(preparation.get('targets', []), 1):
        hits = [r for r in selected if index in r.get('scriptTargetIds', [r.get('scriptTarget')])]
        records = [dict(c,source=r['source'],assetId=r['assetId']) for r in hits
                   for c in r.get('scriptCoverage', []) if c.get('target') == index]
        failure = failures.get(index)
        # Old/cache-only hits without explicit whole-sentence evidence must not
        # become a false "complete" badge in a new coverage report.
        def fully_retained(record):
            if not _valid_span(record.get('sourceStart'),record.get('sourceEnd')):return False
            rows=sorted((h for h in hits if h['assetId']==record['assetId'] and h['source']==record['source']),
                        key=lambda h:h['start'])
            cores=_required_cores(record,source_items.get(record['assetId']))
            record['retentionBasis']='refined-asr-cores' if cores is not None else 'conservative-source-envelope'
            if cores is None:cores=[(record['sourceStart'],record['sourceEnd'])]
            record['requiredCores']=[{'start':a,'end':b} for a,b in cores]
            record['fullyRetained']=all(_retained_interval(a,b,rows) for a,b in cores)
            return record['fullyRetained']
        complete = any(c.get('status') == 'complete' and fully_retained(c) for c in records)
        status = 'missing' if not hits else ('complete' if complete and not failure else 'partial')
        reason = (failure or {}).get('reason') or (
            '已匹配整句候选；仍需听审实际措辞、角色和声音' if status == 'complete' else
            '只匹配到部分或近似内容，未按脚本补造台词' if hits else '没有可靠候选；不能据此判定原片未拍摄')
        dialogue.append(dict(target=index, text=target['text'], scene=target.get('scene', ''),
            speaker=target.get('speaker', ''), status=status, reason=reason,
            missingText=(failure or {}).get('missingText', []),
            evidence=records, clips=[{'assetId':r['assetId'], 'source':r['source'],
                'start':r['start'], 'end':r['end'], 'at':r.get('at', 0)} for r in hits]))
    shot_report = shot_report or {}
    shots = shot_report.get('requirements') or [dict(r, status='uncertain',
        reason='本次镜头检索未完成，未将未检查的要求标为已覆盖')
        for r in preparation.get('shotRequirements', [])]
    requirements = {r.get('id'): r for r in preparation.get('shotRequirements', []) if r.get('id')}
    shots = [_with_postproduction_check(r, requirements.get(r.get('id'), r)) for r in shots]
    shots = _apply_manual_review(shots, manual_review)
    unresolved = [r for r in shots if r.get('status') not in ('covered-main', 'inserted')]
    pending = [r for r in dialogue if r['status'] != 'complete']
    excluded = preparation.get('excludedContent', [])
    shot_summary = {'required':len(shots), 'coveredMain':sum(r.get('status')=='covered-main' for r in shots),
        'inserted':sum(r.get('status')=='inserted' for r in shots), 'unresolved':len(unresolved), 'items':shots}
    physical_inserts = (shot_report.get('summary') or {}).get('newInsertCount')
    if type(physical_inserts) is int and physical_inserts >= 0:
        shot_summary['physicalInserts'] = physical_inserts
    result = {'revision':REVISION, 'status':'draft-with-gaps' if pending or unresolved else 'draft-awaiting-review',
        'note':'已输出可编辑草稿，不代表审片通过。未匹配不等于原片缺失；模型确认的镜头仍需人工复核。',
        'dialogue':{'required':len(dialogue), 'complete':sum(r['status']=='complete' for r in dialogue),
            'partial':sum(r['status']=='partial' for r in dialogue),
            'missing':sum(r['status']=='missing' for r in dialogue), 'items':dialogue},
        'shots':shot_summary, 'scriptConflicts':audit_script_conflicts(preparation),
        'excludedContent':excluded, 'duration':plan.get('duration', 0)}
    if manual_review is not None:
        result['manualReview'] = copy.deepcopy(manual_review)
    return result


def coverage_text(report):
    d, s = report['dialogue'], report['shots']
    labels = {'complete':'整句候选', 'partial':'部分匹配', 'missing':'未匹配',
              'covered-main':'主线已覆盖候选', 'inserted':'已补充镜头候选',
              'unmatched':'未找到', 'uncertain':'无法确认', 'conflict':'未能安全放入'}
    lines = ['脚本内容覆盖与缺项清单', report['note'],
        f'台词 {d["required"]} 条：整句候选 {d["complete"]}，部分匹配 {d["partial"]}，未匹配 {d["missing"]}。',
        f'镜头要求 {s["required"]} 项：主线覆盖 {s["coveredMain"]}，已补充 {s["inserted"]}，待解决 {s["unresolved"]}。',
        f'本次草稿 {report["duration"]:.2f} 秒。统一按剧本选片；不硬截完整台词凑时长。']
    if 'physicalInserts' in s:
        lines.append(f'实际新增第二轨 {s["physicalInserts"]} 段（不等于完成要求 {s["physicalInserts"]} 项；部分匹配也可能已补入可用画面）。')
    lines += ['', '一、台词逐项核对']
    for row in d['items']:
        lines.append(f'{row["target"]}. [{labels[row["status"]]}] {row["text"]} —— {row["reason"]}')
        for c in row['clips']:
            lines.append(f'   {Path(c["source"]).name} 原片 {c["start"]:.2f}–{c["end"]:.2f}s，草稿 {c["at"]:.2f}s')
    lines += ['', '二、镜头逐项核对']
    for row in s['items']:
        lines.append(f'{row.get("id", "镜头")}. [{labels.get(row.get("status"), "待核对")}] '
                     f'{row.get("scene", "")} / {row.get("text", "")} —— {row.get("reason", "")}')
        manual = row.get('manualReview')
        if manual:
            lines.append('   补充抽帧复核意见（仅降级报告，不改剪辑）：' + manual['reason'])
            lines.append('      复核者：' + manual['reviewer'] + '；依据：' + manual['source'] +
                         ('；时间：' + manual['reviewedAt'] if manual.get('reviewedAt') else ''))
            original = row.get('visualCoverage') or {}
            lines.append('      原模型视觉结论（保留备查，不再计作整项完成）：[' +
                         labels.get(original.get('status'), '待核对') + '] ' + original.get('reason', ''))
        components = row.get('componentCoverage') or []
        lookup = {c.get('id'): c for c in components if isinstance(c, dict)}
        for component in components:
            state = labels.get(component.get('status'), '待核对')
            lines.append(f'   画面要素 [{state}] {component.get("text", "")} —— {component.get("reason", "")}')
            placement = component.get('placement') or {}
            if placement:
                track = '第二轨' if placement.get('track') == 2 else '主线' if placement.get('track') == 1 else '已用片段'
                existing = '（已有片段，未新增）' if placement.get('existing') else ''
                at = placement.get('at')
                position = f'，草稿 {at:.2f}s' if type(at) in (int, float) and math.isfinite(at) else ''
                lines.append(f'      {track}{existing}{position}')
        remaining = row.get('remainingComponents') or []
        if remaining:
            lines.append('   仍缺内容（该要求未完整完成）：')
            for value in remaining:
                item = value if isinstance(value, dict) else lookup.get(value, {'text': str(value)})
                lines.append(f'      {item.get("text", "")} —— {item.get("reason", "待复核")}')
        for obligation in row.get('nonVisualObligations') or []:
            text = obligation.get('text', '') if isinstance(obligation, dict) else str(obligation)
            lines.append(f'   非视觉义务（需听审／后期核对，不以画面代替完成）：{text}')
            if isinstance(obligation, dict) and obligation.get('reason'):
                lines.append('      ' + obligation['reason'])
        if row.get('placements'):
            new = sum(not p.get('existing') for p in row['placements'])
            lines.append(f'   本项实际使用第二轨 {len(row["placements"])} 段，其中新增 {new} 段；不据此认定整项完成。')
    if report.get('scriptConflicts'):
        lines += ['', '三、脚本数值冲突（只提示，不自动选择）']
        for conflict in report['scriptConflicts']:
            lines.append(f'{conflict["scene"]} / {conflict["field"]} —— {conflict["reason"]}')
            lines.append(f'   台词原句：{conflict["dialogue"]["text"]}（数值 {conflict["dialogue"]["value"]}）')
            lines.append(f'   画面原句：{conflict["shot"]["text"]}（数值 {conflict["shot"]["value"]}）')
    if report['excludedContent']:
        lines += ['', '四、非同期声内容（配音、花字等，未假装在原片找到）']
        for row in report['excludedContent']:
            lines.append(str(row.get('text', row)) if isinstance(row, dict) else str(row))
    return '\n'.join(lines) + '\n'
