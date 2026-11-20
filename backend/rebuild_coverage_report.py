"""Rebuild completed-job reports only; never import models, workers or NLEs.

Default is a read-only preview. --apply requires a succeeded job and an exited
worker, snapshots every input, backs up changed files, then replaces each output
atomically. Restart the idle service afterward to refresh its in-memory summary.
"""
import argparse
import copy
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path

from script_coverage import build_coverage, coverage_text


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def _require_finished(status):
    if status.get('status') != 'succeeded':
        raise ValueError('只允许重建已成功完成任务的报告；不会修改运行中任务。')
    pid = status.get('workerPid')
    if type(pid) is int and pid > 0:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        except PermissionError:
            pass
        raise ValueError('任务工作进程仍存在，不能重建报告；请等待进程退出。')


def _warnings(existing, coverage):
    values = [x for x in (existing or []) if not isinstance(x, str) or not (
        re.match(r'^\d+ 项脚本镜头未解决；', x) or
        re.match(r'^\d+ 条脚本台词未完整匹配（含部分匹配）', x) or x.startswith('后期执行待核验：'))]
    missing = coverage['dialogue']['partial'] + coverage['dialogue']['missing']
    if missing:
        values.append(f'{missing} 条脚本台词未完整匹配（含部分匹配），详情见脚本缺项清单。')
    unresolved = coverage['shots']['unresolved']
    if unresolved:
        values.append(f'{unresolved} 项脚本镜头未解决；包括部分画面、非视觉／后期义务等，请看逐项缺项清单，不强配画面。')
    count = sum(any(isinstance(o, dict) and o.get('kind') == 'postproduction'
                    for o in row.get('nonVisualObligations', [])) for row in coverage['shots']['items'])
    if count:
        values.append(f'后期执行待核验：{count} 项脚本镜头包含尚无执行回执的后期要求；找到画面不等于已做加速、慢放或文字包装。')
    return values


