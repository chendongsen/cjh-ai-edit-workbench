"""Auditable conservative editing rules. This is NOT speaker identification."""
import math
import re
import unicodedata
from pathlib import Path

REVISION = 'editorial-3'
TRANSLATION = str.maketrans('廣東這個說後來夾腦飯餅腸蝦魚蓮槤燒鵝飽傳統點裡見過還開標',
                           '广东这个说后来夹脑饭饼肠虾鱼莲莲烧鹅饱传统点里见过还开标')
TRANSLATION.update(str.maketrans({'買':'买','稅':'税','紙':'纸','帶':'带','歡':'欢','幾':'几','塊':'块',
                                '麼':'么','麽':'么','為':'为','輕':'轻','覺':'觉','體':'体','愛':'爱',
                                '們':'们','會':'会','來':'来','講':'讲','裡':'里','從':'从','氣':'气',
                                '實':'实','嚴':'严','樣':'样','給':'给','頭':'头','應':'应','該':'该'}))


def norm(text):
    return re.sub(r'[^\w\u4e00-\u9fff]', '', unicodedata.normalize('NFKC', text).translate(TRANSLATION).lower())


# No duck=goose, tofu=brain or generic "点心" aliases: these were real false matches.
DISHES = {
    '虾饺': ['大虾饺', '虾饺'], '菠萝包': ['水牛奶菠萝包', '菠萝包'],
    '红米肠': ['红米肠', '红米肠粉'], '肠粉': ['肠粉'],
    '榴莲酥': ['榴莲酥', '天鹅酥'], '榴莲糖沙翁': ['榴莲糖沙翁', '糖沙翁'],
    '肉饼饭': ['咸蛋肉饼', '肉饼拌饭', '肉饼蒸', '肉饼饭', '拌饭'],
    '麻婆豆腐': ['麻婆豆腐', '马婆豆腐'], '脑花': ['脑花'],
    '鲍鱼': ['鲍鱼'], '大肠': ['大肠'], '豆芽': ['豆芽'],
    '清蒸鱼': ['清蒸鱼', '葱油蒸', '笋壳鱼', '蒸鱼'],
    '烧鹅': ['烧鹅'], '烤鸭': ['烤鸭', '烧鸭'], '皮蛋': ['皮蛋'],
}


def dish_tags(text):
    text = norm(text)
    found = {key for key, aliases in DISHES.items() if any(norm(a) in text for a in aliases)}
    if '红米肠' in found:
        found.discard('肠粉')
    return found


def person_visible(item):
    vision = item.get('vision', {})
    return vision.get('kind') == '人物' or bool(re.search(r'女士|女子|女性|男子|男士|男人|女人|主持人', vision.get('summary', '')))


def explicit_broll_folder(item):
    return bool(re.search(r'空镜|菜品|混剪素材|店内人流|路人', str(Path(item['source']).parent)))


def main_candidate(item):
    # Named performance clips can contain predominantly food; a single visual enum is not a gate.
    if not item.get('video') or not item.get('segments') or explicit_broll_folder(item):
        return False
    return person_visible(item)


def food_candidate(item):
    if not item.get('video') or item.get('duration', 0) < 2:
        return False
    name = Path(item['source']).stem
    parent = str(Path(item['source']).parent)
    if re.search(r'菜品|食物|food', parent, re.I):
        return True
    # Do not turn face-dominant reaction shots into dish inserts just because their title names food.
    return not person_visible(item) and (item.get('vision', {}).get('kind') == '食物' or
                                          bool(re.search(r'空镜|展现|特写', name) and dish_tags(name)))


def word_map(segment):
    text, mapping = '', []
    words = segment.get('words') or []
    for w in words:
        token = norm(w['text'])
        text += token
        mapping.extend([w] * len(token))
    return text, mapping


