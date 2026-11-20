"""Unified local folder/script intake and bounded, authenticated browser copies."""
import json
import os
import re
import shutil
import threading
import unicodedata
import uuid
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path, PurePosixPath
from common import DATA, read, write
from script_normalize import normalize_script, DIALOGUE_HEADERS

MEDIA = {'.mov', '.mp4', '.mkv', '.m4v', '.wav', '.mp3', '.m4a', '.aac', '.flac', '.jpg', '.jpeg', '.png'}
SCRIPTS = {'.docx', '.txt', '.md'}
CHUNK = 8 * 1024**2
SCRIPT_LIMIT = 10 * 1024**2
LOCK = threading.RLock()


def relative(value):
    if not isinstance(value, str) or not value or len(value) > 1000 or '\\' in value or ':' in value or any(ord(c)<32 for c in value):
        raise ValueError('文件相对路径无效')
    parts = value.split('/')
    if len(parts)>50 or any(x in ('', '.', '..') or x.startswith(('.', '~$')) for x in parts):
        raise ValueError('不允许隐藏文件或跨目录路径')
    return str(PurePosixPath(value))


def script_text(path):
    path = Path(path)
    if path.stat().st_size > SCRIPT_LIMIT:
        raise ValueError('脚本超过 10 MB，请另存为较小的 DOCX/TXT/MD')
    if path.suffix.lower() == '.docx':
        with zipfile.ZipFile(path) as doc:
            info = doc.getinfo('word/document.xml')
            if info.file_size > 20*1024**2:
                raise ValueError('Word 正文解压后过大')
            xml = doc.read(info).decode('utf-8-sig')
        if '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
            raise ValueError('不接受含实体声明的 Word 文档')
        root = ET.fromstring(xml)
        ns = '{http://schemas.openxmlformats.org/wordprocessingml/2006/main}'
        def paragraph(p):
            return ''.join((n.text or '') if n.tag == ns+'t' else '\t' if n.tag == ns+'tab' else '\n'
                           for n in p.iter() if n.tag in (ns+'t', ns+'tab', ns+'br')).strip()
        body = root.find(ns+'body')
        lines = []
        for child in body if body is not None else []:
            if child.tag == ns+'p':
                lines.append(paragraph(child))
            elif child.tag == ns+'tbl':
                first_row = child.find(ns+'tr')
                table_headers = [''.join(paragraph(p) for p in cell.findall('.//'+ns+'p')).strip()
                                 for cell in first_row.findall(ns+'tc')] if first_row is not None else []
                preserve_columns = any(header in DIALOGUE_HEADERS for header in table_headers)
                for row in child.findall(ns+'tr'):
                    cells = ['\n'.join(paragraph(p) for p in cell.findall('.//'+ns+'p')).strip() for cell in row.findall(ns+'tc')]
                    if not preserve_columns and len(cells)==2 and re.fullmatch(r'达人|达人|闺蜜|朋友|编导|主持人', cells[0]):
                        lines.append(cells[0]+'：'+cells[1])
                    else:
                        # Keep column boundaries: a script's shot notes must not
                        # become dialogue merely because they followed a speech cell.
                        lines.append('\t'.join(c.replace('\n', '<br>') for c in cells))
                lines.append('')
        text = '\n'.join(lines)
    else:
        raw = path.read_bytes()
        encodings = ['utf-16'] if raw.startswith((b'\xff\xfe', b'\xfe\xff')) else ['utf-8-sig', 'gb18030']
        for encoding in encodings:
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                pass
        else:
            raise ValueError('脚本文字编码无法识别，请另存为 UTF-8')
    text = text.replace('\r\n', '\n').replace('\r', '\n').strip()
    if not text or '\x00' in text:
        raise ValueError('未提取到可用文字；图片/扫描件暂不支持')
    if len(text) > 100000:
        raise ValueError('脚本超过 10 万字，请缩小范围')
    return text


def scan(paths, register):
    if not isinstance(paths, list) or not 1<=len(paths)<=20 or any(not isinstance(p, str) for p in paths):
        raise ValueError('请选择 1～20 个文件或文件夹路径')
    files, seen, warnings, skipped = [], set(), [], 0
    def add(path, base):
        nonlocal skipped
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(base):
            skipped += 1
            return
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            if resolved.suffix.lower() in MEDIA|SCRIPTS:
                files.append(resolved)
                if len(files)>1020:
                    raise ValueError('本批最多 1000 个素材和 20 份脚本')
            else:
                skipped += 1
    visited = 0
    for raw in paths:
        root = Path(raw).expanduser().resolve()
        if root.is_dir():
            for parent, dirs, names in os.walk(root, followlinks=False):
                dirs[:] = sorted(d for d in dirs if not d.startswith('.') and not (Path(parent)/d).is_symlink())
                if len(Path(parent).relative_to(root).parts)>50:
                    raise ValueError('文件夹嵌套过深，请选择更小的目录')
                for name in sorted(names):
                    visited += 1
                    if visited>100000:
                        raise ValueError('文件夹内容过多，请选择本次拍摄目录')
                    if name.startswith(('.', '~$')):
                        skipped += 1
                        continue
                    add(Path(parent)/name, root)
        elif root.is_file():
            add(root, root.parent)
        else:
            raise ValueError('路径不存在：'+str(root))
    media = [p for p in files if p.suffix.lower() in MEDIA]
    documents = [p for p in files if p.suffix.lower() in SCRIPTS]
    if len(media)>1000 or len(documents)>20:
        raise ValueError('本批最多 1000 个素材和 20 份脚本')
    scripts=[]
    for path in documents:
        try:
            text=script_text(path)
            scripts.append({'id':str(uuid.uuid4()), 'name':path.name, 'path':str(path), 'text':text,
                            'preparation':normalize_script(text)})
        except Exception as error:
            warnings.append(path.name+'：'+str(error))
    preferred = [s for s in scripts if re.search(r'脚本|文稿|script', s['name'], re.I)]
    chosen = preferred[0] if len(preferred)==1 else scripts[0] if len(scripts)==1 else None
    if scripts and not chosen:
        warnings.append('发现多份脚本，请在下拉框选择本次使用的版本；不会自动混合多稿。')
    if not scripts:
        warnings.append('本批未找到可读脚本；新批次不会沿用旧脚本，可按口播整理或手动填写。')
    if not media:
        warnings.append('本次没有新素材；若只补充脚本，会保留已读取的素材。')
    assets=register([str(p) for p in media]) if media else []
    return {'assets':assets, 'scripts':scripts, 'selectedScriptId':chosen['id'] if chosen else None,
            'warnings':warnings, 'skipped':skipped, 'mode':'path'}


