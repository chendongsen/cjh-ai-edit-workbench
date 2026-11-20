"""One-off exact-input boundary observation reuse; never copies result/cut data.

Run only for an explicitly authorized old/new job pair. Every skipped/written
batch is audited. Existing files and the current/next live clip are untouched.
"""
import ast
import hashlib
import json
import math
import os
import re
import tempfile
from pathlib import Path

from common import DATA, ROOT, read, write
from edit_policy import policy
from night_edit import select
from reaction_review import REVISION, sample_times


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda:stream.read(8*1024**2), b''):
            digest.update(chunk)
    return digest.hexdigest()


def fingerprint(path):
    stat = path.stat()
    return [str(path), stat.st_size, stat.st_mtime_ns]


def functions(path):
    source = path.read_text()
    tree = ast.parse(source)
    return {node.name:ast.get_source_segment(source, node) for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name in ('infer','make_board','sample_times')}


def active_clip(job):
    report = read(job/'boundary-review.json', {})
    text = (job/'log.txt').read_text() if (job/'log.txt').exists() else ''
    progress = [int(x) for x in re.findall(r'切点前后多帧复核 (\d+)/\d+', text)]
    return max([len(report.get('clips', []))+1, *progress])


def exclusive_json(path, data):
    """Atomic no-overwrite publication, so a live reader never sees partial JSON."""
    path.parent.mkdir(exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, prefix='.reuse-', delete=False) as stream:
        temporary = Path(stream.name)
        json.dump(data, stream, ensure_ascii=False)
        stream.flush()
        os.fsync(stream.fileno())
    try:
        os.link(temporary, path)
    finally:
        temporary.unlink()


