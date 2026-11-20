"""Extract evidenced dialogue from a local script, without generating new words.

The original text and source line for every target remain available for review.
Formatting is evidence of dialogue, not evidence of a person's voice identity.
"""
import re

REVISION = 'script-normalize-4'
NO_DIALOGUE = '未从这份文稿中识别到明确台词。请检查是否选中了正确脚本；已导入脚本但无可用台词时会停止，不会把全部口播当成按脚本粗剪。无需手动改格式。'
KNOWN = r'达人|达人|闺蜜|朋友|助理|搭档|主持人|主播|嘉宾|口播|台词|旁白|解说|配音|V\.?O\.?'
KNOWN_RE = re.compile(r'^(?:' + KNOWN + r')', re.I)
ROLE_ACTION = r'(?:指着|看着|舀起|点菜|旁白|配音|笑着说|边走边说|说|问|回答|回应|疑惑|支支吾吾|气喘吁吁|喘气|得意|嘲笑|俯身|快速爬|跑步|挥手|抬头|低头|抢|拿出|拿起|放下|坐在|站在)'
ROLE_CUE = r'(?:[（(][^）)\n]{0,30}[）)]|' + ROLE_ACTION + r'[^：:\n]{0,16})?'
META_RE = re.compile(r'^(?:标题|主题|文案标题|品牌|客户|编导|导演|摄影|拍摄|镜头|场景|画面|动作|备注|花字|字幕|音效|音乐|BGM|AI音|AI配音|人工智能|要求|注意|目标|时长|时间|序号|转场|素材|结构|内容结构|出镜演员|人物设定|发布|选题|话题|服装|道具|妆造|执行|计划|剪辑|节奏|参考)', re.I)
DIRECTION_RE = re.compile(r'(?:画面|镜头|特写|空镜|花字|字幕|转场|音效|AI音|BGM|旁拍|跟拍|闪身步|入镜|出镜|遮挡|掏出|递给|抽出|掰开|拿起|放下|打开|看到|看价格|舀起|走向|走进|看向|点头|摇头|捂嘴|回头|咀嚼|试吃|展示|拍照|制止|指着|边走|偷偷|闻|笑|哭|吃)', re.I)
DIALOGUE_HEADERS = {'台词', '对白', '口播', '口播文案', '台词内容', '对白内容', '文案', '解说词', '配音文案', '文案内容', '声音', '音频'}
ROLE_HEADERS = {'角色', '人物', '说话人', '姓名', '出镜人物', '演员'}
SCENE_HEADERS = {'场景', '地点', '场景地点', '拍摄地点'}
SHOT_HEADERS = {'画面', '画面内容', '画面描述', '镜头内容', '镜头描述', '画面需求'}
SYNTHETIC_NAME = r'(?:AI\s*(?:画外(?:配音|音)|合成配音|配音|音|旁白)|人工智能(?:配音|旁白)|合成(?:配音|旁白))'
SYNTHETIC_LABEL = re.compile(SYNTHETIC_NAME + r'$', re.I)
SYNTHETIC_MARKER = re.compile(r'[（(【\[]\s*' + SYNTHETIC_NAME + r'\s*[）)】\]]|' + SYNTHETIC_NAME + r'\s*[：:]', re.I)
# Repeated role labels are formatting evidence, never voice identity.  Restrict
# unquoted unfamiliar labels to plausible person names, excluding product keys
# that can also begin with a surname (e.g. 颜色). Explicit role columns/quotes
# continue to support arbitrary nicknames without this heuristic.
COMMON_SURNAMES = set('赵钱孙李周吴郑王冯陈褚卫蒋沈韩杨朱秦尤许何吕施张孔曹严华金魏陶姜戚谢邹喻柏水窦章云苏潘葛范彭郎鲁韦昌马苗方俞任袁柳史唐费廉岑薛雷贺倪汤滕殷罗毕郝邬安常乐于时傅皮卞齐康伍余元卜顾孟平黄和穆萧尹姚邵汪祁毛禹狄米贝明臧计伏成戴宋茅庞熊纪舒屈项祝董梁杜阮蓝闵席季麻强贾路娄危江童颜郭梅盛林刁钟徐邱骆高夏蔡田胡凌霍虞万支柯昝管卢莫房裘缪解应宗丁宣邓郁单杭洪包诸左石崔吉龚程嵇邢滑裴陆荣翁荀羊甄曲封芮储靳汲邴糜松井段富巫乌焦巴弓牧隗山谷车侯宓蓬全班仰秋仲伊宫宁仇栾甘厉戎祖武符刘景詹束龙叶幸司韶黎蓟薄印宿白怀蒲台从索咸赖卓蔺屠蒙池乔阴胥能苍双闻莘党翟谭贡劳逄姬申扶堵冉宰郦雍却璩桑桂濮牛寿通边扈燕冀郏浦尚农温庄晏柴瞿阎连茹习艾鱼容向古易慎戈廖庾终暨居衡步都耿满弘匡国文寇广禄阙东欧利师巩聂晁勾敖融冷訾辛阚那简饶曾关蒯相查后荆红游竺权逯盖益桓公')
NON_PERSON_LABELS = {'颜色', '口味', '价格', '特色', '卖点', '推荐', '菜品', '店名', '地址',
                     '风格', '质量', '环境', '服务', '分量', '味道', '份量', '人数', '效果',
                     '体验', '产品', '商品', '活动', '时间', '地点', '正文', '主题', '内容'}
