"""Explicitly submit a new comparison job, reusing validated analysis only."""
import argparse
import json
import urllib.request
import uuid
from pathlib import Path
from common import DATA, read, write


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source_job')
    parser.add_argument('--name',required=True)
    parser.add_argument('--submit',action='store_true',help='Create new Resolve/Jianying projects via the serial worker')
    parser.add_argument('--record',type=Path)
    args=parser.parse_args()
    source=(DATA/'jobs'/args.source_job).resolve()
    if source.parent != (DATA/'jobs').resolve():raise ValueError('Invalid job id')
    previous=read(source/'input.json')
    if not previous or not previous.get('unattended'):raise ValueError('需要已存在的夜间粗剪任务')
    if args.record and args.record.exists():raise ValueError('记录已存在；请检查现有任务，不重复提交')
    def api(path,body=None,token=None):
        headers={'X-CJH-Local':'1','Content-Type':'application/json'}
        if token:headers['Authorization']='Bearer '+token
        req=urllib.request.Request('http://127.0.0.1:8765'+path,
            data=json.dumps(body).encode() if body is not None else None,headers=headers)
        with urllib.request.urlopen(req,timeout=60) as response:return json.load(response)
    token=api('/api/session')['token']
    active=[j['id'] for j in api('/api/jobs',token=token)['jobs'] if j['status'] in ('queued','running')]
    if active:raise ValueError('已有任务运行；未提交新任务：'+','.join(active))
    if not args.submit:
        print(json.dumps({'ready':True,'assets':len(previous['assets']),
            'name':args.name,'sourceJob':args.source_job,'willReuseAnalysis':True},ensure_ascii=False))
        return
    payload={k:v for k,v in previous.items() if k not in ('assets','voiceProfile','scriptPreparation','autoLaunchEditors')}
    payload.update(name=args.name,clientRequestId=str(uuid.uuid4()),editMode='strict',
                   confirmed=True,reuseAnalysisJobId=args.source_job)
    job=api('/api/jobs',payload,token)
    result={'jobId':job['id'],'sourceJob':args.source_job,'name':args.name,
            'projectId':payload['projectId'],'status':job['status']}
    if args.record:write(args.record,result)
    print(json.dumps(result,ensure_ascii=False))


if __name__=='__main__':main()