def clean_speech(item, usable, fps):
    """Remove explicit production commands only, preserving uncertainty and original timestamps."""
    removed, reviews, parts = [], [], []
    for s in sorted(item.get('segments', []), key=lambda x: x['start']):
        text, mapping = word_map(s)
        original = norm(s.get('text', ''))
        if not text or text != original:
            text, mapping = original, []
        if not text:
            continue
        ranges = []
        countdown = re.match(r'^(?:(?:ok|好|对|来))*[3三][2二][1一](?:开始)?', text)
        if countdown:
            ranges.append((0, countdown.end(), '开拍倒数'))
        if re.fullmatch(r'(?:cut|卡|再来一次|再拍一遍|重拍一遍|看镜头)', text):
            ranges.append((0, len(text), '明确拍摄口令'))
        # Strict command-only grammar avoids deleting a performance sentence mentioning "表情".
        if re.fullmatch(r'(?:(?:你)?(?:表情|标情)(?:管理|那个一点|那个)?|我(?:要)?拍(?:她|他|你|一下)?)+', text):
            ranges.append((0, len(text), '表情/摄像指导'))
        action = re.match(r'^(?:你)?再(?:夹|舀|咬)(?:一下|一口|一出)', text)
        if action:
            # Keep a talent reply in the SAME ASR segment when its boundary is explicit.
            boundary = re.search(r'这个是|这个真|这是|哇|好吃', text[action.end():])
            end = action.end() + boundary.start() if boundary else len(text)
            if end <= 16:
                ranges.append((0, end, '重复动作拍摄指导'))
        prompt = re.match(r'^(?:哦|嗯|好|那|没有)*(?:然后说|你就说|你可以这样说)', text)
        if prompt:
            ranges.append((0, prompt.end(), '提示词前缀；后续内容保留待审'))
        mask = [False] * len(text)
        local = []
        for a, b, reason in ranges:
            if (a > 0 or b < len(text)) and not mapping:
                reviews.append({'source': item['source'], 'assetId': item['assetId'], **s,
                                'reason': '混合句缺词级时间码，保留整句待审'})
                continue
            start = mapping[a]['start'] if mapping else s['start']
            end = mapping[b-1]['end'] if mapping else s['end']
            # A zero-width/invalid word time cannot justify deleting any audio.
            if end <= start:
                continue
            for k in range(a, b):
                mask[k] = True
            local.append((start, end))
            removed.append({'assetId': item['assetId'], 'source': item['source'],
                            'start': start, 'end': end, 'text': text[a:b], 'reason': reason,
                            'method': '转写语义规则+词级时间；非声纹身份识别', 'confirmed': False})
        if not any(mask):
            parts.append(dict(s))
        elif not all(mask):
            pos = 0
            while pos < len(text):
                if mask[pos]:
                    pos += 1
                    continue
                end = pos + 1
                while end < len(text) and not mask[end]:
                    end += 1
                start_time, end_time = mapping[pos]['start'], mapping[end-1]['end']
                # Never cut inside one token which straddles removed and retained text.
                if ((pos and mapping[pos] is mapping[pos-1]) or
                        (end < len(text) and mapping[end-1] is mapping[end])):
                    raise ValueError('指导语边界跨越同一词，请审核词级时间码')
                words = [w for w in s['words'] if w['start'] >= start_time and w['end'] <= end_time]
                parts.append(dict(s, start=start_time, end=end_time, text=''.join(w['text'] for w in words).strip(), words=words))
                pos = end
        if re.search(r'然后说|你就|你可以这样说|慢一点|慢点|你待会|你表[现現]|铲一勺|怎么说|说什么|你要跟我说|对对对|就那一件|心理斗争|放到你的|就不放|放这种|要不要给你|我也不知道|别拿|你先拿|都吃来吧', text):
            if not all(mask):
                reviews.append({'assetId': item['assetId'], 'source': item['source'],
                                'start': s['start'], 'end': s['end'], 'text': s['text'],
                                'reason': '可能含提示/示范或混合说话人，保留待听审'})
    merged = []
    for s in parts:
        start = max(0, round((s['start'] - .16) * fps) / fps)
        end = min(math.floor(usable * fps) / fps, round((s['end'] + .22) * fps) / fps)
        # No padding or merge may re-introduce a removed voice span.
        for cut in removed:
            if cut['end'] <= s['start'] + 1e-6:
                start = max(start, math.ceil(cut['end'] * fps) / fps)
            elif cut['start'] >= s['end'] - 1e-6:
                end = min(end, math.floor(cut['start'] * fps) / fps)
        if end <= start:
            continue
        barrier = merged and any(c['start'] < start and c['end'] > merged[-1]['end'] for c in removed)
        words = [dict(w) for w in s.get('words', [])]
        if merged and not barrier and start - merged[-1]['end'] <= .5:
            merged[-1]['end'] = max(merged[-1]['end'], end)
            merged[-1]['text'] += ' ' + s['text']
            merged[-1]['words'].extend(words)
        else:
            merged.append({'assetId': item['assetId'], 'source': item['source'], 'start': start,
                           'end': end, 'text': s['text'], 'words': words, 'role': '待确认'})
    return merged, removed, reviews
