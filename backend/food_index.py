"""Local, frame-specific food index. Original ASR/vision evidence is never overwritten."""
import base64
import copy
import hashlib
import json
import telemetry
import urllib.request
from pathlib import Path
from common import DATA, OLLAMA_URL, read, write, local_urlopen
from editorial import DISHES, food_candidate, dish_tags

INDEX_REVISION = 'food-shots-2'
LABELS = list(DISHES) + ['烧味（鸭鹅不确定）', '其他食物', '未见清晰食物']


def refine_food_index(items, model, output):
    enriched = copy.deepcopy(items)
    cache = DATA / 'food-index'
    cache.mkdir(exist_ok=True)
    candidates = [x for x in enriched if food_candidate(x)]
    reviews = read(DATA / 'food-reviews.json', {})
    for index, item in enumerate(candidates):
        frames = item.get('frames', [])
        if not frames or not model:
            continue
        if 'cloud' in model.lower():
            raise ValueError('食物二次索引只允许本地模型')
        fingerprint = hashlib.sha256(json.dumps([INDEX_REVISION, model, item.get('fingerprint'),
                                  [(f['at'], hashlib.sha256((DATA/f['file']).read_bytes()).hexdigest()) for f in frames]],
                                  sort_keys=True).encode()).hexdigest()
        cached = read(cache / (fingerprint + '.json'))
        telemetry.event('cache', 'food_hit' if cached else 'food_miss', count=1)
        print(f'食物逐帧复核 {index+1}/{len(candidates)}：{Path(item["source"]).name}' + ('（已缓存）' if cached else ''), flush=True)
        if not cached:
            schema = {'type': 'object', 'properties': {'frames': {'type': 'array', 'minItems': len(frames), 'maxItems': len(frames),
                      'items': {'type': 'object', 'properties': {'index': {'type': 'integer'},
                      'label': {'type': 'string', 'enum': LABELS}, 'visible': {'type': 'boolean'},
                      'confidence': {'type': 'number'}, 'description': {'type': 'string'}},
                      'required': ['index', 'label', 'visible', 'confidence', 'description']}}}, 'required': ['frames']}
            prompt = ('按顺序逐张判断这些视频抽帧，只描述当前帧前景主体，忽略背景小盘子。每张返回index(从0开始)、label、visible(食物已出现且清晰可辨)、confidence(0到1)、description(15字内客观描述)。'
                      '桌面空白、只有盖子、尚未端上的盘子选择未见清晰食物。标签从给定枚举选最具体的一项。'
                      '不要把虾饺说成蒸饺，不要把黄色格纹圆面包漏成普通糕点。天鹅造型酥可选榴莲酥，但内馅无法仅凭外观证实。'
                      '鸭鹅外观无法区别时选烧味（鸭鹅不确定）。脑花与豆腐无法区分时不要猜脑花。米饭上圆形肉饼与蛋黄选肉饼饭。'
                      '同一素材的三帧可以有不同菜，不能把最后一帧的菜名分配给前面所有帧。不要推测人物身份。')
            payload = {'model': model, 'stream': False, 'format': schema,
                       'options': {'temperature': 0, 'num_ctx': 8192, 'num_predict': 768},
                       'messages': [{'role': 'user', 'content': prompt,
                                     'images': [base64.b64encode((DATA/f['file']).read_bytes()).decode() for f in frames]}]}
            req = urllib.request.Request(OLLAMA_URL + '/api/chat', data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
            with local_urlopen(req, timeout=600) as response:
                result = json.loads(telemetry.ollama(json.loads(response.read()))['message']['content'])
            rows = result.get('frames', [])
            if sorted(r.get('index', -1) for r in rows) != list(range(len(frames))):
                raise ValueError('食物索引帧序号无效，请重试：' + item['source'])
            for r in rows:
                if r.get('label') not in LABELS or not isinstance(r.get('visible'), bool) or not 0 <= r.get('confidence', -1) <= 1:
                    raise ValueError('食物索引结构无效：' + item['source'])
            cached = {'revision': INDEX_REVISION, 'fingerprint': fingerprint, 'frames': rows}
            write(cache / (fingerprint + '.json'), cached)
        shots = []
        named = dish_tags(Path(item['source']).stem)
        for row in cached['frames']:
            f = frames[row['index']]
            if not row['visible'] or row['label'] == '未见清晰食物' or row['confidence'] < .65:
                continue
            tags = {row['label']} if row['label'] in DISHES else set()
            # Named dish close-ups are user's metadata, not a model's confirmation of ingredients.
            if named:
                tags |= named
            if tags:
                shots.append({'at': f['at'], 'frame': f['file'], 'tags': sorted(tags),
                              'confidence': row['confidence'], 'label': row['label'],
                              'description': row.get('description', ''),
                              'basis': '本地逐帧视觉' + (' + 素材命名' if named else '')})
        item['foodShots'] = shots
        item['foodIndex'] = cached
        review = reviews.get(item['assetId'], {})
        if review.get('indexFingerprint') == fingerprint:
            # Reviewed frame annotations are separate from model results and expire on any index change.
            item['foodShots'] = []
            for row in review['frames']:
                frame = frames[row['index']]
                tags = [t for t in row['tags'] if t in DISHES]
                if tags:
                    item['foodShots'].append({'at': frame['at'], 'frame': frame['file'], 'tags': tags,
                                             'confidence': 1, 'basis': '抽帧人工核对标注（非全段审片）',
                                             'reviewedBy': review['reviewedBy'], 'reason': row['reason']})
            item['foodReview'] = review
        # Bound a visible sample by the next observation; gaps/label changes are not assumed safe.
        for shot in item['foodShots']:
            following = [f for f in frames if f['at'] > shot['at']]
            if following:
                next_at = min(f['at'] for f in following)
                shot['nextAt'] = next_at
                shot['nextTags'] = next((s['tags'] for s in item['foodShots'] if s['at'] == next_at), [])
    write(output / 'food-index.json', {'revision': INDEX_REVISION, 'candidateCount': len(candidates),
                                    'items': [x for x in enriched if food_candidate(x)]})
    return enriched
