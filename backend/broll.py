"""Paragraph-aware V2 planning; never edits V1/audio or infers speaker identity.

Source labels propose shots, not prove them. Every new placement requires local
in/middle/out visual checks of both the insert and the covered performance.
"""
import base64
import copy
import hashlib
import json
import math
import re
import subprocess
import time
import urllib.request
from pathlib import Path

import imageio_ffmpeg
from common import DATA, OLLAMA_URL, read, write, local_urlopen
from editorial import dish_tags, food_candidate, norm, word_map

REVISION = 'paragraph-broll-2'
MIN_SHOT = 1.2
TOPICS = {
    '后厨制作': (r'后厨|制作|现做|现烤|现切|现炒|现蒸|炭火|烤制|烤串|烤肉|翻面|刷酱|撒料|备菜',
               r'后厨|厨房|厨师|制作|烹饪|烧烤|烤架|烤炉|炭火|烤串|烤肉|切菜|切肉|翻炒|煎|蒸笼|刷酱|撒料'),
    '上菜过程': (r'上菜|上桌|端上|端来|端过来|端菜|出餐|出炉|刚上|摆盘',
               r'上菜|上桌|端盘|端菜|端着|托盘|出餐|出炉|摆盘|服务员.*盘'),
    '城市景观': (r'俯瞰|视角|广州塔|窗外|城市景观', r'城市|天际线|广州塔|珠江|窗外'),
    '餐厅环境': (r'餐厅|环境|早餐|早茶|示例餐厅', r'餐厅|用餐区|餐桌|茶室'),
    '菜单': (r'菜单|点菜', r'菜单'),
    '购物场景': (r'免税店|买大牌|购物', r'免税|商店|柜台|商品'),
    '日常穿着': (r'穿衣|衣服|西装|短裤|短袖', r'行人|男子|女子|人行道'),
    '购物袋': (r'包包|奶茶', r'购物袋|携带包|手提袋'),
}
# Strip only exact role-directory labels, never arbitrary store/chapter names.
ROLE_FOLDER = re.compile(r'^(?:人物|口播|达人|主线|人物口播|人物主线|达人口播|'
                         r'a[-_ ]?roll|b[-_ ]?roll|空镜|环境空镜|食物|菜品|食物特写|'
                         r'菜品特写|后厨|后厨制作|制作过程|上菜|上菜过程|补充画面|特写)$', re.I)
BROLL_FOLDER = re.compile(r'^(?:b[-_ ]?roll|空镜|环境空镜|食物|菜品|食物特写|菜品特写|'
                          r'后厨|后厨制作|制作过程|上菜|上菜过程|补充画面|特写)$', re.I)
FOOD_CONTEXT = re.compile(r'早餐|早茶|点心|菜品|粤菜|吃饭|自己的嘴|餐食|这道菜|这家店|'
                          r'烧烤|烤串|烤肉|串串|羊肉|牛肉|猪肉|五花肉|鸡翅|鸡爪|鸡肉|'
                          r'海鲜|生蚝|扇贝|茄子|土豆|肉串|羊排|牛排|烤鱼|火锅|炸鸡')


def scene_root(item):
    parent = Path(item['source']).parent
    while parent != parent.parent and ROLE_FOLDER.fullmatch(parent.name):
        parent = parent.parent
    return parent


def explicit_insert_source(item):
    parent = Path(item['source']).parent
    return BROLL_FOLDER.fullmatch(parent.name) is not None


def same_scene(main, candidate):
    # A chapter/restaurant folder is a boundary, not the whole imported batch.
    # Preserve existing chapter -> named B-roll subfolder matches (e.g. /4 to
    # /4/示例餐厅菜品), and additionally recognize explicit sibling role folders.
    return (Path(candidate['source']).is_relative_to(Path(main['source']).parent)
            or scene_root(main) == scene_root(candidate))


def duration(item):
    return min(item['duration'], item.get('usableDuration', item['duration']))


def topics(text):
    return [topic for topic, (pattern, _) in TOPICS.items() if re.search(pattern, norm(text))]


