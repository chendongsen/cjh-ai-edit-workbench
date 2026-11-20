"""One local, resumable pipeline. No prewritten editorial plan or cloud inference."""
import json
import math
import re
import subprocess
import sys
import telemetry
import editor_startup
from pathlib import Path
from common import ROOT, RESOLVE, read, write
from analyze import run as analyze, probe
from planner import build
import jianying
from editorial import REVISION, clean_speech, main_candidate, food_candidate, dish_tags, norm, word_map, DISHES, explicit_broll_folder
from food_index import refine_food_index


def natural(value):
    return [int(p) if p.isdigit() else p.casefold() for p in re.split(r'(\d+)', value)]


def required_main_ranges(shot_report):
    """Keep already verified script actions visible when adding optional B-roll."""
    ranges = []
    for requirement in shot_report.get('requirements', []):
        for row in requirement.get('componentCoverage') or [requirement]:
            placement = row.get('placement', {})
            if row.get('status') != 'covered-main' or placement.get('track') != 1:
                continue
            start = float(placement['at'])
            end = start + float(placement['end']) - float(placement['start'])
            if not all(math.isfinite(x) for x in (start, end)) or start < 0 or end <= start:
                raise ValueError('脚本主画面保护区间无效，停止补画面以免遮盖必要动作')
            ranges.append((start, end))
    return sorted(set(ranges))


def supporting_pictures(plan, items, preparation, request, root, phase):
    """An explicit script picture list governs V2; do not fill its gaps by topic."""
    report = {}
    has_requirements = bool(request.get('unattended') and preparation.get('shotRequirements'))
    if has_requirements:
        from script_shots import apply_script_shots
        import video_retrieval
        semantic_enabled=video_retrieval.enabled(request)
        phase('broll', '正在逐项检索脚本要求的画面；先核对主线，再多帧验证补充镜头')
        # Old filename-only food placements have not passed the required-shot
        # visual gate. Required pictures are planned first and never overwritten.
        plan['foodPlacements'] = []
        plan, report = apply_script_shots(plan, items, preparation, root,
            request.get('visionModel'), int(request.get('fps', 30)),
            lambda message: phase('broll', message),
            config={'videoRetrieval':semantic_enabled,'maxCandidates':6 if semantic_enabled else 3})
        # Broad paragraph retrieval previously inserted merely related pictures
        # after every explicit requirement had failed. Keep the script's list
        # authoritative, including an honest unresolved result.
        shots=plan.get('foodPlacements',[])
        plan['usedFoodSources']=len({s['assetId'] for s in shots})
        seconds=sum(s['end']-s['start'] for s in shots)
        plan['brollSummary']={'count':len(shots),'insertCount':len(shots),'scriptCount':len(shots),
            'foodCount':0,'environmentCount':0,'seconds':round(seconds,3),
            'coverage':round(seconds/plan['duration'],4) if plan.get('duration') else 0,
            'mainAndAudioUnchanged':True,
            'optionalReviewComplete':True,'warnings':[],
            'selectionBasis':'explicit-script-requirements',
            'note':'补画面按剧本要求逐项匹配；未匹配项留在缺项清单，不用泛场景候选代替。'}
    elif request.get('visionModel'):
        from broll import enhance
        phase('broll', '正在补充食物、制作、上菜和环境画面；保留已匹配的脚本镜头与人物反应')
        plan = enhance(plan, items, request, root, preserve_existing=has_requirements,
                       protected_main_ranges=required_main_ranges(report))
    return plan, report


