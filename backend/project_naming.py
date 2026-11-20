"""Stable local project names derived only from the submitted asset batch."""
import hashlib
import json
import math
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

REVISION = 'project-naming-1'
TIMEZONE = 'Asia/Shanghai'
_GENERATED = re.compile(r'(?:[0-9a-f]{24,64}|[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})$', re.I)
_BOUNDARIES = {'users', 'user', 'home', 'volumes', 'downloads', 'documents', 'desktop', 'movies',
               'library', 'private', 'var', 'tmp', 'temp', 'folders', 'cache', 'caches',
               'cjh', 'ai-edit-workbench', '.local', '.loc', 'imports', 'files', 'parts', 'jobs',
               '下载', '文档', '桌面'}
_GROUPS = {'素材', '原素材', '原始素材', '视频', '音频', '图片', '人物', '食物', '空镜',
           'media', 'assets', 'video', 'videos', 'audio', 'images', 'photos', 'raw', 'footage'}


def _clean(value):
    if not isinstance(value, str):
        return ''
    value = unicodedata.normalize('NFC', value)
    value = ''.join(c if unicodedata.category(c)[0] in 'LMN' or c in ' ._-·（）()' else '_'
                    for c in value)
    return re.sub(r'[\s_]+', '_', value).strip(' ._-')


def _label(value):
    value = _clean(value)
    if (not value or value.casefold() in _BOUNDARIES or _GENERATED.fullmatch(value) or
            re.match(r'^(?:我的第一个剪辑项目|未命名项目|新建项目|Untitled(?:_Project)?)(?:$|[_（(])', value, re.I)):
        return ''
    return value


def _created(value):
    if value is None:
        value = datetime.now(timezone.utc)
    elif type(value) in (int, float):
        if not math.isfinite(value):
            raise ValueError('任务创建时间必须是有效 epoch 或 ISO 时间')
        try:
            value = datetime.fromtimestamp(value, timezone.utc)
        except (ValueError, OverflowError, OSError) as error:
            raise ValueError('任务创建时间超出可用范围') from error
    elif isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.strip().replace('Z', '+00:00'))
        except ValueError as error:
            raise ValueError('任务创建时间必须是有效 epoch 或 ISO 时间') from error
    if not isinstance(value, datetime):
        raise ValueError('任务创建时间必须是有效 epoch 或 ISO 时间')
    zone = ZoneInfo(TIMEZONE)
    # Naive ISO values mean Shanghai local time, never the host's current zone.
    return (value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone))


def _asset_path(asset):
    value = asset.get('path')
    return Path(os.path.normpath(os.path.expanduser(value))) if isinstance(value, str) and value.strip() else None


def _copied_folder(paths):
    """Read only manifests that belong to the explicitly submitted asset paths."""
    labels, manifests = [], {}
    for path in paths:
        if path is None:
            return ''
        parts = path.parts
        marker = next((i for i in range(len(parts)-4)
                       if parts[i] in ('.local', '.loc') and parts[i+1] == 'imports'
                       and _GENERATED.fullmatch(parts[i+2]) and parts[i+3] == 'files'), None)
        if marker is None:
            return ''
        manifest = Path(*parts[:marker+3]) / 'manifest.json'
        if manifest not in manifests:
            try:
                if manifest.is_symlink():
                    return ''
                with manifest.open('rb') as stream:
                    raw = stream.read(2_000_001)
                if len(raw) > 2_000_000:
                    return ''
                data = json.loads(raw)
                rows = data.get('files') if isinstance(data, dict) else None
                if not isinstance(rows, list):
                    return ''
                manifests[manifest] = {unicodedata.normalize('NFC', row['path']) for row in rows
                    if isinstance(row, dict) and isinstance(row.get('path'), str)}
            except (OSError, ValueError, UnicodeError):
                return ''
        relative = parts[marker+4:]
        if (len(relative) < 2 or unicodedata.normalize('NFC', '/'.join(relative)) not in manifests[manifest]):
            return ''
        label = _label(relative[0])
        if not label:
            return ''
        labels.append(label)
    return labels[0] if labels and len(set(labels)) == 1 else ''


def _common_folder(paths):
    if len(paths) < 2 or any(path is None for path in paths):
        return ''
    try:
        parent = Path(os.path.commonpath([str(path.parent) for path in paths]))
    except ValueError:
        return ''
    while parent.name:
        name = parent.name.casefold()
        # Never climb out of generic roots and accidentally use a user/account name.
        if (name in _BOUNDARIES or _GENERATED.fullmatch(parent.name) or
                parent.parent.name.casefold() in ('users', 'home', 'volumes')):
            return ''
        if name not in _GROUPS:
            return _label(parent.name)
        parent = parent.parent
    return ''


def make_project_naming(assets, job_id, created=None, fallback='素材'):
    """Return one persisted name; retries must reuse the same job_id/created.

    Explicit batch labels take precedence. A single unique file uses its stem;
    multiple files can use verified upload-folder provenance. Multi-root batches use a
    deterministic first stem, never an account, workbench or UUID directory.
    """
    if not isinstance(assets, (list, tuple)) or any(not isinstance(a, dict) for a in assets):
        raise ValueError('命名素材应为本任务的素材列表')
    if not isinstance(job_id, str) or not job_id.strip():
        raise ValueError('自动命名需要有效任务 ID')
    when = _created(created)
    # A plan may have many dialogue clips from the same source. They are one
    # source for naming, not "N materials"; never modify the caller's assets.
    unique, label_sets = {}, {}
    for index, asset in enumerate(assets):
        path = _asset_path(asset)
        key = ('path', unicodedata.normalize('NFC', str(path))) if path is not None else ('row', index)
        if key not in unique:
            unique[key] = dict(asset)
            label_sets[key] = set()
        label = _label(asset.get('batchLabel'))
        if label:
            label_sets[key].add(label)
    assets = list(unique.values())
    for key, asset in unique.items():
        asset['batchLabel'] = next(iter(label_sets[key])) if len(label_sets[key]) == 1 else ''
    paths = [_asset_path(asset) for asset in assets]
    labels = [_label(asset.get('batchLabel')) for asset in assets]
    label = labels[0] if labels and all(labels) and len(set(labels)) == 1 else ''
    stems = sorted(filter(None, (_label(path.stem if path is not None else
                                       Path(str(asset.get('name') or '')).stem)
                                 for asset, path in zip(assets, paths))), key=lambda s: (s.casefold(), s))
    if not label and len(assets) == 1 and stems:
        label = stems[0]
    label = label or _copied_folder(paths)
    if not label:
        label = _common_folder(paths)
    if not label:
        label = (stems[0] + (f'等{len(assets)}个素材' if len(assets) > 1 else '')) if stems else (_label(fallback) or '素材')
    # Hash the entire task ID, so jobs sharing a UUID prefix still differ.
    short_id = hashlib.sha256(job_id.strip().encode('utf-8')).hexdigest()[:12]
    prefix, suffix = when.strftime('%Y%m%d_'), when.strftime('_粗剪_%H%M%S_') + short_id
    while len(prefix + label + suffix) > 100 or len((prefix + label + suffix).encode('utf-8')) > 240:
        label = label[:-1]
    label = label.rstrip(' ._-') or '素材'
    return {'projectName': prefix + label + suffix, 'sourceLabel': label,
            'createdAt': when.isoformat(), 'timezone': TIMEZONE, 'revision': REVISION}