def visible_service_action(summary):
    """An action proposal, not a speaker/worker identity inference.

    Person-dominant footage needs an explicit observed preparation/delivery
    action. Merely discussing cooking, holding a skewer, or tasting is not one.
    Final insert visibility and main-reaction checks are still mandatory.
    """
    text = norm(summary)
    if re.search(r'试吃|品尝|咀嚼|吃了一口|(?:介绍|讲解).{0,12}(?:制作|烹饪|烧烤|烤制|上菜|端菜)', text):
        return False
    return bool(re.search(
        r'(?:厨师|师傅|工作人员|店员|男子|女子|人员|双手|手部).{0,14}'
        r'(?:切菜|切肉|切配|翻炒|翻烤|烤制|煎制|刷酱|撒料|串肉|烹饪|翻动.{0,4}(?:肉|串|菜))'
        r'|(?:端着|端起|端来|端上|端菜|递上|递出|递来|摆上|放上|放到|摆到).{0,8}(?:菜|盘|餐|桌|托盘)'
        r'|(?:盘|餐|菜).{0,8}(?:摆到|放到|端到|递给)', text))


def scene_candidate(item):
    vision = item.get('vision', {})
    return (item.get('video') and duration(item) >= MIN_SHOT + .3 and
            (vision.get('kind') in ('环境', '食物') or
             (vision.get('kind') == '人物' and visible_service_action(vision.get('summary', '')))))


def food_source_candidate(item):
    # A food-dominant shot may contain a chef, server, or a face in the background.
    # Face-dominant performance clips remain excluded unless filed as B-roll.
    return (item.get('video') and duration(item) >= MIN_SHOT + .3 and
            (food_candidate(item) or item.get('vision', {}).get('kind') == '食物' or
             (explicit_insert_source(item) and re.search(
                 r'食物|菜品|餐食|肉串|烤串|烤肉|烧烤|盘中|盘子里',
                 item.get('vision', {}).get('summary', '')))))


def protected_ranges(clip, item, boundary=None):
    """Protect explicit audio events and visual reaction handles, not an entire
    long sentence merely because a flag says its tail was extended."""
    ranges = [(a, b) for a, b, *_ in item.get('voiceGuard', {}).get('protected', [])]
    ranges += [(clip['start'], clip.get('speechStart', clip['start']) + .35),
               (clip.get('speechEnd', clip['end']) - .2, clip['end'])]
    if clip.get('voiceRole') in ('friend', 'mixed') or re.search(r'哈哈|呵呵|笑声', clip['text']):
        return [(clip['start'], clip['end'])]
    for evidence in (boundary or {}).get('clips', []):
        if evidence['assetId'] != clip['assetId']:
            continue
        times = evidence.get('startTimes', []) + evidence.get('endTimes', [])
        for row in evidence.get('observed', {}).get('frameActions', []):
            if row['action'] in ('bite', 'chew', 'handoff') and row['confidence'] >= .75:
                if 0 <= row['index'] < len(times):
                    t = times[row['index']]
                    ranges.append((t - .25, t + .5))
    return ranges


def free_windows(clip, item, boundary):
    windows = [(max(clip['start'] + .4, clip.get('speechStart', clip['start']) + .35),
                min(clip['end'] - .35, clip.get('speechEnd', clip['end']) - .2))]
    return subtract_ranges(windows, protected_ranges(clip, item, boundary))


def subtract_ranges(windows, protected):
    for a, b in protected:
        next_windows = []
        for x, y in windows:
            if b <= x or a >= y:
                next_windows.append((x, y))
            else:
                if a > x: next_windows.append((x, a))
                if b < y: next_windows.append((b, y))
        windows = next_windows
    return [(a, b) for a, b in windows if b - a >= MIN_SHOT]