EDIT_POLICY_KEY = re.compile(r'^(?:durationRangeSeconds|directorInstructions|rolePolicy|repetitionPolicy|brollPolicy|timebase|audioPolicy)\s*[：:]', re.I)
REVIEW_SECTION = re.compile(r'^【(S\d+)\s+([^】]+)】\s*(.*)$', re.I)
POLICY_SECTION = re.compile(r'^【(?:删除和压缩说明|审片重点|剪辑说明|剪辑规则|后期说明)】')
BROLL_SECTION = re.compile(r'^【(?:第二轨(?:道)?画面|B[- ]?roll(?:画面|清单))[^】]*】', re.I)
TIMECODE = r'\d+:\d{2}:\d{2}(?::\d{2})?'


def _reviewed_timecode(value, fps):
    """Old edit references are MM:SS:FF (or HH:MM:SS:FF), never new at values."""
    if fps is None or not re.fullmatch(TIMECODE, value):
        return None
    parts = list(map(int, value.split(':')))
    minutes, seconds, frames = parts[-3:]
    if seconds >= 60 or frames >= fps or len(parts) == 4 and minutes >= 60:
        return None
    return (parts[0] * 3600 if len(parts) == 4 else 0) + minutes * 60 + seconds + frames / fps


def _reviewed_reference(value, fps):
    label = re.search(r'成片\s*([^｜|]*)', value)
    if label is None:
        return None
    match = re.fullmatch(r'(' + TIMECODE + r')(?:\s*[—–－-]\s*(' + TIMECODE + r'))?', label[1].strip())
    if match is None:
        return {'text': label[0], 'start': None, 'end': None, 'valid': False}
    start = _reviewed_timecode(match[1], fps)
    end = _reviewed_timecode(match[2], fps) if match[2] else start
    return {'text': label[0], 'start': start, 'end': end,
            'valid': start is not None and end is not None and end >= start}


def _shared_subject_length(description, value):
    """Literal content overlap only; this associates script rows, not footage."""
    compact = lambda s: re.sub(r'[^a-z0-9\u4e00-\u9fff]', '', (s or '').lower())
    left, right = compact(description), compact(value)
    generic = {'画面', '镜头', '特写', '空镜', '环境', '人物', '素材', '达人', '达人', '朋友', '闺蜜', '这里', '就是'}
    for length in range(min(24, len(left), len(right)), 1, -1):
        if any(left[i:i + length] in right and left[i:i + length] not in generic
               for i in range(len(left) - length + 1)):
            return length
    return 0


def _associate_reviewed_shot(shot, sections, targets):
    """Resolve the original chapter first, then target actual dialogue in it.

    Original edit timecodes are provenance only. A missing/overlapping chapter
    cannot fall back to the entire script, or set a new timeline insertion time.
    """
    reference = shot.get('_reference')
    chapter = None
    method = None
    reason = ''
    if reference is not None:
        if reference['valid']:
            matches = []
            for section in sections:
                span = section.get('reference')
                if not span or not span['valid'] or span['end'] <= span['start']:
                    continue
                if (span['start'] <= reference['start'] < span['end'] and
                        reference['end'] <= span['end']):
                    matches.append(section)
            if len(matches) == 1:
                chapter, method = matches[0], 'original-chapter-reference'
            else:
                reason = '原清单成片位置未落入唯一章节，保留待审，不指定新片位置'
        else:
            reason = '原清单时间码或帧率不明确，保留待审，不指定新片位置'
    elif shot.get('_scope'):
        matches = [s for s in sections if s['scope'] == shot['_scope']]
        if len(matches) == 1:
            chapter, method = matches[0], 'explicit-chapter'
    if chapter is None and reference is None and not reason:
        scores = []
        for section in sections:
            local = [t for t in targets if t['_scope'] == section['scope']]
            score = max([_shared_subject_length(shot['text'], section['scene'])] +
                        [_shared_subject_length(shot['text'], t['text'] + ' ' + (t['sourceName'] or '')) for t in local])
            scores.append((score, section))
        best = max([score for score, _ in scores], default=0)
        matches = [section for score, section in scores if score == best and score >= 2]
        if len(matches) == 1:
            chapter, method = matches[0], 'unique-content-chapter'
        else:
            reason = '画面要求无法关联到唯一章节台词，保留待审'
    eligible = [(index, t) for index, t in enumerate(targets, 1)
                if chapter is not None and t['_scope'] == chapter['scope']]
    scores = [(index, max(_shared_subject_length(shot['text'], t['text']),
                         _shared_subject_length(shot['text'], t['sourceName']))) for index, t in eligible]
    best = max([score for _, score in scores], default=0)
    # Environment shots may be tied to a proven chapter without a literal word
    # in its speech. This bounded chapter association never means all targets.
    ids = [index for index, score in scores if score == best] if best >= 2 else [index for index, _ in eligible]
    result = {'scene': chapter['scene'] if chapter else '', 'targetIds': ids,
              'anchorMode': 'targets-only', 'associationStatus': 'matched' if ids else 'unanchored',
              'associationReason': ('按原章节内的台词及素材名称关联' if best >= 2 else '按原清单的章节上下文关联')
                                   if ids else reason or '对应章节没有可用台词，保留待审',
              'associationEvidence': {'method': method, 'sectionId': chapter['id'] if chapter else None,
                                      'sharedSubjectLength': best}}
    return result


