"""Explicit public model download. Never runs during editing or uploads media."""
import hashlib
import os
import urllib.request
from common import DATA, write

CLIP_REVISION = '36e679e65c2a2fead755ae21162091293ad37834'


def run():
    os.environ.setdefault('HF_HUB_DISABLE_XET', '1')
    from huggingface_hub import snapshot_download
    root = DATA/'models/vision-combo'
    root.mkdir(parents=True, exist_ok=True)
    print('下载官方 Chinese-CLIP ViT-B/16（约 753 MB）', flush=True)
    snapshot_download('OFA-Sys/chinese-clip-vit-base-patch16', revision=CLIP_REVISION,
        local_dir=str(root/'chinese-clip'), max_workers=2,
        allow_patterns=['config.json', 'preprocessor_config.json', 'vocab.txt', 'pytorch_model.bin', 'README.md'])
    with (root/'chinese-clip/pytorch_model.bin').open('rb') as file:
        if hashlib.file_digest(file, 'sha256').hexdigest() != '7b7b583c210c867410bc6bdb8a55fe14eec62999e0a9ea31ff222dc501f9cfbe':
            raise ValueError('Chinese-CLIP 权重校验失败')
    url = 'https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/face_landmarker.task'
    target = root/'face_landmarker.task'
    if not target.exists():
        print('下载官方 Face Landmarker（约 3.8 MB）', flush=True)
        with urllib.request.urlopen(url, timeout=120) as response:
            payload = response.read()
        if len(payload) != 3758596:
            raise ValueError('Face Landmarker 下载大小不符')
        target.write_bytes(payload)
    write(root/'provenance.json', {'clipRepository':'OFA-Sys/chinese-clip-vit-base-patch16',
        'clipRevision':CLIP_REVISION, 'faceURL':url,
        'faceSHA256':hashlib.sha256(target.read_bytes()).hexdigest(),
        'distributionReview':'本机验证；模型权重许可需在对外打包前另行核查'})
    print('模型已下载，尚未自动启用组合流程', flush=True)


if __name__ == '__main__': run()