def intent(clip, item, candidates, previous):
    text = norm(clip['text'])
    named = dish_tags(Path(item['source']).stem)
    explicit = dish_tags(text)
    tags = explicit | named
    basis = '台词菜名与人物素材命名限定的段落主题（非逐词对齐）' if explicit else '人物素材命名限定的菜品主题（非逐词对齐）'
    # A short pronoun continuation may inherit only an adjacent, same-scene,
    # unambiguous dish. No general-purpose cross-restaurant semantic guesses.
    if not tags and previous and re.match(r'^(这个|里面|它|这种|味道|口感)', text):
        prev, prev_tags = previous
        if same_scene(prev, item) and len(prev_tags) == 1:
            tags = set(prev_tags)
            basis = '相邻同场单一菜品的指代延续（需审片）'
    if not tags and '榴莲' in norm(Path(item['source']).stem + clip['text']):
        # Prefer the explicit source metadata, not contradictory model aliases.
        local = set().union(*(dish_tags(Path(c['source']).stem) for c in candidates if same_scene(item, c)))
        local = {t for t in local if '榴莲' in t}
        if len(local) == 1:
            tags = local
            basis = '同场唯一明确命名的榴莲点心（非内馅视觉证明）'
    # Here generic means outside the legacy fixed dish vocabulary, NOT permission
    # to substitute arbitrary food. Verification also receives the full dialogue.
    generic = not tags and bool(FOOD_CONTEXT.search(norm(clip['text'] + Path(item['source']).stem)))
    return tags, generic, basis


def options_for(item, tags, generic, wanted_topics, items, occupied, fps):
    options = []
    for candidate in items:
        if candidate['assetId'] == item['assetId'] or not same_scene(item, candidate):
            continue
        if food_source_candidate(candidate):
            for shot in candidate.get('foodShots', []):
                matches = tags.intersection(shot['tags']) if tags else set(shot['tags']) if generic else set()
                if not matches or shot['confidence'] < .65:
                    continue
                maximum = duration(candidate)
                if 'nextAt' in shot:
                    matches &= set(shot['nextTags'])
                    maximum = min(maximum, shot['nextAt'])
                if not matches:
                    continue
                start = math.ceil(shot['at'] * fps) / fps
                if maximum - start < MIN_SHOT:
                    continue
                dish = sorted(matches)[0]
                options.append(dict(item=candidate, start=start, maximum=maximum,
                    dish=dish if tags else '同场餐食', brollKind='food', confidence=shot['confidence'],
                    frame=shot.get('frame'), basis=shot.get('basis', '逐帧食物候选')))
            # 'Other food' was previously discarded because it was outside the
            # seventeen-dish index. Recover only proposals; Qwen still has to
            # verify the actual dialogue's dish at all three sample points.
            named = dish_tags(Path(candidate['source']).stem)
            if (tags or generic) and not candidate.get('foodReview') and not (tags and named and not tags & named):
                frames = sorted(candidate.get('frames', []), key=lambda f:f['at'])
                original_frames = candidate.get('frames', [])
                indexed = {original_frames[row['index']]['at']: row
                           for row in candidate.get('foodIndex', {}).get('frames', [])
                           if isinstance(row.get('index'),int) and 0 <= row['index'] < len(original_frames)}
                known = {(o['item']['assetId'], o['start']) for o in options}
                for index, frame in enumerate(frames):
                    row = indexed.get(frame['at'])
                    if row and (not row.get('visible') or row.get('label') == '未见清晰食物' or row.get('confidence', 0) < .65):
                        continue
                    if row and tags and row.get('label') in dish_tags(row.get('label', '')) and row['label'] not in tags:
                        continue
                    if row is None and candidate.get('vision', {}).get('kind') != '食物':
                        continue
                    start = math.ceil(frame['at']*fps)/fps
                    maximum = min(duration(candidate), frames[index+1]['at'] if index+1<len(frames) else duration(candidate))
                    if maximum-start < MIN_SHOT or (candidate['assetId'],start) in known:
                        continue
                    options.append(dict(item=candidate, start=start, maximum=maximum,
                        dish=sorted(tags)[0] if tags else '同场餐食', brollKind='food',
                        confidence=row.get('confidence',0) if row else 0,
                        frame=frame.get('file'), basis='可见餐食候选；须按完整口播核对具体菜品'))
        if wanted_topics and scene_candidate(candidate):
            summary = candidate.get('vision', {}).get('summary', '')
            labels = summary + ' ' + Path(candidate['source']).stem + ' ' + Path(candidate['source']).parent.name
            for topic in wanted_topics:
                if not re.search(TOPICS[topic][1], labels):
                    continue
                if topic not in ('后厨制作','上菜过程') and candidate.get('vision', {}).get('kind') != '环境':
                    continue
                if topic == '日常穿着' and not re.search(r'穿|短裤|短袖', str(Path(candidate['source']).parent)):
                    continue
                frames = sorted(candidate.get('frames', []),key=lambda f:f['at'])
                for index, frame in enumerate(frames):
                    start = math.ceil(frame['at'] * fps) / fps
                    maximum = min(duration(candidate),frames[index+1]['at'] if index+1<len(frames) else duration(candidate))
                    if maximum-start < MIN_SHOT:
                        continue
                    options.append(dict(item=candidate, start=start, maximum=maximum,
                        dish=topic, brollKind='environment', confidence=.7,
                        frame=frame['file'], basis='同场文件夹与视觉内容；待首中尾复核'))
    return sorted(options, key=lambda o: (o['dish'] not in ('后厨制作','上菜过程'),
                                         len(occupied.get(o['item']['assetId'], [])),
                                         -min(3.2, o['maximum']-o['start']), -o['confidence'], o['start']))