def _repeated_person_label(name):
    if name in NON_PERSON_LABELS:
        return False
    if re.fullmatch(r'[老小阿][\u4e00-\u9fff]{1,2}', name):
        return True
    return bool(re.fullmatch(r'[\u4e00-\u9fff]{2,3}', name) and name[0] in COMMON_SURNAMES)


def _plain(value):
    value = value.strip().replace('\ufeff', '')
    value = re.sub(r'^\s*(?:>\s*|[-*+]\s+|(?:\d+|[一二三四五六七八九十]+)[.、）)]\s*)', '', value)
    return value.replace('**', '').replace('__', '').strip()


def _speaker(label, explicit=False):
    label = _plain(label).strip('【】[]：: \t')
    if (label.startswith('（') and label.endswith('）')) or (label.startswith('(') and label.endswith(')')):
        label = label[1:-1].strip()
    label = re.sub(r'^(?:下一秒切|切到)', '', label)
    if META_RE.match(label):
        return None
    if re.search(r'(?:需求|要求|说明|意图|备注|标题|方式|建议)$', label):
        return None
    match = KNOWN_RE.match(label)
    if match:
        return match[0] if re.fullmatch(ROLE_CUE, label[match.end():]) else None
    nickname = re.fullmatch(r'([老小阿][\u4e00-\u9fff]{1,2}?)(' + ROLE_ACTION + r'[^：:\n]{0,16}|[（(][^）)\n]{0,30}[）)])', label)
    if explicit and nickname:
        return nickname[1]
    # Unfamiliar names are accepted only in a declared role / dialogue column.
    if explicit and re.fullmatch(r'[\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z0-9· _-]{0,19}', label):
        return label
    return None


def _clean_speech(value):
    value = _plain(value)
    # Parenthetical stage directions are kept in sourceText, not matched as speech.
    def note(match):
        inner = match[0][1:-1]
        action = re.match(r'^(?:打开|拿起|放下|掏出|递给|抽出|掰开|舀起|又?看到|看价格|走向|走进|看向|点头|摇头|捂嘴|回头|咀嚼|试吃|展示|拍照|制止|指着|边走|偷偷|闻|笑|哭|吃(?:完|一口)?$)', inner)
        visual = re.search(r'画面|镜头|特写|空镜|花字|字幕|转场|音效|AI音|闪身步|遮挡', inner)
        actor_action = re.match(r'^(?:达人|达人|闺蜜|朋友).*(?:拍照|照片|拿起|递给|掏出|看向)', inner)
        return '' if action or visual or actor_action or META_RE.match(inner) else match[0]
    value = re.sub(r'（[^（）]*）|\([^()]*\)|【[^【】]*】|\[[^\[\]]*\]', note, value)
    value = re.sub(r'^(?:——|—|－)+\s*', '', value).strip()
    pairs = [('“', '”'), ('「', '」'), ('『', '』'), ('"', '"'), ("'", "'")]
    for left, right in pairs:
        if len(value) > 1 and value.startswith(left) and value.endswith(right):
            value = value[1:-1].strip()
    return value