def automatic_plan(items, name, fps=30, selection=None):
    """Conservative, traceable command removal and same-location, time-aligned dish inserts."""
    speech, food, skipped, removed, reviews, cursor = [], [], [], [], [], 0
    durations = {}
    def usable(item):
        key = item['source']
        if key not in durations:
            durations[key] = float(item['usableDuration']) if 'usableDuration' in item else probe(key)['usableDuration']
        return min(float(item['duration']), durations[key])
    candidates = [i for i in items if food_candidate(i)]
    used = {}
    recovered = []
    by_id={i['assetId']:i for i in items}
    sequence=[(by_id[s['assetId']],[s]) for s in selection] if selection is not None else [(i,None) for i in sorted(items,key=lambda i:natural(i['source']))]
    insert_counts={}
    for item, prepared in sequence:
        # A coarse image class must not discard dialogue already recovered by
        # script retrieval. Keep explicit B-roll exclusions and provenance: this
        # is an auditable candidate, not a new speaker/face identity decision.
        script_recovered = bool(prepared and item.get('video') and item.get('segments')
            and not explicit_broll_folder(item)
            and all(s.get('scriptVisualReview') and s.get('scriptTargetIds') for s in prepared))
        if not main_candidate(item) and not script_recovered:
            skipped.append({'source': item['source'], 'reason': '非人物口播候选或明确空镜文件夹；原分析保留'})
            continue
        if item.get('vision', {}).get('kind') != '人物':
            recovered.append(item['source'])
        if prepared is None:
            merged, cuts, checks = clean_speech(item, usable(item), fps)
        else:
            merged,cuts=prepared,[]
            checks=[{'start':a,'end':b} for a,b,_ in item.get('voiceGuard',{}).get('protected',[])]
            checks += [{'start':s['start'],'end':s['end']} for s in prepared if s.get('voiceRole') in ('mixed','friend')]
        removed.extend(cuts)
        reviews.extend(checks)
        source_inserts = insert_counts.get(item['assetId'],0)
        for s in merged:
            duration = math.ceil((s['end'] - s['start']) * fps - 1e-8) / fps
            s['at'] = cursor
            speech.append(s)
            words, mapping = word_map(s)
            # Prefer actual dish words. Filename fallback is explicitly not word-level recognition.
            events = []
            for dish in sorted(dish_tags(s['text'])):
                aliases = [norm(a) for a in DISHES[dish] if norm(a) in words]
                if aliases and mapping:
                    offset = min(words.index(a) for a in aliases)
                    at = max(.6, mapping[offset]['start'] - s['start'])
                    events.append((at, dish, '台词菜名 + 词级时间'))
                else:
                    events.append((.6, dish, '台词菜名；缺词级定位'))
            title_tags = dish_tags(Path(item['source']).stem)
            topic_basis = '同场素材文件名主题（非台词精确匹配）'
            if not title_tags and '榴莲' in norm(Path(item['source']).stem):
                scene_tags = {tag for c in candidates if Path(c['source']).is_relative_to(Path(item['source']).parent)
                              for shot in c.get('foodShots', []) for tag in shot['tags'] if '榴莲' in tag}
                if len(scene_tags) == 1:
                    title_tags = scene_tags
                    topic_basis = '同场唯一已标注的榴莲菜品（主题候选，需审片）'
            # Homophones are candidates only when constrained by this source's explicit dish name.
            homophones = {'虾饺': ['笑叫', '下脚', '虾酱'], '菠萝包': ['玻璃包'],
                          '脑花': ['马花'], '鲍鱼': ['包油', '八佑']}
            for dish in sorted(title_tags):
                for alias in homophones.get(dish, []):
                    if alias in words and mapping:
                        events.append((max(.6, mapping[words.index(alias)]['start']-s['start']), dish,
                                       '素材菜名约束的同音词候选 + 词级时间（需听审）'))
                        break
            if not events and re.search(r'吃|味道|香|脆|嫩|虾|饺|鱼|肠|饭|包|榴|酥|奶|广东|厨师|食材|好看|好满|咸', norm(s['text'])):
                events = [(.8, dish, topic_basis) for dish in sorted(title_tags)]
            # Long description may use two inserts, but faces/reactions retain most of the screen time.
            if duration >= 16 and events:
                events.append((max(9, events[0][0] + 9), events[0][1], events[0][2]))
            options = []
            for candidate in candidates:
                if not Path(candidate['source']).is_relative_to(Path(item['source']).parent):
                    continue
                if candidate['assetId'] == item['assetId']:
                    continue
                shots = candidate.get('foodShots')
                if shots is None:
                    # Compatibility for older analyses without frames; no invented visual verification.
                    tags = dish_tags(' '.join(candidate.get('vision', {}).get('dishCandidates', [])[:1])) | dish_tags(Path(candidate['source']).stem)
                    shots = [{'at': usable(candidate) * .65, 'tags': sorted(tags), 'confidence': .65, 'basis': '旧视觉候选；未逐帧复核'}] if tags else []
                for shot in shots:
                    options.append((candidate, shot))
            local_end, coverage = -10, 0
            for offset, dish, basis in sorted(events):
                if duration < 3 or source_inserts >= 4:
                    break
                length = round(min(2.5, duration * .35 - coverage) * fps) / fps
                if length < 1:
                    break
                if basis.startswith('同场'):
                    # Do not cover uncertain dialogue or a spontaneous question with a title guess.
                    if any(r['start'] < s['end'] and r['end'] > s['start'] for r in checks) or re.search(r'吗|嗎|谁|誰|什么|什麼|怎么|怎麼|\?', s['text']):
                        continue
                    if '唯一' in basis and not re.search(r'榴|刘烈|多连|味道|好吃|传统|典型', norm(s['text'])):
                        continue
                at = round(min(max(.6, offset), duration - length - .2) * fps) / fps
                if prepared is not None and s['start']+at+length>s.get('speechEnd',s['end']):
                    continue  # Never cover the post-speech expression handle with food.
                if at < local_end + 4:
                    continue
                if any(r['start'] < s['start'] + at + length and r['end'] > s['start'] + at for r in checks):
                    continue
                ranked = sorted(options, key=lambda x: (len(used.get(x[0]['assetId'], [])), len(x[1]['tags']), -x[1]['confidence'], -x[1]['at']))
                for candidate, shot in ranked:
                    if dish not in shot['tags']:
                        continue
                    occupied = used.get(candidate['assetId'], [])
                    if len(occupied) >= 3:
                        continue
                    maximum = int(usable(candidate) * fps) / fps
                    # Never back up from a visible sample into the preceding empty-table interval.
                    # A changing/occluded next sample is insufficient evidence for a safe window.
                    if 'nextAt' in shot:
                        if dish not in shot['nextTags']:
                            continue
                        maximum = min(maximum, int(shot['nextAt'] * fps) / fps)
                    start = math.ceil(shot['at'] * fps) / fps
                    end = min(round((start + length) * fps) / fps, maximum)
                    actual_length = round((end - start) * fps) / fps
                    if actual_length < 1:
                        continue
                    if end > maximum or any(start < b and end > a for a, b in occupied):
                        continue
                    food.append({'assetId': candidate['assetId'], 'start': start, 'end': end,
                                 'at': round((cursor + at) * fps) / fps, 'dish': dish, 'quote': s['text'],
                                 'mainAssetId': item['assetId'], 'mainSourceStart': s['start'] + at,
                                 'confirmed': False, 'matchBasis': basis + ' / ' + shot.get('basis', '本地逐帧视觉'),
                                 'evidenceFrame': shot.get('frame'), 'confidence': shot['confidence']})
                    used.setdefault(candidate['assetId'], []).append((start, end))
                    source_inserts += 1
                    insert_counts[item['assetId']]=source_inserts
                    local_end, coverage = at + actual_length, coverage + actual_length
                    break
            cursor = round((cursor + duration) * fps) / fps
    if not speech:
        raise ValueError('没有找到同时具备人物画面与台词的素材。请在高级工作台人工确认主线；未创建空剪辑。')
    if selection is not None and len(speech) != len(selection):
        raise ValueError('主线组装遗漏了已选台词，已停止编辑器写入；请检查画面分类与素材用途，不能静默丢句。')
    return {'project': name, 'revision': REVISION, 'selectedSpeech': speech, 'foodPlacements': food, 'packaging': [],
            'removedSpeech': removed, 'reviewSpeech': reviews, 'recoveredMainSources': recovered,
            'foodCandidateCount': len(candidates), 'usedFoodSources': len(used),
            'skipped': skipped, 'duration': cursor,
            'notes': '按文件顺序整理口播，依据转写和词级时间剔除明确倒数/拍摄指导，不是声纹身份识别；混合说话人、示范台词、重拍和不确定语句仍需听审。食物限同场，优先菜名词级对齐，文件名主题匹配明确标注；逐帧模型仍可能误判，短插入保留人物反应，食材品种与插入边界需审片。原素材未改动，综艺包装留在剪映完成。'}