def proposals(plan, items, boundary=None):
    """Pure deterministic plan. Rejected proposals never change main/audio."""
    lookup = {i['assetId']: i for i in items}
    candidates = [i for i in items if food_source_candidate(i)]
    previous = None
    for clip in plan['selectedSpeech']:
        item = lookup[clip['assetId']]
        tags, generic, basis = intent(clip, item, candidates, previous)
        previous = (item, tags)
        windows = free_windows(clip, item, boundary)
        spoken = max(0, clip.get('speechEnd', clip['end']) - clip.get('speechStart', clip['start']))
        budget = spoken * .45  # Upper budget, not a quota; no forced coverage.
        yield dict(clip=clip, item=item, tags=tags, generic=generic, basis=basis,
                   topics=topics(clip['text']) or topics(Path(item['source']).stem), windows=windows, budget=budget)


def infer_frames(source, times, model, prompt, choices, cache_root):
    if not model or 'cloud' in model.lower():
        raise ValueError('补画面复核仅允许已安装的本地视觉模型')
    stat = Path(source).stat()
    key = hashlib.sha256(json.dumps([REVISION, source, stat.st_size, stat.st_mtime_ns, times,
                                    model, prompt, choices], ensure_ascii=False).encode()).hexdigest()
    folder = cache_root/key
    cached = read(folder/'result.json')
    if cached:
        return cached
    folder.mkdir(parents=True, exist_ok=True)
    files = []
    for n, t in enumerate(times):
        path = folder/f'{n}.jpg'
        subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(), '-v', 'error', '-nostdin', '-y',
            '-ss', str(t), '-i', source, '-frames:v', '1', '-vf',
            'scale=480:640:force_original_aspect_ratio=decrease', '-threads', '1', str(path)],
            check=True, capture_output=True, timeout=45)
        files.append(path)
    schema = {'type':'object','properties':{'frames':{'type':'array','minItems':len(times),'maxItems':len(times),
        'items':{'type':'object','properties':{'index':{'type':'integer'},'label':{'type':'string','enum':choices},
        'confidence':{'type':'number'},'description':{'type':'string'}},
        'required':['index','label','confidence','description']}}},'required':['frames']}
    payload = {'model':model,'stream':False,'format':schema,
        'options':{'temperature':0,'num_ctx':8192,'num_predict':512},
        'messages':[{'role':'user','content':prompt + f' 共{len(times)}张图，逐张独立返回index从0开始、label、confidence(0到1)、description(15字内)。',
            'images':[base64.b64encode(f.read_bytes()).decode() for f in files]}]}
    request = urllib.request.Request(OLLAMA_URL+'/api/chat', data=json.dumps(payload).encode(),
                                     headers={'Content-Type':'application/json'})
    with local_urlopen(request, timeout=180) as response:
        result = json.loads(json.loads(response.read())['message']['content'])
    rows = result.get('frames', [])
    if sorted(r.get('index', -1) for r in rows) != list(range(len(times))):
        raise ValueError('补画面复核帧序号无效')
    for r in rows:
        if (r.get('label') not in choices or type(r.get('confidence')) not in (int,float)
                or not math.isfinite(r['confidence']) or not 0 <= r['confidence'] <= 1):
            raise ValueError('补画面复核字段无效')
    result['evidence'] = [{'at':t,'file':str(f.relative_to(DATA))} for t,f in zip(times,files)]
    write(folder/'result.json', result)
    return result


