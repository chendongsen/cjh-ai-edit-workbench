"""Explicit A/B test: freeze an existing successful job's main/audio, redo V2.

prepare: writes only a new comparison directory. export: creates independent
Resolve/Jianying projects, never reuses an ambiguous editor write.
"""
import argparse
import copy
import subprocess
import uuid
from pathlib import Path
from common import ROOT, DATA, RESOLVE, read, write
from broll import enhance
from planner import build
import editor_startup
import jianying


def prepare(source, output, combo=False):
    old = read(source/'auto-plan.json')
    request = read(source/'input.json')
    if not old or not request or not read(source/'result.json'):
        raise ValueError('只能从已完成的真实任务生成对比')
    if output.exists():
        raise ValueError('输出目录已经存在，请使用新目录，不覆盖旧版')
    if combo:
        request = dict(request, visionCombo=True)
    for asset in request['assets']:
        stat = Path(asset['path']).stat()
        if stat.st_size != asset['size'] or int(stat.st_mtime*1000) != asset['modified']:
            raise ValueError('源素材发生变化，不能复用旧分析：'+asset['name'])
    output.mkdir(parents=True)
    items = read(source/'refined-items.json', read(source/'analysis/result.json'))['items']
    food = {i['assetId']:i for i in read(source/'food-index.json')['items']}
    for item in items:
        if item['assetId'] in food:
            for key in ('foodShots','foodIndex','foodReview'):
                if key in food[item['assetId']]:item[key]=food[item['assetId']][key]
    write(output/'input.json',dict(request,name='达人示例广场_组合视觉对比' if combo else '达人示例广场_第二轨增强对比',comparisonSource=str(source)))
    write(output/'boundary-review.json',read(source/'boundary-review.json',{}))
    (output/'analysis').mkdir()
    write(output/'analysis/result.json',{'items':items})
    write(output/'baseline-plan.json',old)
    plan=enhance(copy.deepcopy(old),items,request,output)
    assert plan['selectedSpeech']==old['selectedSpeech']
    assert plan['duration']==old['duration']
    write(output/'auto-plan.json',plan)
    write(output/'comparison.json',dict(plan['brollSummary'],source=str(source),
        totalSeconds=plan['duration'],exported=False))
    print(plan['brollSummary'],flush=True)


def export(output):
    request=read(output/'input.json');plan=read(output/'auto-plan.json')
    if not plan or not read(output/'broll-review.json',{}).get('complete'):
        raise ValueError('补画面分析未完成')
    active=[]
    for path in (DATA/'jobs').glob('*/status.json'):
        state=read(path,{})
        if state.get('status') in ('running','queued'):active.append(path.parent.name)
    if active:raise ValueError('其他任务正在运行或排队，不能切换达芬奇项目：'+','.join(active))
    editor_startup.prepare()
    resolve=output/'resolve';resolve.mkdir(exist_ok=True)
    result=read(resolve/'result.json')
    if not result:
        if (resolve/'started.json').exists():raise ValueError('达芬奇写入回执不明，停止重复创建')
        # resolve_bridge preserves/names the current project before switching.
        data=build({'plan':dict(plan,project=request['name']),'fps':request.get('fps',30),
                    'vertical':request.get('vertical',True),'automaticDraft':True},
                   {a['id']:a for a in request['assets']},resolve,uuid.uuid4().hex)
        write(resolve/'resolve-input.json',data)
        write(resolve/'started.json',{'started':True})
        subprocess.run([RESOLVE,str(ROOT/'backend/resolve_bridge.py'),str(resolve/'resolve-input.json')],check=True)
        result=read(resolve/'result.json')
    jy=output/'jianying';jy.mkdir(exist_ok=True)
    draft=read(jy/'result.json')
    if not draft:
        if (jy/'started.json').exists():raise ValueError('剪映导出回执不明，停止重复创建')
        write(jy/'input.json',{'source':{k:result[k] for k in ('projectId','timelineId')},
                               'name':request['name'],'autoDraftName':result['project']+'_剪映','openAfter':True})
        write(jy/'started.json',{'started':True})
        jianying.run(jy/'input.json');draft=read(jy/'result.json')
    baseline=read(output/'baseline-plan.json')
    actual=read(jy/'timeline-snapshot.json')
    main=next(t for t in actual['tracks'] if t['kind']=='video' and t['index']==1)['clips']
    assert len(main)==len(baseline['selectedSpeech'])
    for a,b in zip(main,baseline['selectedSpeech']):
        assert a['path']==b['source'] and abs(a['sourceStart']-b['start'])<.05
        assert abs(a['duration']-(b['end']-b['start']))<.05
    report=read(output/'comparison.json')
    report.update(exported=True,resolve=result,draft=draft,actualMainTimelineVerified=True)
    write(output/'comparison.json',report)
    print({'draft':draft,'comparison':str(output/'comparison.json')},flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('action',choices=['prepare','export']);parser.add_argument('output',type=Path)
    parser.add_argument('--source',type=Path)
    parser.add_argument('--combo',action='store_true')
    args=parser.parse_args()
    if args.action=='prepare':prepare(args.source,args.output,args.combo)
    else:export(args.output)