@telemetry.workflow
def run(source):
    request = read(source)
    root = source.parent
    if not request.get('projectNaming'):
        from project_naming import make_project_naming
        request['projectNaming']=make_project_naming(request.get('assets',[]),root.name,
            read(root/'status.json',{}).get('created',source.stat().st_mtime))
        write(source,request)
    from edit_policy import policy
    settings=policy(request.get('editMode'))
    write(root/'edit-policy.json',settings)
    from script_normalize import normalize_script
    preparation=normalize_script(request.get('editingScript') or request.get('script',''))
    request['scriptPreparation']=preparation
    write(root/'script-preparation.json',preparation)
    if request.get('unattended'):
        supplied_script = request.get('editingScript') or request.get('script', '')
        if supplied_script.strip() and not preparation['strictReady']:
            message='已导入脚本，但没有找到明确台词。请检查是否选中了正确脚本；本次已停止，不会把全部口播当成按脚本粗剪。'
            write(root/'startup-failure.json',{'needsUserAction':True,'error':message})
            raise ValueError(message)
        if not preparation['strictReady']:
            message='请把含人物台词的剧本与素材一起导入。系统会自动整理；没有剧本台词时不会生成全量口播。'
            write(root/'startup-failure.json',{'needsUserAction':True,'error':message})
            raise ValueError(message)
    for asset in request['assets']:
        stat = Path(asset['path']).stat()
        if stat.st_size != asset['size'] or int(stat.st_mtime * 1000) != asset['modified']:
            raise ValueError('素材登记后发生变化，请重新读取素材并新建任务：' + asset['name'])
    def phase(stage, message):
        telemetry.phase(stage)
        write(root / 'progress.json', {'stage': stage, 'message': message})
        print(message, flush=True)
    def command(args):
        subprocess.run(args, check=True)
    if request.get('autoLaunchEditors'):
        phase('startup', '正在自动准备达芬奇与剪映环境；应用启动成功后才进入分析')
        write(root/'startup-failure.json',{})
        try:
            editor_startup.prepare(lambda message: phase('startup',message))
            import model_startup
            request['visionModel'] = model_startup.ensure(request.get('visionModel'), lambda message: phase('startup',message))
            write(root/'runtime-models.json', {'visionModel': request['visionModel'], 'model': request.get('model', 'small')})
        except Exception as error:
            write(root/'startup-failure.json',{'needsUserAction':True,'error':str(error)})
            raise
    analysis_dir = root / 'analysis'
    analysis_dir.mkdir(exist_ok=True)
    phase('analysis', '正在本机分析素材：抽帧、画面分类与原声转写')
    analyze(dict(request, transcribe=True, asrEngine='whisper', resume=True), analysis_dir)
    items = read(analysis_dir / 'result.json')['items']
    bad = [i for i in items if i.get('error') or any('失败' in w for w in i.get('warnings', []))]
    if bad:
        raise ValueError(f'{len(bad)} 个素材分析未完成，已停止下游写入。可点击继续重试，只重做失败项。')
    fps = int(request.get('fps', 30))
    night = request.get('unattended') is True
    night_warnings=list(preparation['warnings']) if night else []
    speech_report={};boundary_report={};alignment_report={};retrieval_report={}
    if night:
        from voice_guard import analyze_voice, fallback_guard
        phase('voices','正在复用已确认声纹，检测语音分段与笑声候选；无需逐批确认')
        try:
            items=analyze_voice(items,request,root)
        except Exception as e:
            night_warnings.append('声音保护模型未完成，已退回不按声音删除的保守模式：'+str(e))
            items=fallback_guard(items)
        if not request.get('voiceProfile'):
            night_warnings.append('没有适用于本批的已确认声音；未知声音保留，任务不等待身份确认。')
        from speech_refine import refine_speech
        phase('voices','正在二次转写纠错与匿名说话人分段；短反应和未知身份不按编导删除')
        try:
            items,speech_report=refine_speech(items,request,root)
        except Exception as e:
            night_warnings.append('转写纠错/匿名分段未完成，使用原转写并保留原声：'+str(e))
        import precise_alignment
        if precise_alignment.enabled(request):
            phase('voices','正在用本机新语音模型复核疑难台词，并定位实际说出的字词；不把脚本硬套到原声')
            try:
                items,alignment_report=precise_alignment.run(items,request,root,
                    phase=lambda message: phase('voices',message))
                if alignment_report.get('status')=='failed':
                    raise ValueError('本轮没有完成有效台词对齐，请查看精确对齐报告后重试')
            except Exception as error:
                write(root/'startup-failure.json',{'needsUserAction':True,
                    'error':'台词精确对齐未完成，已暂停生成草稿：'+str(error)})
                raise
            night_warnings.extend(alignment_report.get('warnings',[]))
    phase('analysis', '正在逐帧复核食物与可见时间，并筛除明确拍摄指导语')
    try:
        items = refine_food_index(items, request.get('visionModel'), root)
    except Exception as e:
        if not night:raise
        night_warnings.append('食物逐帧复核未完成，本次不插入未验证食物：'+str(e))
        for item in items:item['foodShots']=[]
    if night:
        from night_edit import select,review_report
        try:
            selection,cuts,reviews,missing,notes=select(items,request.get('editingScript') or request.get('script',''),fps,request.get('editMode'))
        except ValueError as error:
            write(root/'startup-failure.json',{'needsUserAction':True,'error':str(error)})
            raise
        write(root/'selection-before-boundary.json',{'selectedSpeech':selection,
            'missingScript':missing,'removedSpeech':cuts,'editMode':settings['mode'],
            'notes':notes,'stage':'script-selection-before-visual-handles'})
        from reaction_review import review_boundaries
        phase('boundaries','按剧本选段完成；正在复核切点附近的表情和动作')
        selection,boundary_report=review_boundaries(selection,items,request,root)
        from script_coverage import validate_dialogue_integrity
        validate_dialogue_integrity(selection,fps)
        night_warnings.extend(boundary_report['warnings'])
        if boundary_report.get('policyLimitedClips'):
            night_warnings.append(f'{boundary_report["policyLimitedClips"]} 段按“{settings["label"]}”限制了额外留量，请优先检查这些反应切点。')
        if selection and not boundary_report['reviewedClips']:
            raise ValueError('所有切点多帧复核均未完成，已停止编辑器写入；修复本地视觉模型后可继续。')
        plan=automatic_plan(items,request.get('name','夜间初剪'),fps,selection)
        plan.update(removedSpeech=cuts,reviewSpeech=reviews,missingScript=missing,revision='night-3',editPolicy=settings,
            notes='本地双引擎转写交叉复核＋CAM++ 匿名分段＋切点多帧复核；未知/混合不按编导删除，未做重叠人声分离。'+''.join(notes))
        if plan['duration']>request.get('targetSeconds',180):
            night_warnings.append('初剪超过目标时长；未为凑时长强切完整台词和反应，请早晨复核。')
        if missing:night_warnings.append(f'{len(missing)} 条脚本台词未完整匹配（含部分匹配），详情见脚本缺项清单。')
        report=review_report(plan,items,request,night_warnings)
        report['scriptPreparation']=preparation
        report['speechRefinement']=speech_report
        report['preciseAlignment']=alignment_report
        report['boundaryReview']=boundary_report
        write(root/'review-report.json',report)
    else:
        plan = automatic_plan(items, request.get('name', '自动粗剪'), fps)
    import video_retrieval
    if video_retrieval.enabled(request):
        phase('broll','正在建立本机视频片段索引，按完整台词和脚本画面要求检索；候选仍需视觉复核')
        try:
            retrieval_report=video_retrieval.prepare(items,preparation,request,root,
                log=lambda message: phase('broll',message),plan=plan)
        except Exception as error:
            write(root/'startup-failure.json',{'needsUserAction':True,
                'error':'视频检索未完成，已停止草稿写入：'+str(error)})
            raise
        if night:night_warnings.extend(retrieval_report.get('warnings',[]))
    plan['preciseAlignment']=alignment_report
    plan['videoRetrieval']=retrieval_report
    plan,shot_report = supporting_pictures(plan,items,preparation,request,root,phase)
    supplement_warnings = plan.get('brollSummary', {}).get('warnings', [])
    if night:night_warnings.extend(supplement_warnings)
    elif supplement_warnings:plan['notes'] += '；'.join(supplement_warnings)
    if request.get('visionModel') and not plan.get('foodPlacements'):
        message='本次没有插入第二轨补充画面。请查看脚本缺项清单和补画面复核记录；未匹配到不代表素材中没有。'
        if night:night_warnings.append(message)
        else:plan['notes'] += message
    if night:
        from script_coverage import build_coverage,coverage_text
        coverage=build_coverage(preparation,plan,shot_report,items=items)
        plan['scriptCoverage']=coverage
        if coverage['shots']['unresolved']:
            night_warnings.append(f'{coverage["shots"]["unresolved"]} 项脚本镜头未解决；未找到、无法确认和无安全插入位置分别列出，不强配画面。')
        report.update(scriptCoverage=coverage,scriptShots=shot_report,warnings=night_warnings,
                      preciseAlignment=alignment_report,videoRetrieval=retrieval_report)
        write(root/'review-report.json',report)
        write(root/'script-coverage.json',coverage)
        (root/'脚本覆盖与缺项清单.txt').write_text(coverage_text(coverage),encoding='utf-8')
    write(root / 'auto-plan.json', plan)
    resolve_dir = root / 'resolve'
    resolve_dir.mkdir(exist_ok=True)
    phase('resolve', '分析完成；正在创建独立达芬奇粗剪项目')
    result = read(resolve_dir / 'result.json', {})
    if not result:
        if (resolve_dir / 'started.json').exists():
            raise ValueError('上次达芬奇写入未取得完成回执，可能已创建项目。请先在高级工作台检查，不能盲目重试造成重复项目。')
        if request.get('autoLaunchEditors'):
            # Analysis can take hours: reconnect before entering the write boundary.
            try:
                editor_startup.ensure_resolve(lambda message: phase('resolve',message))
            except Exception as error:
                write(root/'startup-failure.json',{'needsUserAction':True,'error':str(error)})
                raise
        assets = {a['id']: a for a in request['assets']}
        data = build({'plan': plan, 'fps': fps, 'vertical': request.get('vertical', True), 'automaticDraft': True,
                      'projectNaming':request['projectNaming']}, assets, resolve_dir, root.name)
        if night:
            data['markers'] += [{'at':r['timelineStart'],'duration':max(1/fps,r['timelineEnd']-r['timelineStart']),
                'type':'早晨复核','text':'；'.join(r['reasons'])+'：'+r['text'][:80]} for r in report['review']]
        write(resolve_dir / 'resolve-input.json', data)
        write(resolve_dir / 'started.json', {'started': True})
        command([RESOLVE, str(ROOT / 'backend/resolve_bridge.py'), str(resolve_dir / 'resolve-input.json')])
        result = read(resolve_dir / 'result.json')
    jy_dir = root / 'jianying'
    jy_dir.mkdir(exist_ok=True)
    phase('jianying', '达芬奇粗剪已保存；正在转换多轨剪映草稿')
    draft = read(jy_dir / 'result.json', {})
    if not draft:
        if (jy_dir/'started.json').exists():
            raise ValueError('上次剪映导出结果不明，请检查草稿；不会自动重复创建。')
        write(jy_dir / 'input.json', {'source': {k: result[k] for k in ('projectId', 'timelineId')},
            'name':request.get('name', '自动粗剪'),
            'autoDraftName':result.get('project',request['projectNaming']['projectName'])+'_剪映', 'openAfter': True})
        write(jy_dir/'started.json',{'started':True})
        jianying.run(jy_dir / 'input.json')
        draft = read(jy_dir / 'result.json')
    if not draft.get('opened'):
        draft.update(jianying.launch_app(draft.get('draftPath')))
        write(jy_dir / 'result.json', draft)
    write(root / 'result.json', {'analysisCount': len(items), 'speechClips': len(plan['selectedSpeech']),
          'reusedAnalysisFrom': request.get('reuseAnalysisJobId'),
          'foodClips': plan.get('brollSummary', {}).get('foodCount', len(plan['foodPlacements'])), 'duration': plan['duration'], 'resolve': result, 'draft': draft,
          'editorialRevision': plan['revision'], 'removedCommands': len(plan['removedSpeech']),
          'removedCommandSeconds': round(sum(x['end']-x['start'] for x in plan['removedSpeech']), 2),
          'reviewSpeechCount': len(plan['reviewSpeech']), 'recoveredMainSources': len(plan['recoveredMainSources']),
          'foodCandidateCount': plan['foodCandidateCount'], 'usedFoodSources': plan['usedFoodSources'],
          'brollSummary': plan.get('brollSummary'),
          'reviewedFoodAssets': sum(bool(i.get('foodReview')) for i in items),
          'unattended': night, 'voiceProfileId':(request.get('voiceProfile') or {}).get('id'),
          'editMode':settings['mode'], 'editPolicy':settings,
          'scriptPreparationRevision':preparation['revision'], 'scriptDialogueCount':preparation['count'],
          'scriptCoverage':{k:v for k,v in coverage.items() if k not in ('dialogue','shots','excludedContent')} | {
              'dialogue':{k:v for k,v in coverage['dialogue'].items() if k!='items'},
              'shots':{k:v for k,v in coverage['shots'].items() if k!='items'}} if night else None,
          'scriptShotCount':len(preparation.get('shotRequirements',[])),
          'scriptShotInserted':coverage['shots']['inserted'] if night else 0,
          'scriptShotUnresolved':coverage['shots']['unresolved'] if night else 0,
          'brollClips':len(plan.get('foodPlacements',[])),
          'policyLimitedClips':boundary_report.get('policyLimitedClips',0),
          'morningReviewCount':len(report['review']) if night else 0,
          'missingScriptCount':len(plan.get('missingScript',[])),
          'asrCorrections':len(speech_report.get('corrections',[])),
          'asrDisagreements':len(speech_report.get('disagreements',[])),
          'asrTimingRepairs':len(speech_report.get('realignments',[])),
          'speakerTurns':sum(f['turns'] for f in speech_report.get('files',[])),
          'boundaryReviewedClips':boundary_report.get('reviewedClips',0),
          'reactionExtendedClips':boundary_report.get('extendedClips',0),
          'boundaryFrames':boundary_report.get('frameCount',0),
          'preciseAlignment':{k:v for k,v in alignment_report.items() if k not in ('rows','skipped')},
          'videoRetrieval':{k:v for k,v in retrieval_report.items() if k not in ('windows','queries')},
          'warnings': [plan['notes'], *night_warnings, *result.get('warnings', []), *draft.get('warnings', [])]})
    done_message='多轨草稿已保存，已核对剪映编辑页，可继续精剪。' if draft.get('draftOpened') else draft.get('note','多轨草稿已保存，请在剪映首页打开本次草稿。')
    if night and coverage['status']=='draft-with-gaps':
        done_message+=' 尚有脚本缺项或部分匹配，请先看缺项清单；未标为审片通过。'
    if plan.get('brollSummary', {}).get('optionalReviewComplete') is False:
        done_message+=' 部分补画面复核失败，已保留通过验证的镜头；请查看补画面记录。'
    phase('done',done_message)


if __name__ == '__main__':
    try:
        run(Path(sys.argv[1]))
    except Exception as e:
        print(str(e), file=sys.stderr, flush=True)
        sys.exit(1)