def _speechlike(value, dialogue_context=False):
    """Only permit an unlabelled continuation with positive dialogue evidence."""
    if not value or META_RE.match(value) or re.fullmatch(r'[（(【\[].*[）)】\]]', value):
        return False
    if re.search(r'(?:画面|镜头|特写|空镜|转场|门口|菜单)$', value):
        return False
    if re.search(r'画面|镜头|特写|空镜|穿插|拍摄|机位|长焦', value):
        return False
    if re.search(r'^(?:采用|突出|表现|保留|体现|展示|介绍|拍摄|穿插|插入|建议|需要|要求|安排|拿起|放下|请(?:帮|让|把|拍|保留)|让(?:达人|达人|演员))', value):
        return False
    if re.search(r'[！？!?]|(?:我|你|咱|我们|这个|这里|这道|今天|真的|所以|因为|怎么|好吃|好看)', value):
        return True
    # A dialogue column is positive evidence; do not require pronouns or an
    # exclamation mark in every continuation (e.g. “五块钱，扫那边”).
    if dialogue_context:
        # Bare dish/product headings are not spoken merely because the author
        # placed them between quotes in the dialogue column.
        if re.search(r'[，,。；;：:]', value) and re.search(r'[A-Za-z\u4e00-\u9fff]', value):
            return True
    return bool(re.match(r'(?:(?:[零一二三四五六七八九十百千万两\d]+)(?:块|元|公里|分钟|小时)|嗯|啊|哇|好啦|好吧|对呀|没错|真香|行吧)', value))


def _cells(line):
    if '\t' in line:
        return [c.strip() for c in line.split('\t')]
    if line.strip().startswith('|') and line.strip().endswith('|'):
        return [c.strip() for c in line.strip()[1:-1].split('|')]
    return None


def _description_parts(value, separators='；;'):
    """Do not split a camera note or quotation halfway through parentheses."""
    pairs = {'（': '）', '(': ')', '【': '】', '[': ']', '“': '”', '「': '」', '『': '』'}
    stack, start, parts = [], 0, []
    for index, char in enumerate(value):
        if char in pairs:
            stack.append(pairs[char])
        elif stack and char == stack[-1]:
            stack.pop()
        elif char in separators and not stack:
            parts.append(value[start:index])
            start = index + 1
    return parts + [value[start:]]


