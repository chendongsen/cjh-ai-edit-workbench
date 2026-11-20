"""Local retrieval + visible face-motion protection. Neither identifies people.

CLIP scores only rank candidates; Qwen remains mandatory for actual inserts.
No model downloads or outbound inference occur here. Missing models fall back
explicitly; a failed face check never authorizes covering the performance.
"""
import hashlib
import json
import math
import os
import subprocess
from pathlib import Path
from common import DATA, read, write
from editorial import dish_tags, food_candidate, explicit_broll_folder

REVISION = 'vision-combo-1'
MODEL_ROOT = DATA/'models/vision-combo'
CONFIG = DATA/'vision-combo.json'
NEGATIVES = {'菜单':['餐厅墙上的装饰画','餐厅的装饰墙面'],
             '购物袋':['城市广场远景与远处的小行人','空旷的城市街道远景']}


def contrast_allowed(theme, positive, negatives):
    return theme not in NEGATIVES or not negatives or positive >= max(negatives)+.02


def enabled(request):
    return request.get('visionCombo', read(CONFIG, {}).get('enabled', False)) is True


def fingerprint(path):
    p = Path(path); s = p.stat()
    return [str(p.resolve()), s.st_size, s.st_mtime_ns]


def face_decision(rows):
    """Conservative visible-motion heuristic, NOT an emotion or humor classifier."""
    if not rows or any(r['faces'] != 1 for r in rows):
        return False, '人物不可见或多人同框，保留主画面'
    previous = None
    for row in rows:
        scores = row['scores']
        if min(scores.get('mouthSmileLeft', 0), scores.get('mouthSmileRight', 0)) >= .65:
            return False, '嘴角上扬幅度明显，保留可能的笑容'
        # Ordinary speech mouth opening and blinking are deliberately excluded.
        keys = ('browInnerUp', 'eyeWideLeft', 'eyeWideRight')
        if previous and max(abs(scores.get(k, 0)-previous.get(k, 0)) for k in keys) >= .35:
            return False, '眉眼变化明显，保留人物反应'
        previous = scores
    return True, '抽样未发现强表情变化（非无反应保证）'


def face_ranges(rows, start, end):
    """Localize observed changes; do not sacrifice an entire sentence for one frame."""
    spans=[];previous=None
    for row in rows:
        scores=row['scores']; t=row['at']
        uncertain=row['faces']!=1
        smile=min(scores.get('mouthSmileLeft',0),scores.get('mouthSmileRight',0))>=.65
        motion=previous is not None and previous['faces']==1 and row['faces']==1 and max(
            abs(scores.get(k,0)-previous['scores'].get(k,0))
            for k in ('browInnerUp','eyeWideLeft','eyeWideRight'))>=.35
        if uncertain or smile or motion:
            a=previous['at'] if motion else t
            spans.append([max(start,a-.25),min(end,t+.3)])
        previous=row
    merged=[]
    for a,b in spans:
        if merged and a<=merged[-1][1]:merged[-1][1]=max(b,merged[-1][1])
        else:merged.append([a,b])
    return merged