def verify_insert(candidate, clip, main, model):
    a, b = candidate['start'], candidate['end']
    times = [a, (a+b)/2, b-1/30]
    prompt = ('逐张判断画面是否符合指定主题。label必须按如下含义选择：'
              'usable=主体清楚可见且符合主题；reject=明显是另一主题或主体尚未出现；'
              'unclear=看不清主体，无法判断。能看到清楚主体时选usable，不因背景虚化或画面中有手而拒绝。'
              '不猜画外食材、人物身份、地点身份。')
    if candidate['brollKind']=='food':
        prompt += '食物特写可包含端盘的手和背景人物；食物主体清楚且符合口播具体主题时才usable。只有空桌、锅盖或脸挡住食物时reject。'
    elif candidate['dish']=='后厨制作':
        prompt += '必须清楚看到正在制作、处理或烹饪食物的动作，厨师或工作人员入镜可以使用；仅成品静物或达人试吃不算制作过程。'
    elif candidate['dish']=='上菜过程':
        prompt += '必须清楚看到端菜、递盘、摆盘上桌或出餐的动作；仅已经摆好的餐桌、达人试吃或人物普通走动不算上菜过程。'
    elif candidate['dish']=='菜单':
        prompt += '菜单必须是手持或桌面上的点菜册/点菜纸，清楚可见菜品列表布局。墙面装饰画、相框、海报、店内环境一律reject，不因拍摄在餐厅就当菜单。'
    elif candidate['dish']=='购物袋':
        prompt += '必须能清楚看到人物手提的购物袋是画面重点；只有远处小行人、看不清袋子、普通城市街景一律reject。'
    prompt += ('本次要求：与口播相符的餐食画面。' if candidate['dish']=='同场餐食' else
               f'本次要求：{candidate["dish"]}。若画面是另一种菜或另一种内容选reject。')
    if candidate['brollKind']=='food' or candidate['dish'] in ('后厨制作','上菜过程') or candidate.get('semanticRetrieval'):
        prompt += (f'对应完整口播：{clip["text"]}。素材原文件名：{Path(candidate["source"]).stem}。'
                   '如果口播提及具体菜品、肉类或做法，必须对应，不能仅凭同场或都是烧烤便替换。'
                   '羊肉不能用牛肉替代；肉类/内馅无法从外观区分，且素材命名也无法支持时选unclear。'
                   '只有泛讲门店、餐食时才可用同场一般餐食。文件名仅供拍摄标注参考，画面仍须有相符可见主体。')
    if candidate.get('semanticRetrieval'):
        prompt += ('该片段来自语义检索，检索分数不是画面正确的证据。'
                   '必须支持这段口播具体的可见内容；仅同店背景或人物普通讲话不能代替食物、制作、上菜动作。'
                   '若口播没有可以由当前画面证明的内容，或只有大致相关而不对应，选unclear。')
    # Ingredients not visually distinguishable need explicit user metadata and
    # must not be presented as visual proof of the ingredient.
    if candidate['dish'] in dish_tags(Path(candidate['source']).stem):
        prompt += f'素材原文件名是{Path(candidate["source"]).stem}，作为拍摄者标注参考；画面仍必须有对应主体，标注不能代替可见性判断。'
    visual = infer_frames(candidate['source'], times, model, prompt,
                          ['usable','reject','unclear'], DATA/'broll-cache')
    if not all(r['label']=='usable' and r['confidence']>=.75 for r in visual['frames']):
        return False, {'source':visual, 'reason':'补充素材首中尾未全部通过可见性/主题复核'}
    start = clip['start'] + candidate['at'] - clip['at']
    end = start + b-a
    face = infer_frames(main['source'], [start, (start+end)/2, end-1/30], model,
        'label按以下含义逐张选择：talk=面向镜头普通说话或讲解，包含普通讲解手势；reaction=明显咬下、咀嚼、大笑、惊讶、递接物或与旁人互动；unclear=无法判断人物动作。普通张嘴说话选talk，不当成咀嚼。不猜声音、身份或内心。',
        ['talk','reaction','unclear'], DATA/'broll-cache')
    passed = all(r['label']=='talk' and r['confidence']>=.7 for r in face['frames'])
    return passed, {'source':visual, 'main':face,
                    'reason':'首中尾复核通过' if passed else '主线可能包含反应/互动，保留人物画面'}