def rebuild_job_reports(job, apply=False, manual_review=None):
    job = Path(job).resolve(strict=True)
    snapshots = {}

    def load(name):
        data = (job / name).read_bytes()
        snapshots[name] = data
        value = json.loads(data)
        if not isinstance(value, dict):
            raise ValueError(name + ' 应为 JSON 对象，未修改任何文件。')
        return value

    status = load('status.json')
    _require_finished(status)
    prep = load('script-preparation.json')
    plan = load('auto-plan.json')
    shots = load('script-shots-report.json')
    items = load('refined-items.json').get('items')
    review = load('review-report.json')
    result = load('result.json')
    if not isinstance(items, list) or not isinstance(shots.get('requirements'), list):
        raise ValueError('缺少原始 refined-items 或镜头报告，不能用缓存摘要代替原始依据。')
    previous_coverage = plan.get('scriptCoverage') or {}
    reviews = previous_coverage.get('manualReview')
    review_source = previous_coverage.get('manualReviewSource')
    review_snapshot = None
    if manual_review is not None:
        review_path = Path(manual_review).resolve(strict=True)
        review_bytes = review_path.read_bytes()
        reviews = json.loads(review_bytes)
        review_snapshot = (review_path, review_bytes)
        review_source = {'path': str(review_path), 'sha256': hashlib.sha256(review_bytes).hexdigest()}
        previous_reviews = (previous_coverage.get('manualReview') or {}).get('reviews', [])
        incoming_rows = reviews.get('reviews', []) if isinstance(reviews, dict) else []
        incoming_ids = {row.get('requirementId') for row in incoming_rows if isinstance(row, dict)} \
            if isinstance(incoming_rows, list) else set()
        if any(row.get('requirementId') not in incoming_ids for row in previous_reviews):
            raise ValueError('补充复核文件遗漏此前降级项，已拒绝写入，不能静默恢复为完成。')
    coverage = build_coverage(prep, plan, shots, items=items, manual_review=reviews)
    if review_source is not None and reviews is not None:
        coverage['manualReviewSource'] = review_source
    compact = {k: v for k, v in coverage.items() if k not in ('dialogue', 'shots', 'excludedContent')}
    compact.update(dialogue={k: v for k, v in coverage['dialogue'].items() if k != 'items'},
                   shots={k: v for k, v in coverage['shots'].items() if k != 'items'})
    original_plan = copy.deepcopy(plan)
    plan['scriptCoverage'] = coverage
    review['scriptCoverage'] = coverage
    review['warnings'] = _warnings(review.get('warnings'), coverage)
    result.update(scriptCoverage=compact, scriptShotCount=coverage['shots']['required'],
        scriptShotInserted=coverage['shots']['inserted'], scriptShotUnresolved=coverage['shots']['unresolved'],
        missingScriptCount=coverage['dialogue']['partial'] + coverage['dialogue']['missing'])
    result['warnings'] = _warnings(result.get('warnings'), coverage)
    status['result'] = result
    # Rebuilding explanatory metadata must not alter any editing decision.
    assert {k: v for k, v in plan.items() if k != 'scriptCoverage'} == {
        k: v for k, v in original_plan.items() if k != 'scriptCoverage'}
    outputs = {name: _json_bytes(value) for name, value in {
        'script-coverage.json': coverage, 'auto-plan.json': plan,
        'review-report.json': review, 'result.json': result, 'status.json': status}.items()}
    outputs['脚本覆盖与缺项清单.txt'] = coverage_text(coverage).encode('utf-8')
    for name in outputs:
        if name not in snapshots:
            snapshots[name] = (job / name).read_bytes() if (job / name).exists() else None
    for name, data in snapshots.items():
        current = (job / name).read_bytes() if (job / name).exists() else None
        if current != data:
            raise ValueError('任务文件在读取期间变化，已拒绝写入：' + name)
    if review_snapshot and review_snapshot[0].read_bytes() != review_snapshot[1]:
        raise ValueError('补充复核文件在读取期间变化，已拒绝写入。')
    preview = {'job': str(job), 'applied': False, 'revision': coverage['revision'],
        'dialogue': compact['dialogue'], 'shots': compact['shots'],
        'files': list(outputs), 'nleChanged': False, 'sourceMediaChanged': False,
        'serverReloadRequiredAfterApply': True,
        'manualReviewCount': len((reviews or {}).get('reviews', []))}
    if not apply:
        return preview
    _require_finished(status)
    backup = Path(tempfile.mkdtemp(prefix='coverage-report-backup-', dir=job))
    manifest = {'files': {}, 'note': '仅报告元数据备份；未修改 NLE、选片或素材。'}
    for name in outputs:
        data = snapshots[name]
        manifest['files'][name] = {'existed': data is not None,
            'sha256': hashlib.sha256(data).hexdigest() if data is not None else None}
        if data is not None:
            (backup / name).write_bytes(data)
    (backup / 'manifest.json').write_bytes(_json_bytes(manifest))
    # Re-check after backup creation, immediately before the mutation boundary.
    for name, data in snapshots.items():
        current = (job / name).read_bytes() if (job / name).exists() else None
        if current != data:
            raise ValueError('任务文件在备份期间变化，未写入报告：' + name)
    if review_snapshot and review_snapshot[0].read_bytes() != review_snapshot[1]:
        raise ValueError('补充复核文件在备份期间变化，未写入报告。')
    for name, data in outputs.items():
        with tempfile.NamedTemporaryFile(prefix='.coverage-report-', dir=job, delete=False) as stream:
            stream.write(data)
            temporary = Path(stream.name)
        os.replace(temporary, job / name)
    preview.update(applied=True, backup=str(backup))
    return preview


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('job', type=Path)
    parser.add_argument('--apply', action='store_true', help='写入报告并备份旧文件；不调用剪辑器')
    parser.add_argument('--manual-review', type=Path, help='显式补充复核 JSON；只允许将完成项降为部分匹配或无法确认')
    args = parser.parse_args()
    print(json.dumps(rebuild_job_reports(args.job, args.apply, args.manual_review), ensure_ascii=False, indent=2))