def normalize_script(text):
    if not isinstance(text, str):
        raise ValueError('脚本内容应为文字，请重新导入原脚本')
    if len(text) > 100000:
        raise ValueError('脚本超过 10 万字，请缩小范围')
    source = text
    lines = text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
    targets, omitted, shot_parts, excluded = [], set(), [], []
    current_speaker = None
    current_synthetic = False
    current_scene = ''
    current_scope = 'document'
    reviewed_shots, reviewed_sections = [], []
    review_section = None
    fps_values = {float(m[1]) for m in re.finditer(r'(\d+(?:\.\d+)?)\s*fps\b', source, re.I)
                  if 1 <= float(m[1]) <= 120}
    reference_fps = next(iter(fps_values)) if len(fps_values) == 1 else None
    table = None
    declared = set()
    # Repeated name-like labels with spoken content are evidence of a speaker;
    # arbitrary repeated keys such as 品牌/颜色/价格 remain metadata, not names.
    name_counts = {}
    for raw in lines:
        for fragment in re.split(r'\t|<br\s*/?>|\u2028', raw, flags=re.I):
            m = re.match(r'^([^：:]{2,24})[：:]\s*(.+)', _plain(fragment))
            if not m or META_RE.match(m[1]) or not _speechlike(m[2]):
                continue
            name = _speaker(m[1], explicit=True)
            if name and _repeated_person_label(name):
                name_counts[name] = name_counts.get(name, 0) + 1
    declared.update(name for name,count in name_counts.items() if count >= 2)

    def exclude(value, line_no, kind='synthetic_audio'):
        excluded.append({'text': value.strip(), 'sourceLine': line_no,
                         'scene': current_scene, 'kind': kind})
        omitted.add(line_no)

    def add_shot(value, line_no, column):
        """Keep source descriptions, not generated shots or camera instructions."""
        for part in _description_parts(re.sub(r'<br\s*/?>|\u2028', '；', value, flags=re.I)):
            part = _plain(part).strip()
            if re.fullmatch(r'[（(【\[].*[）)】\]]', part):
                part = part[1:-1].strip()
            part = re.sub(r'^(?:画面|镜头内容|动作)[：:]\s*', '', part)
            # Post-production instructions and camera positions are not footage.
            sentences = []
            for sentence in re.split(r'(?<=。)', part):
                sentence = sentence.strip()
                if re.match(SYNTHETIC_NAME, sentence, re.I):
                    exclude(sentence, line_no)
                    continue
                if re.match(r'^(?:花字|字幕)', sentence):
                    # “花字‘15分钟后’，小林跑步” still requests real running footage.
                    split = re.split(r'[，,]', sentence, maxsplit=1)
                    exclude(split[0], line_no, 'postproduction')
                    sentence = split[1] if len(split) == 2 else ''
                if not sentence or re.match(
                    r'^(?:音效|音乐|BGM|配音|价格用花字|后期|剪辑|机位|拍摄方式|注意|要求)', sentence, re.I):
                    if sentence:
                        exclude(sentence, line_no, 'postproduction')
                    continue
                if re.match(r'^(?:穿插|弹出|加上|添加|加|用|以|配合|定格并加)[^，。]*(?:花字|字幕)', sentence):
                    exclude(sentence, line_no, 'postproduction')
                    continue
                suffix = re.search(r'[，,](?:弹出[^，。]*花字|花字|字幕|为后文|为下文)', sentence)
                if suffix:
                    exclude(sentence[suffix.start() + 1:], line_no, 'postproduction')
                    sentence = sentence[:suffix.start()]
                graphics = re.search(r'[＋+](?:[^，。]*花字|[^，。]*字幕)', sentence)
                if graphics:
                    exclude(sentence[graphics.start() + 1:], line_no, 'postproduction')
                    sentence = sentence[:graphics.start()]
                thought = re.search(r'[、，,](?:心生一计|心想|内心)', sentence)
                if thought:
                    exclude(sentence[thought.start() + 1:], line_no, 'nonvisual_description')
                    sentence = sentence[:thought.start()]
                if re.match(r'^(?:小林|助理|达人|达人|闺蜜|朋友)(?:口播|感叹|大方让|喊[“「"])', sentence):
                    exclude(sentence, line_no, 'spoken_description')
                    continue
                sentences.append(sentence)
            part = ''.join(sentences).strip()
            if not part or not re.search(r'[A-Za-z\u4e00-\u9fff]', part):
                continue
            if re.fullmatch(r'(?:中景|近景|远景|全景|特写|固定|手持|跟拍|俯拍|仰拍|长焦|机位|[＋+、，。\s]|切换|要近一点|要低)+', part):
                continue
            if re.search(r'(?:镜头用|机位要|用长焦|用广角|靠近镜头)', part):
                continue
            shot_parts.append({'scene': current_scene, 'text': part, 'sourceLine': line_no,
                               'sourceColumn': column, '_scope': current_scope})

    def add_shot_from_camera_notes(value, line_no, column):
        # Read only explicit content closeups, not camera-operation requirements.
        camera_words = r'中景|近景|远景|全景|特写|固定|手持|跟拍|俯拍|仰拍|长焦|机位|正反打|切换|视角|主观|侧向|略低角度'
        for part in _description_parts(re.sub(r'<br\s*/?>', '；', value, flags=re.I), '；;，,'):
            if not re.search(r'特写|近景', part):
                continue
            def note(match):
                if re.search(r'AI|花字|字幕|补全|顺带|核心镜头', match[0], re.I):
                    exclude(match[0], line_no, 'production_note')
                    return ''
                return match[0]
            part = re.sub(r'（[^（）]*）|\([^()]*\)', note, part)
            probe = re.sub(camera_words, '', part)
            probe = re.sub(r'[\W\d]|给|用|要', '', probe)
            if len(probe) < 2 or re.match(r'^(?:花字|字幕|音乐|音效)', part):
                continue
            add_shot(part, line_no, column)

    def add(value, speaker, line_no, dialogue=False, cue=None):
        nonlocal current_synthetic
        if SYNTHETIC_LABEL.fullmatch(speaker or ''):
            exclude(value, line_no)
            current_synthetic = True
            return False
        synthetic = SYNTHETIC_MARKER.search(value)
        if synthetic:
            exclude(value[synthetic.start():], line_no)
            value = value[:synthetic.start()]
            current_synthetic = True
        cleaned = _clean_speech(value)
        if not re.search(r'[\w\u4e00-\u9fff]', cleaned):
            return False
        targets.append({'text': cleaned, 'sourceName': None,
                        'dialogue': bool(dialogue or speaker in ('闺蜜', '朋友')),
                        'speaker': speaker or '台词', 'sourceLine': line_no,
                        'scene': current_scene, '_scope': current_scope})
        if cue:
            targets[-1]['speakerCue'] = cue.strip('（）() ')
        return True

    def parse(fragment, line_no, dialogue_context=False, role=None):
        nonlocal current_speaker, current_synthetic, current_scene, current_scope, review_section
        line = _plain(fragment)
        if not line:
            return
        section = REVIEW_SECTION.match(line)
        if section:
            current_scene = section[1].upper() + ' ' + section[2].strip()
            current_scope = 'reviewed-scene-' + str(line_no)
            reviewed_sections.append({'id': section[1].upper(), 'scene': current_scene,
                                      'scope': current_scope,
                                      'reference': _reviewed_reference(section[3], reference_fps)})
            review_section = 'scene'
            current_speaker, current_synthetic = None, False
            omitted.add(line_no)
            return
        if POLICY_SECTION.match(line):
            review_section = 'policy'
            current_speaker, current_synthetic = None, False
            exclude(line, line_no, 'production_policy')
            return
        if BROLL_SECTION.match(line):
            review_section = 'broll'
            current_scene, current_scope = '', 'reviewed-broll-' + str(line_no)
            current_speaker, current_synthetic = None, False
            exclude(line, line_no, 'production_policy')
            return
        if review_section == 'policy' or EDIT_POLICY_KEY.match(line):
            exclude(line, line_no, 'production_policy')
            current_speaker, current_synthetic = None, False
            return
        if re.match(r'^B\d+\s*[｜|]', line, re.I):
            cells = [part.strip() for part in re.split(r'[｜|]', line)]
            if len(cells) < 3 or not cells[1] or not cells[2]:
                exclude(line, line_no, 'invalid_shot_reference')
                return
            reference = _reviewed_reference(line, reference_fps)
            shot = {'originalEditId': cells[0].upper(), 'text': cells[1],
                    'sourceName': cells[2], 'sourceLine': line_no, 'sourceColumn': '第二轨画面清单',
                    'requiredTrack': 'V2',
                    '_scope': current_scope if review_section == 'scene' else None,
                    '_reference': reference,
                    'sourceRefs': [{'sourceLine': line_no, 'sourceColumn': '第二轨画面清单', 'text': line}]}
            source_range = re.search(r'(?:^|[｜|])\s*源\s+(\d+(?:\.\d+)?)\s*[—–－-]\s*(\d+(?:\.\d+)?)\s*s\b', line, re.I)
            if source_range:
                start, end = float(source_range[1]), float(source_range[2])
                if 0 <= start < end < float('inf'):
                    shot['sourceRange'] = {'in': start, 'out': end}
            if reference:
                shot['originalTimelineReference'] = reference['text']
            reviewed_shots.append(shot)
            return
        if SYNTHETIC_MARKER.match(line) or SYNTHETIC_LABEL.fullmatch(line):
            exclude(line, line_no)
            current_speaker = None
            current_synthetic = True
            return
        # Backwards compatibility for reviewed, source-linked edit scripts.
        legacy = re.search(r'台词意图[：:]\s*(.*)', line)
        if legacy:
            heading = line[:legacy.start()]
            speaker = re.search(r'角色意图[：:]([^｜|]+)', heading)
            value = legacy[1].split('｜素材：', 1)[0]
            add(value, speaker[1].strip() if speaker else '台词', line_no,
                bool(re.search('闺蜜|朋友|互动|接话', heading)))
            return
        match = re.match(r'^素材[：:]\s*(.+)', line)
        if match and targets:
            targets[-1]['sourceName'] = match[1].split('｜')[0].strip()
            return
        if re.match(r'^(?:出镜演员|出镜人物|角色|人物设定)[：:]', line):
            for name in re.split(r'[、,，/\s]+', line.split('：', 1)[-1].split(':', 1)[-1]):
                if _speaker(name, explicit=True):
                    declared.add(name)
            omitted.add(line_no)
            return
        scene_label = re.match(r'^(?:场景|地点)[：:]\s*(.+)', line)
        if scene_label:
            current_scene = scene_label[1].strip()
            current_scope = 'scene-' + str(line_no)
            current_speaker = None
            current_synthetic = False
            omitted.add(line_no)
            return
        if META_RE.match(line) and not KNOWN_RE.match(line):
            if re.match(r'^(?:画面|动作|镜头内容)[：:]', line):
                add_shot(line, line_no, '文案动作')
            current_speaker = None
            current_synthetic = bool(re.match(SYNTHETIC_NAME, line, re.I))
            if current_synthetic:
                exclude(line, line_no)
            omitted.add(line_no)
            return
        # A labelled speaker may be followed by a delivery/action cue.
        label_re = r'(?P<label>' + KNOWN + r'|'+ '|'.join(map(re.escape, sorted(declared, key=len, reverse=True))) + r')'
        label_re = label_re.replace('|)', ')')
        boundary = r'(?:^|(?<=[。！？!?；;”」])|\s+)\s*'
        cue = r'(?P<cue>' + ROLE_CUE + ')'
        meta_label = r'(?P<meta>(?:导演|编导|拍摄|镜头|场景|画面|动作|备注|花字|字幕|音效|BGM|' + SYNTHETIC_NAME + r')(?:方式|说明|要求|内容|备注|指令)?)'
        pattern = re.compile(boundary + '(?:' + label_re + cue + '|' + meta_label + r')[：:]\s*', re.I)
        matches = list(pattern.finditer(line))
        if matches:
            for index, match in enumerate(matches):
                end = matches[index + 1].start() if index + 1 < len(matches) else len(line)
                if match['meta']:
                    current_synthetic = bool(SYNTHETIC_LABEL.fullmatch(match['meta']))
                    if current_synthetic:
                        exclude(line[match.start():end], line_no)
                    current_speaker = None
                    omitted.add(line_no)
                    continue
                current_speaker = match['label']
                current_synthetic = False
                add(line[match.end():end], current_speaker, line_no, cue=match['cue'])
            return
        # Name alone on one line; the next paragraph contains the quote.
        role_only = re.fullmatch(r'[【\[]?(' + KNOWN + ('|' + '|'.join(map(re.escape, declared)) if declared else '') + r')[】\]]?[：:]?', line, re.I)
        if role_only:
            current_speaker = role_only[1]
            current_synthetic = False
            return
        if re.fullmatch(r'[（(【\[].*[）)】\]]', line):
            add_shot(line, line_no, '文案动作')
            omitted.add(line_no)
            return
        # Unknown explicit role labels, or dish-action labels inside dialogue cells.
        m = re.match(r'^([^：:]{1,24})[：:]\s*(.*)', line)
        if m and (dialogue_context or role or (current_speaker and m[1].startswith('吃')) or m[2].startswith(('“', '「', '『', '"'))):
            label = m[1]
            if label.startswith('吃') and len(label) <= 12:
                add(m[2], role or current_speaker or '台词', line_no)
                return
            speaker = _speaker(label, explicit=True)
            if speaker:
                current_speaker = speaker
                current_synthetic = False
                cue = label.replace(speaker, '', 1) if label != speaker else None
                add(m[2], speaker, line_no, cue=cue)
                return
            omitted.add(line_no)
            return
        cleaned = _clean_speech(line)
        action_start = r'(?:拿起|放下|掏出|接过|试吃|点头|摇头|走向|走进|拍照|看向|指着|展示|介绍|突出|表现|保留|拍摄|闪身步|来到|靠窗|跑向)'
        actors = '|'.join(map(re.escape, sorted(set(declared) | {'达人', '达人', '闺蜜', '朋友', '助理', role or '', current_speaker or ''} - {''}, key=len, reverse=True)))
        stage_only = bool(re.search(r'画面|镜头|特写|空镜|机位', cleaned)
                          or re.match(r'^(?:(?:' + actors + r')\s*)?' + action_start, cleaned)
                          or re.match(r'^(?:广角|城市景观)', cleaned))
        if stage_only:
            add_shot(line, line_no, '文案动作')
            omitted.add(line_no)
        elif current_synthetic:
            exclude(line, line_no)
        elif role and cleaned and not META_RE.match(cleaned):
            add(cleaned, role, line_no)
        elif current_speaker and _speechlike(cleaned, dialogue_context):
            add(cleaned, role or current_speaker, line_no)
        elif dialogue_context and _speechlike(cleaned, True):
            add(cleaned, '台词', line_no)
        else:
            omitted.add(line_no)

    for number, raw in enumerate(lines, 1):
        line = raw.strip()
        if not line:
            # Blank paragraphs don't erase a preceding explicit speaker.
            continue
        cells = _cells(raw)
        if cells:
            headers = [_plain(c).strip('：: ') for c in cells]
            if any(h in DIALOGUE_HEADERS or h in SHOT_HEADERS for h in headers):
                table = {'dialogue': next((i for i,h in enumerate(headers) if h in DIALOGUE_HEADERS), None),
                         'role': next((i for i,h in enumerate(headers) if h in ROLE_HEADERS), None),
                         'scene': next((i for i,h in enumerate(headers) if h in SCENE_HEADERS), None),
                         'shots': [(i,h) for i,h in enumerate(headers) if h in SHOT_HEADERS],
                         'camera': [(i,h) for i,h in enumerate(headers) if h in {'拍摄方式', '拍摄说明', '镜头语言'}],
                         'columns': len(cells)}
                current_speaker = None
                current_synthetic = False
                omitted.add(number)
                continue
            if all(re.fullmatch(r':?-{2,}:?', c) for c in cells):
                continue
            if table and len(cells) == table['columns']:
                current_scope = 'row-' + str(number)
                if table['scene'] is not None and cells[table['scene']]:
                    current_scene = cells[table['scene']]
                role = _speaker(cells[table['role']], explicit=True) if table['role'] is not None else None
                current_speaker = role
                current_synthetic = False
                for column, heading in table['shots']:
                    add_shot(cells[column], number, heading)
                for column, heading in table['camera']:
                    add_shot_from_camera_notes(cells[column], number, heading)
                # A director row is not performance dialogue even in a speech table.
                if table['role'] is not None and (META_RE.match(cells[table['role']]) or SYNTHETIC_LABEL.fullmatch(cells[table['role']])):
                    if re.match(SYNTHETIC_NAME, cells[table['role']], re.I):
                        exclude(cells[table['dialogue']] if table['dialogue'] is not None else '', number)
                    omitted.add(number)
                    continue
                cell = cells[table['dialogue']] if table['dialogue'] is not None else ''
                for fragment in re.split(r'<br\s*/?>|\u2028', cell, flags=re.I):
                    parse(fragment, number, True, role)
                continue
            table = None
            current_speaker = None
            if len(cells) == 2 and _speaker(cells[0]):
                parse(cells[0] + '：' + cells[1], number)
            else:
                # Explicit labels are still usable in tables without a dialogue header.
                for cell in cells:
                    if KNOWN_RE.match(_plain(cell)):
                        for fragment in re.split(r'<br\s*/?>', cell, flags=re.I):
                            parse(fragment, number)
                omitted.add(number)
            continue
        table = None
        if line.startswith('#'):
            current_speaker = None
            omitted.add(number)
            continue
        parse(raw, number)

    # Keep distinct source requirements separate: sequential actions must not be
    # turned into an impossible single frame. Never truncate a scene's shot list.
    grouped = {}
    for part in shot_parts:
        key = ('scene', part['scene']) if part['scene'] else ('scope', part['_scope'])
        grouped.setdefault(key, []).append(part)
    shots = []
    for key, parts in grouped.items():
        unique = []
        for part in parts:
            compact = re.sub(r'\W', '', part['text'])
            duplicate = next((prior for prior in unique if compact and compact in re.sub(r'\W', '', prior['text'])), None)
            ref = {k: part[k] for k in ('sourceLine', 'sourceColumn', 'text')}
            if duplicate is not None:
                duplicate['sourceRefs'].append(ref)
            else:
                unique.append({**part, 'sourceRefs': [ref]})
        target_ids = [i + 1 for i,t in enumerate(targets)
                      if (t['scene'] == key[1] if key[0] == 'scene' else t['_scope'] == key[1])]
        for part in unique:
            shots.append({'id': 'shot-' + str(len(shots) + 1), 'scene': part['scene'],
                          'text': part['text'],
                          'sourceLine': part['sourceLine'], 'sourceColumn': part['sourceColumn'],
                          'targetIds': target_ids,
                          'sourceRefs': part['sourceRefs'],
                          'sequenceRequired': bool(re.search(r'先.+(?:再|然后)|然后|之后|下一秒|接着', part['text']))})
    for part in reviewed_shots:
        association = _associate_reviewed_shot(part, reviewed_sections, targets)
        shots.append({k: v for k, v in part.items() if not k.startswith('_')} |
                     association | {'id': 'shot-' + str(len(shots) + 1),
                                    'sequenceRequired': bool(re.search(r'先.+(?:再|然后)|然后|之后|下一秒|接着', part['text']))})
    for target in targets:
        target.pop('_scope', None)
    normalized = []
    for target in targets:
        speaker = target['speaker']
        if (target['dialogue'] and speaker not in ('闺蜜', '朋友')) or not KNOWN_RE.fullmatch(speaker):
            interaction = '互动' if target['dialogue'] and not re.search('闺蜜|朋友|互动', speaker) else ''
            normalized.append(f'角色意图：{speaker}{interaction}｜台词意图：{target["text"]}')
        else:
            normalized.append(f'{speaker}：{target["text"]}')
        if target['sourceName']:
            normalized.append('素材：' + target['sourceName'])
    warnings = []
    if not targets:
        warnings.append(NO_DIALOGUE if source.strip() else
            '请把含人物台词的剧本与素材一起导入，系统会自动整理；无需手动改格式。')
    elif omitted:
        warnings.append('已跳过标题、拍摄说明或无法确定为台词的内容；原文保留，可展开核对。')
    synthetic_count = sum(row['kind'] == 'synthetic_audio' for row in excluded)
    if synthetic_count:
        warnings.append(f'已识别 {synthetic_count} 处 AI／合成配音，单独保留供后期参考，不作为现场台词检索。')
    unanchored_count = sum(shot.get('associationStatus') == 'unanchored' for shot in shots)
    if unanchored_count:
        warnings.append(f'有 {unanchored_count} 条第二轨画面清单未能关联到明确章节台词，已标记待审，不会套用旧成片时间或任意插入。')
    return {'revision': REVISION, 'sourceText': source, 'normalizedText': '\n'.join(normalized),
            'targets': targets, 'count': len(targets), 'strictReady': bool(targets),
            'shotRequirements': shots, 'excludedContent': excluded,
            'warnings': warnings, 'omittedCount': len(omitted)}