def enhance(plan, items, request, output, verifier=None, preserve_existing=False, protected_main_ranges=None):
    if not request.get('visionModel'):
        return plan
    from vision_combo import create
    started = time.monotonic()
    engine, report = create(request) if verifier is None else (None, {'status':'test-verifier'})
    try:
        return _enhance(plan, items, request, output, verifier, engine, report,
                        preserve_existing, protected_main_ranges)
    finally:
        report['elapsedSeconds'] = round(time.monotonic()-started, 3)
        write(output/'vision-combo-report.json', report)
        if engine is not None: engine.close()


def _enhance(plan, items, request, output, verifier, engine, combo_report,
             preserve_existing=False, protected_main_ranges=None):
    """Replace V2 only after validation. All identities, V1 cuts and audio stay."""
    original = copy.deepcopy(plan)
    model = request.get('visionModel')
    if not model:
        return plan
    fps = int(request.get('fps', 30))
    boundary = read(output/'boundary-review.json', {})
    accepted = copy.deepcopy(original['foodPlacements']) if preserve_existing else []
    decisions, occupied, block_reports = [], {}, []
    reserved = list(protected_main_ranges or [])
    for shot in accepted:
        reserved.append((shot['at'],shot['at']+shot['end']-shot['start']))
        occupied.setdefault(shot['assetId'],[]).append((shot['start'],shot['end']))
    def combined_verify(c, s, i):
        facial = None
        if engine is not None:
            start = s['start'] + c['at'] - s['at']
            try:
                allowed, facial = engine.check_face(i['source'],start,start+c['end']-c['start'])
            except Exception as error:
                # Keep the performance if the extra protection fails mid-job.
                combo_report['warnings'].append('表情检测失败，此候选保留人物画面：'+str(error))
                return False, {'main':{'faceMotionError':str(error)},'error':str(error),
                               'reason':'表情检测失败，保留人物画面'}
            if not allowed:
                return False, {'main':{'faceMotion':facial},'reason':facial['reason']}
        passed,evidence = verify_insert(c,s,i,model)
        if facial is not None: evidence['faceMotion']=facial
        return passed,evidence
    verifier = verifier or combined_verify
    blocks = list(proposals(plan, items, boundary))
    for index, block in enumerate(blocks, 1):
        clip, item = block['clip'], block['item']
        local_reserved = [(clip['start']+a-clip['at'],clip['start']+b-clip['at']) for a,b in reserved]
        speech_left = clip['at'] + clip.get('speechStart',clip['start']) - clip['start']
        speech_right = clip['at'] + clip.get('speechEnd',clip['end']) - clip['start']
        already_covered = sum(max(0,min(speech_right,shot['at']+shot['end']-shot['start'])
                                  -max(speech_left,shot['at'])) for shot in accepted)
        # Required pictures remain intact, but do not stack another 45% of
        # optional inserts on top of their already occupied screen time.
        budget = max(0,block['budget']-already_covered)
        remaining = subtract_ranges(list(block['windows']),local_reserved)
        block_report = {'assetId':clip['assetId'],'at':clip['at'],'text':clip['text'],
                        'safeWindows':remaining,'topics':block['topics'],'tags':sorted(block['tags']),
                        'candidateCount':0,'reviewedCount':0,'insertedCount':0}
        before_decisions = len(decisions)
        reason = ('no-safe-window' if not remaining or budget < MIN_SHOT else
                  'no-topic' if not block['tags'] and not block['generic'] and not block['topics'] else
                  'no-candidates')
        count = 0
        print(f'段落补画面 {index}/{len(blocks)}：{Path(item["source"]).name}', flush=True)
        # Try distinct positions before giving up, but bound local inference.
        # Localized face exclusions may advance in small increments. Keep a
        # bounded larger search budget without weakening either visual gate.
        for attempt in range(10 if engine is not None else 5):
            if budget < MIN_SHOT or not remaining or count >= 2:
                break
            left, right = remaining.pop(0)
            left = math.ceil(left * fps) / fps
            length = math.floor(min(3.2, budget, right-left) * fps) / fps
            if length < MIN_SHOT:
                continue
            options = options_for(item, block['tags'], block['generic'], block['topics'], items, occupied, fps)
            if engine is not None:
                try:
                    options = engine.rank(block, options, items, occupied, fps)
                except Exception as error:
                    combo_report['warnings'].append('检索失败，此段沿用原候选：'+str(error))
            # A different embedding space has a different score scale. Preserve
            # its ordering ahead of legacy options; never compare raw CLIP and
            # video-embedding scores or treat either as a visual confirmation.
            import video_retrieval
            semantic = (video_retrieval.retrieved_options(block,items,occupied,fps,output)
                        if video_retrieval.enabled(request) else [])
            if semantic:
                seen = {(o['item']['assetId'],round(o['start'],6)) for o in semantic}
                options = semantic + [o for o in options
                    if (o['item']['assetId'],round(o['start'],6)) not in seen]
                block_report['semanticCandidateCount'] = len(semantic)
            block_report['candidateCount'] = max(block_report['candidateCount'],len(options))
            if options:
                reason='no-unused-source-window'
            chosen = None
            for option in options:
                n = math.floor(min(length, option['maximum']-option['start']) * fps) / fps
                if n < MIN_SHOT:
                    continue
                used = occupied.get(option['item']['assetId'], [])
                if len(used) >= 3 or any(option['start'] < b and option['start']+n > a for a,b in used):
                    continue
                # Do not retry the same rejected source window at the same main location.
                key = (option['item']['assetId'], option['start'], round(left,3))
                if any(tuple(d['key']) == key for d in decisions):
                    continue
                chosen = dict(assetId=option['item']['assetId'], source=option['item']['source'],
                    start=option['start'], end=option['start']+n,
                    at=round((clip['at']+left-clip['start'])*fps)/fps,
                    dish=option['dish'], brollKind=option['brollKind'], quote=clip['text'],
                    mainAssetId=clip['assetId'], mainSourceStart=left, confirmed=False,
                    matchBasis=(block['basis'] if block['tags'] else '同场段落主题')+' / '+option['basis'],
                    confidence=option['confidence'], evidenceFrame=option['frame'],
                    retrievalScore=option.get('retrievalScore'),
                    semanticRetrieval=option.get('semanticRetrieval',False),
                    retrievalQuery=option.get('retrievalQuery'),
                    retrievalEvidence=option.get('retrievalEvidence'))
                try:
                    passed, evidence = verifier(chosen, clip, item)
                except Exception as error:
                    passed, evidence = False, {'reason':'本地复核失败；不插入', 'error':str(error)}
                decisions.append({'key':key,'placement':chosen,'passed':passed,'evidence':evidence})
                reason='review-rejected'
                if passed:
                    accepted.append(chosen)
                    occupied.setdefault(chosen['assetId'], []).append((chosen['start'],chosen['end']))
                    budget -= n
                    count += 1
                    # Consecutive food shots form a montage; don't flash the
                    # face for a few frames between two B-roll inserts.
                    if right-left-n >= MIN_SHOT:
                        remaining.insert(0,(left+n,right))
                else:
                    # Main reaction: shift to another position within the same
                    # speech window. Source mismatch: try another source first.
                    if evidence.get('main'):
                        localized=evidence['main'].get('faceMotion',{}).get('protectedRanges',[])
                        if localized:
                            remaining=subtract_ranges([(left,right)],localized)+remaining
                        elif right-left-length-.5 >= MIN_SHOT:
                            remaining.insert(0,(left+length+.5,right))
                    else:
                        remaining.insert(0,(left,right))
                break
            if chosen is None:
                break
        block_report.update(reason='inserted' if count else reason,insertedCount=count,
                            reviewedCount=len(decisions)-before_decisions)
        block_reports.append(block_report)
        write(output/'broll-review.json', {'revision':REVISION,'decisions':decisions,'blocks':block_reports,'complete':False})
    plan['foodPlacements'] = sorted(accepted,key=lambda c:c['at'])  # legacy schema also holds environment V2
    plan['usedFoodSources'] = len({c['assetId'] for c in accepted if c.get('brollKind','food')=='food'})
    summary = {'revision':REVISION,'insertCount':len(accepted),
        'newInsertCount':len(accepted)-(len(original['foodPlacements']) if preserve_existing else 0),
        'preservedCount':len(original['foodPlacements']) if preserve_existing else 0,
        'visionCombo':combo_report,
        'foodCount':sum(c.get('brollKind','food')=='food' for c in accepted),
        'environmentCount':sum(c.get('brollKind')=='environment' for c in accepted),
        'processCount':sum(c.get('dish')=='后厨制作' for c in accepted),
        'servingCount':sum(c.get('dish')=='上菜过程' for c in accepted),
        'scriptCount':sum(c.get('brollKind')=='script' for c in accepted),
        'seconds':round(sum(c['end']-c['start'] for c in accepted),3),
        'oldCount':len(original['foodPlacements']),
        'oldSeconds':round(sum(c['end']-c['start'] for c in original['foodPlacements']),3),
        'rejected':sum(not d['passed'] for d in decisions),
        'mainAndAudioUnchanged':plan['selectedSpeech']==original['selectedSpeech'],
        'note':'首中尾抽样复核，不是全帧理解或人工审片；反应保护失败/不确定时不插入。不强凑覆盖率。'}
    food_sections = sum(b['clip']['end']-b['clip']['start'] for b in blocks if b['tags'] or b['generic'])
    food_seconds = sum(c['end']-c['start'] for c in accepted if c.get('brollKind','food')=='food')
    summary.update(foodSeconds=round(food_seconds,3),foodSectionSeconds=round(food_sections,3),
                   foodSectionCoverage=round(food_seconds/food_sections,4) if food_sections else 0)
    summary['coverage'] = round(summary['seconds']/plan['duration'],4) if plan['duration'] else 0
    summary['blockStatusCounts'] = {reason:sum(b['reason']==reason for b in block_reports)
                                   for reason in sorted({b['reason'] for b in block_reports})}
    summary['unfilledBlocks'] = sum(b['reason']!='inserted' for b in block_reports)
    plan['brollSummary'] = summary
    errors = [d['evidence']['error'] for d in decisions if d['evidence'].get('error')]
    summary['reviewErrorCount'] = len(errors)
    summary['optionalReviewComplete'] = not errors
    summary['warnings'] = ([f'补充画面复核有 {len(errors)} 次失败；已通过的镜头保留，未确认自动补齐，请查看未插入原因。']
                           if errors else [])
    if errors and not accepted:
        write(output/'broll-review.json', {'revision':REVISION,'decisions':decisions,'blocks':block_reports,'complete':False,
            'summary':summary,'error':'本地复核未完成，停止导出：'+errors[0]})
        raise RuntimeError('本地补画面复核失败，不能把零插入标为完成：'+errors[0])
    write(output/'broll-review.json', {'revision':REVISION,'decisions':decisions,'blocks':block_reports,'complete':not errors,'summary':summary})
    return plan