def reuse(old_job, new_job, old_source, manifest):
    old_job, new_job, old_source, manifest = map(Path, (old_job,new_job,old_source,manifest))
    audit = {'kind':'exact-boundary-observation-reuse-1', 'oldJob':old_job.name, 'newJob':new_job.name,
             'written':[], 'skipped':[], 'complete':False, 'resultFilesCopied':0, 'modelCalls':0}
    audit_path = new_job/'boundary-batch-reuse-audit.json'
    def checkpoint():
        write(audit_path, audit)
    checkpoint()
    try:
        old_request, new_request = read(old_job/'input.json'), read(new_job/'input.json')
        old_report = read(old_job/'boundary-review.json')
        old_created = read(old_job/'status.json')['created']
        model = new_request['visionModel']
        if old_request['visionModel'] != model or read(old_job/'runtime-models.json')['visionModel'] != model or read(new_job/'runtime-models.json')['visionModel'] != model:
            raise ValueError('旧新任务模型名不一致')
        if model != manifest.parent.name+':'+manifest.name:
            raise ValueError('模型manifest路径与任务模型名不一致')
        if old_report['revision'] != REVISION:
            raise ValueError('动作观察版本不一致')
        expected = functions(old_source)
        current_source = ROOT/'backend/reaction_review.py'
        if set(expected) != {'infer','make_board','sample_times'} or expected != functions(current_source):
            raise ValueError('历史包与当前的取帧/模型prompt/schema/options代码不完全相同')
        st = old_source.stat()
        if max(st.st_mtime, st.st_ctime) >= old_created:
            raise ValueError('历史源代码快照时间不早于旧任务，无法证明配置未变')
        model_rows = []
        entries = read(manifest)
        files = [manifest]+[manifest.parents[4]/'blobs'/row['digest'].replace(':','-')
                            for row in [entries['config'], *entries['layers']]]
        for path in dict.fromkeys(files):
            st = path.stat()
            if max(st.st_mtime, st.st_ctime) >= old_created:
                raise ValueError('模型文件在旧任务之后有修改，不能证明模型未变：'+str(path))
            digest = sha(path)
            if path.name.startswith('sha256-') and digest != path.name.removeprefix('sha256-'):
                raise ValueError('模型blob内容哈希不符：'+str(path))
            model_rows.append({'path':str(path), 'sha256':digest, 'size':st.st_size,
                               'mtimeNs':st.st_mtime_ns, 'ctimeNs':st.st_ctime_ns})
        source_signature = fingerprint(current_source)
        audit['proof'] = {'model':model, 'modelFiles':model_rows, 'oldJobCreated':old_created,
            'historicalSource':str(old_source), 'historicalSourceSha256':sha(old_source),
            'inferenceCodeSha256':hashlib.sha256(json.dumps(expected,sort_keys=True).encode()).hexdigest(),
            'equalFunctions':['infer','make_board','sample_times'], 'currentCodeFingerprint':source_signature}
        old_batches, frames = {}, {}
        labels = {'bite','chew','smile','gesture','handoff','look_shift','neutral','unclear'}
        for row in old_report['clips']:
            source = Path(row['source']);fp = fingerprint(source)
            times = row['startTimes']+row['endTimes']
            key = hashlib.sha256(json.dumps([REVISION,old_report['editPolicy'],model,*fp,row['startTimes'],row['endTimes']]).encode()).hexdigest()
            folder = DATA/'reaction-cache'/key
            result = read(folder/'result.json')
            reported_folders = {str(Path(f['file']).parent) for f in row.get('frames', [])}
            if str(Path('reaction-cache')/key) not in reported_folders or not result or result.get('observed') != row.get('observed'):
                audit['skipped'].append({'oldSource':str(source), 'reason':'源指纹/旧cache目录/观察结果未一致验证'})
                continue
            observed = row['observed']['frameActions']
            if len(observed) != len(times):
                audit['skipped'].append({'oldSource':str(source), 'reason':'旧观察帧数不完整'})
                continue
            for at, action in zip(times, observed):
                path = folder/f'{at:.3f}.jpg'
                if not path.exists():
                    continue
                value = (action['action'], action['confidence'], sha(path))
                frames.setdefault((str(source),at),set()).add(value)
            for offset in range(0,len(times),6):
                batch_times = times[offset:offset+6]
                batch_path = folder/f'batch-{offset}.json'
                part = read(batch_path)
                if not isinstance(part,list) or sorted(r.get('index',-1) for r in part) != list(range(len(batch_times))):
                    audit['skipped'].append({'oldBatch':str(batch_path),'reason':'旧batch序号不完整'})
                    continue
                ordered = sorted(part,key=lambda r:r['index'])
                if any(r.get('action') not in labels or type(r.get('confidence')) not in (float,int)
                       or not math.isfinite(r['confidence']) or not 0 <= r['confidence'] <= 1
                       or r['action'] != observed[offset+i]['action'] or r['confidence'] != observed[offset+i]['confidence']
                       for i,r in enumerate(ordered)):
                    audit['skipped'].append({'oldBatch':str(batch_path),'reason':'batch字段无效或与旧最终观察不一致'})
                    continue
                hashes = [sha(folder/f'{t:.3f}.jpg') for t in batch_times]
                old_batches.setdefault((str(source),tuple(batch_times)),[]).append({
                    'path':str(batch_path),'rows':part,'times':batch_times,'frameSha256':hashes,'sourceFingerprint':fp})
        items = read(new_job/'refined-items.json')['items']
        byid = {i['assetId']:i for i in items}
        selected,*_ = select(items,new_request.get('editingScript') or new_request.get('script',''),
                             int(new_request.get('fps',30)),new_request.get('editMode'))
        settings = policy(new_request.get('editMode'))
        audit['selectionClips'] = len(selected)
        for index,row in enumerate(selected,1):
            item = byid[row['assetId']];source = Path(item['source']);fp = fingerprint(source)
            duration = min(item['duration'],item.get('usableDuration',item['duration']))
            starts = sample_times(row['start'],.75,.75,duration);ends = sample_times(row['end'],.75,3,duration)
            times = starts+ends
            key = hashlib.sha256(json.dumps([REVISION,settings,model,*fp,starts,ends]).encode()).hexdigest()
            folder = DATA/'reaction-cache'/key
            for offset in range(0,len(times),6):
                target = folder/f'batch-{offset}.json'
                record = {'clip':index, 'newBatch':str(target), 'times':times[offset:offset+6]}
                reason = None
                # One extra clip of distance prevents entering a race during filesystem checks.
                if index <= active_clip(new_job)+1:
                    reason = '已完成/正在处理/紧邻下一条，未改动'
                elif target.exists() or (folder/'result.json').exists():
                    reason = '新缓存已存在，绝不覆盖'
                matches = old_batches.get((str(source),tuple(record['times'])),[])
                if not reason and not matches:
                    reason = '没有完全相同帧顺序和batch长度的旧批'
                if not reason and (any(len(frames.get((str(source),t),set())) != 1 for t in record['times'])
                    or len({json.dumps(m['rows'],sort_keys=True) for m in matches}) != 1):
                    reason = '同帧观察或同batch存在冲突，全部跳过'
                if not reason:
                    previous = matches[0]
                    if fingerprint(source) != previous['sourceFingerprint'] or fingerprint(current_source) != source_signature:
                        reason = '源素材或动作识别代码在审计期间变化'
                    else:
                        record.update(oldBatch=previous['path'], sourceFingerprint=fp,
                            frameSha256=previous['frameSha256'], oldBatchSha256=sha(Path(previous['path'])))
                        try:
                            if index <= active_clip(new_job)+1:
                                reason = '任务已接近此条，写入前再次检查后跳过'
                            else:
                                exclusive_json(target,previous['rows'])
                                record['newBatchSha256'] = sha(target)
                                audit['written'].append(record)
                        except FileExistsError:
                            reason = '写入时已有缓存，原子发布拒绝覆盖'
                if reason:
                    record['reason'] = reason
                    audit['skipped'].append(record)
                checkpoint()
        audit['complete'] = True
    except Exception as error:
        audit['blocked'] = str(error)
        raise
    finally:
        checkpoint()
    return audit


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('old_job');parser.add_argument('new_job');parser.add_argument('old_source');parser.add_argument('manifest')
    args = parser.parse_args()
    audit = reuse(args.old_job,args.new_job,args.old_source,args.manifest)
    print(json.dumps({'writtenBatches':len(audit['written']), 'skipped':len(audit['skipped']),
                      'audit':str(Path(args.new_job)/'boundary-batch-reuse-audit.json')},ensure_ascii=False))