def folder(identifier):
    if not isinstance(identifier, str) or not re.fullmatch(r'[a-f0-9]{32}', identifier):
        raise ValueError('导入批次无效')
    return DATA/'imports'/identifier


def begin(body):
    rows=body.get('files')
    if not isinstance(rows, list) or not 1<=len(rows)<=1020:
        raise ValueError('请选择有效文件，本批最多 1020 个文件')
    names=set(); files=[]; total=0; media=0; scripts=0
    for row in rows:
        path=relative(row.get('path')); size=row.get('size')
        suffix=Path(path).suffix.lower()
        key=unicodedata.normalize('NFC',path).casefold()
        if key in names:
            raise ValueError('同一批存在重名路径，请分别放入不同子文件夹：'+path)
        names.add(key)
        if suffix not in MEDIA|SCRIPTS or type(size) is not int or not 0<size<=200*1024**3:
            raise ValueError('不支持的文件类型或大小：'+path)
        if suffix in SCRIPTS and size>SCRIPT_LIMIT:
            raise ValueError('脚本不得超过 10 MB：'+path)
        media+=suffix in MEDIA; scripts+=suffix in SCRIPTS; total+=size
        files.append({'path':path,'size':size})
    if media>1000 or scripts>20:
        raise ValueError('本批最多 1000 个素材和 20 份脚本')
    if total > shutil.disk_usage(DATA).free-2*1024**3:
        raise ValueError('磁盘空间不足以保存本机导入副本，请使用路径读取（不复制）')
    identifier=uuid.uuid4().hex; root=folder(identifier)
    (root/'parts').mkdir(parents=True, mode=0o700)
    (root/'files').mkdir(mode=0o700)
    write(root/'manifest.json', {'files':files,'status':'copying','totalBytes':total})
    return {'id':identifier,'chunkBytes':CHUNK,'totalBytes':total}


def offsets(identifier):
    root=folder(identifier); manifest=read(root/'manifest.json')
    if not manifest:
        raise ValueError('导入批次不存在')
    return {'offsets':[(root/'parts'/str(i)).stat().st_size if (root/'parts'/str(i)).exists()
                       else row['size'] if (root/'files'/row['path']).exists() else 0
                       for i,row in enumerate(manifest['files'])], 'status':manifest['status']}


def chunk(identifier, index, offset, data):
    with LOCK:
        root=folder(identifier); manifest=read(root/'manifest.json')
        if not manifest or manifest['status']!='copying' or not 0<=index<len(manifest['files']):
            raise ValueError('批次已结束或文件序号无效')
        row=manifest['files'][index]; path=root/'parts'/str(index)
        if not 0<len(data)<=CHUNK or offset<0 or offset+len(data)>row['size']:
            raise ValueError('分块大小或偏移无效')
        current=path.stat().st_size if path.exists() else 0
        if current!=offset:
            raise ValueError('分块偏移不同步，请重试本批')
        if shutil.disk_usage(root).free<len(data)+64*1024**2:
            raise ValueError('磁盘空间不足，导入已停止，原文件未改动')
        with path.open('ab') as file:
            file.write(data)
        return {'offset':offset+len(data)}


def finish(identifier, register):
    with LOCK:
        root=folder(identifier); manifest=read(root/'manifest.json')
        if not manifest:
            raise ValueError('批次不存在')
        if manifest.get('result'):
            return manifest['result']
        for index,row in enumerate(manifest['files']):
            part=root/'parts'/str(index); target=root/'files'/row['path']
            if target.is_file() and target.stat().st_size==row['size']:
                continue
            if not part.is_file() or part.stat().st_size!=row['size']:
                raise ValueError('文件尚未传完，不会交给剪辑引擎：'+row['path'])
        for index,row in enumerate(manifest['files']):
            part=root/'parts'/str(index); target=root/'files'/row['path']
            if part.exists():
                target.parent.mkdir(parents=True,exist_ok=True)
                part.rename(target)
        result=scan([str(root/'files')],register)
        result.update(mode='copy',importId=identifier,copyBytes=manifest['totalBytes'])
        manifest.update(status='complete',result=result)
        write(root/'manifest.json',manifest)
        return result