class Ensemble:
    def __init__(self):
        import torch
        from transformers import ChineseCLIPModel, ChineseCLIPProcessor
        # Cache location must be writable in packaged installs too.
        os.environ.setdefault('MPLCONFIGDIR', str(DATA/'matplotlib-cache'))
        import mediapipe as mp
        self.torch, self.mp = torch, mp
        self.device = 'mps' if torch.backends.mps.is_available() else 'cpu'
        self.model = ChineseCLIPModel.from_pretrained(str(MODEL_ROOT/'chinese-clip'),
            local_files_only=True, weights_only=True).eval().to(self.device)
        self.processor = ChineseCLIPProcessor.from_pretrained(str(MODEL_ROOT/'chinese-clip'), local_files_only=True, use_fast=False)
        self.face = mp.tasks.vision.FaceLandmarker.create_from_options(mp.tasks.vision.FaceLandmarkerOptions(
            base_options=mp.tasks.BaseOptions(model_asset_path=str(MODEL_ROOT/'face_landmarker.task'),
                delegate=mp.tasks.BaseOptions.Delegate.CPU),
            running_mode=mp.tasks.vision.RunningMode.IMAGE, num_faces=3,
            output_face_blendshapes=True))
        self.model_key = fingerprint(MODEL_ROOT/'chinese-clip/pytorch_model.bin')
        self.face_key = fingerprint(MODEL_ROOT/'face_landmarker.task')
        self.vectors, self.queries = {}, {}
        self.report = {'revision':REVISION, 'status':'active', 'device':self.device,
            'retrievalModel':'Chinese-CLIP ViT-B/16', 'faceModel':'MediaPipe Face Landmarker',
            'rankedBlocks':0, 'retrievalCandidates':0, 'faceChecks':0, 'faceProtected':0,
            'warnings':[], 'scoresAreProbabilities':False}

    def close(self):
        self.face.close()

    def embed(self, frame):
        from PIL import Image
        key = hashlib.sha256(json.dumps([REVISION,self.model_key,fingerprint(frame)]).encode()).hexdigest()
        if key in self.vectors: return self.vectors[key]
        root = DATA/'clip-cache'; root.mkdir(exist_ok=True)
        cached = read(root/(key+'.json'))
        if cached:
            vec = self.torch.tensor(cached['vector'], device=self.device)
        else:
            with Image.open(frame) as im:
                inputs = self.processor(images=im.convert('RGB'), return_tensors='pt').to(self.device)
            with self.torch.inference_mode():
                vec = self.model.get_image_features(**inputs)[0]
                vec = vec / vec.norm()
            write(root/(key+'.json'), {'vector':vec.cpu().tolist()})
        self.vectors[key] = vec
        return vec

    def text(self, query):
        if query not in self.queries:
            inputs = self.processor(text=[query], return_tensors='pt', padding=True, truncation=True, max_length=64).to(self.device)
            with self.torch.inference_mode():
                # Chinese-CLIP uses the unpooled CLS state. Transformers 4.57.1
                # get_text_features incorrectly asks its disabled pooler for it.
                hidden = self.model.text_model(**inputs).last_hidden_state[:,0,:]
                vec = self.model.text_projection(hidden)[0]
                self.queries[query] = vec / vec.norm()
        return self.queries[query]

    def rank(self, block, options, items, occupied, fps):
        from broll import same_scene, duration, MIN_SHOT
        tags, generic = block['tags'], block['generic']
        food_themes = sorted(tags) if tags else ['同场餐食'] if generic else []
        # A dialogue can name a dish while asking for a menu/cooking/serving
        # shot. Give every proposed theme its own query; never score a kitchen
        # action only against a dish photo or lose menu contrast to a KeyError.
        themes = sorted(set(food_themes) | set(block['topics']) | {o['dish'] for o in options})
        if not themes: return options
        texts = {t:self.text('一张'+('食物特写' if t=='同场餐食' else t)+'的照片') for t in themes}
        result = [dict(o) for o in options]; known = {(o['item']['assetId'],o['start']) for o in result}
        # Recover visually plausible unlabelled shots, but never cross a chapter,
        # explicit conflicting dish name, reviewed exclusion, or performance take.
        for candidate in items:
            if candidate['assetId']==block['item']['assetId'] or not same_scene(block['item'],candidate):continue
            if candidate.get('foodReview'):continue
            is_food = food_candidate(candidate)
            if tags or generic:
                if not is_food:continue
                named = dish_tags(Path(candidate['source']).stem)
                if tags and named and not tags.intersection(named):continue
                candidate_themes = food_themes
            elif not explicit_broll_folder(candidate) or candidate.get('vision',{}).get('kind')!='环境':continue
            else: candidate_themes = block['topics']
            if not candidate_themes:continue
            frames = sorted(candidate.get('frames',[]), key=lambda f:f['at'])
            for i,frame in enumerate(frames):
                start = math.ceil(frame['at']*fps)/fps
                maximum = min(duration(candidate), frames[i+1]['at'] if i+1<len(frames) else duration(candidate))
                if maximum-start<MIN_SHOT or (candidate['assetId'],start) in known:continue
                vector = self.embed(DATA/frame['file'])
                theme, score = max(((t,float(vector @ texts[t])) for t in candidate_themes), key=lambda pair:pair[1])
                result.append(dict(item=candidate,start=start,maximum=maximum,dish=theme,
                    brollKind='food' if tags or generic else 'environment', confidence=0,
                    retrievalScore=score,frame=frame['file'],
                    basis='Chinese-CLIP 检索候选（不是菜名确认，需 Qwen 首中尾复核）'))
        for option in result:
            if 'retrievalScore' not in option and option.get('frame'):
                vector = self.embed(DATA/option['frame'])
                option['retrievalScore'] = float(vector @ texts[option['dish']])
        filtered=[]
        for option in result:
            theme=option['dish']
            if theme in NEGATIVES and option.get('frame'):
                vector=self.embed(DATA/option['frame'])
                negatives=[float(vector @ self.text('一张'+label+'的照片')) for label in NEGATIVES[theme]]
                # Fine-grained environment inserts require contrastive evidence;
                # a high general restaurant score must not turn wall art into menus.
                positive=float(vector @ texts[theme])
                if not contrast_allowed(theme,positive,negatives):
                    self.report['contrastRejected']=self.report.get('contrastRejected',0)+1
                    continue
            filtered.append(option)
        result=filtered
        self.report['rankedBlocks'] += 1
        self.report['retrievalCandidates'] += len(result)
        # Prefer unused sources, then semantic fit. Scores never pass verification.
        return sorted(result,key=lambda o:(len(occupied.get(o['item']['assetId'],[])),
            -o.get('retrievalScore',-1),-o['confidence'],o['start']))

    def check_face(self, source, start, end):
        import imageio_ffmpeg
        # 4 Hz across the proposed overlay, including its end; not merely 3 frames.
        times = sorted({round(start+i*.25,4) for i in range(math.ceil((end-start)/.25))}
                       | {round(max(start,end-1/30),4)})
        key = hashlib.sha256(json.dumps([REVISION,self.face_key,fingerprint(source),times]).encode()).hexdigest()
        root = DATA/'face-motion-cache'/key; root.mkdir(parents=True,exist_ok=True)
        evidence = read(root/'result.json')
        if evidence is None:
            rows=[]
            for i,t in enumerate(times):
                frame=root/f'{i}.jpg'
                if not frame.exists():
                    subprocess.run([imageio_ffmpeg.get_ffmpeg_exe(),'-v','error','-nostdin','-y','-ss',str(t),
                        '-i',source,'-frames:v','1','-vf','scale=480:640:force_original_aspect_ratio=decrease',
                        '-threads','1',str(frame)],check=True,capture_output=True,timeout=45)
                result=self.face.detect(self.mp.Image.create_from_file(str(frame)))
                scores={c.category_name:float(c.score) for c in result.face_blendshapes[0]} if len(result.face_blendshapes)==1 else {}
                rows.append({'at':t,'faces':len(result.face_landmarks),'scores':scores,'file':str(frame.relative_to(DATA))})
            evidence={'rows':rows,'method':REVISION,'sampleHz':4}
            write(root/'result.json',evidence)
        passed,reason=face_decision(evidence['rows'])
        self.report['faceChecks']+=1
        self.report['faceProtected']+=int(not passed)
        return passed,dict(evidence,reason=reason,passed=passed,
                           protectedRanges=face_ranges(evidence['rows'],start,end))


def create(request):
    if not enabled(request): return None, {'status':'disabled'}
    try:
        engine=Ensemble()
        return engine,engine.report
    except Exception as error:
        return None, {'status':'fallback','warnings':['组合视觉未启动，使用原有 Qwen 多帧流程：'+str(error)]}
