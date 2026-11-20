"""Loopback-only authenticated job service; serial workers keep Resolve safe."""
import json
import hashlib
import hmac
import mimetypes
import os
import secrets
import subprocess
import sys
import threading
import time
import urllib.parse
import uuid
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from common import ROOT, DATA, ASSETS, RESOLVE, OLLAMA_URL, read, write, asset_for, local_urlopen
import jianying
import speaker
import voice_guard
import beta
import telemetry
import intake
import editor_startup
import model_startup
import edit_policy
import script_normalize
from project_naming import make_project_naming

PORT = int(os.environ.get('CJH_PORT', '8765'))
LOCAL = f'http://127.0.0.1:{PORT}'
TOKEN = secrets.token_urlsafe(32)
HOSTS = {f'127.0.0.1:{PORT}', f'localhost:{PORT}'}
ORIGINS = {LOCAL, f'http://localhost:{PORT}'}
EXTENSIONS = {'.mov', '.mp4', '.mkv', '.m4v', '.wav', '.mp3', '.m4a', '.aac', '.flac', '.jpg', '.jpeg', '.png'}
LOCK, JOBS, PROCESSES = threading.RLock(), {}, {}
ASSET_MAP = read(ASSETS, {})


def safe_resume(folder):
    """No blind replays across an NLE side-effect boundary."""
    if (folder/'jianying/started.json').exists() and not (folder/'jianying/result.json').exists():
        return False
    return not (folder/'resolve/started.json').exists() or (folder/'resolve/result.json').exists()


for p in (DATA / 'jobs').glob('*/status.json') if (DATA / 'jobs').exists() else []:
    j = read(p)
    if j['status'] in ('queued', 'running'):
        alive=False
        if j.get('workerPid'):
            try:os.kill(j['workerPid'],0);alive=True
            except ProcessLookupError:pass
            except PermissionError:alive=True
        if j.get('unattended') and j.get('attempts',0)<3 and not alive and safe_resume(p.parent):
            j.update(status='queued',message='服务已恢复，将从已完成检查点继续')
        else:j.update(status='interrupted', message='服务重启；存在运行进程或写入结果不明，请检查后继续')
    JOBS[j['id']] = j


def resolve_status():
    try:
        proc = subprocess.run([RESOLVE, str(ROOT / 'backend/resolve_bridge.py'), 'status'],
                              capture_output=True, text=True, timeout=12)
        if proc.returncode:
            raise ValueError(proc.stderr.strip() or '达芬奇接口未响应')
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as e:
        return {'connected': False, 'error': str(e)}


def capabilities(check_resolve=True):
    models = []
    try:
        with local_urlopen(OLLAMA_URL + '/api/tags', timeout=1) as r:
            models = [m['name'] for m in json.loads(r.read()).get('models', []) if 'cloud' not in m['name'].lower()]
    except Exception:
        pass
    installed_models = model_startup.installed_models()
    return {'service': 'CJH Local Engine', 'version': '3.10', 'autoLaunchEditors': True,
            'automaticProjectNaming': True,
            'scriptCoverage':True,
            'scriptPreparationRevision': script_normalize.REVISION,
            'editModes': list(edit_policy.POLICIES), 'editPolicyRevision': edit_policy.REVISION,
            'autoStartModels': True, 'modelServiceRunning': bool(models),
            'installedVisionModels': installed_models,
            'resolve': resolve_status() if check_resolve else {'installed': editor_startup.RESOLVE_APP.is_dir()}, 'jianying': jianying.status(),
            'voiceGuard': voice_guard.health(),
            'speaker': {'engine': '3D-Speaker / CAM++', 'batchReady': (speaker.folder(speaker.BATCH)/'manifest.json').is_file(), 'mode': 'reference-confirmed-audition-only'},
            'asr': True, 'diarization': 'resolve-21.1-optional', 'localModels': list(dict.fromkeys(models + installed_models)),
            'modelCache': model_startup.speech_models(),
            'packaging': ['花字（预渲染透明视频）', '人物放大（定值 1.18 倍）', '音效（需指定文件）'],
            'notes': ['视频和转写保存在本机', '已确认声纹按项目/素材目录复用；未知声音保留', '视觉分析需要本机 Ollama 视觉模型', '暂不自动回放、音乐避让或导出成片']}


def register(paths, batch_label=None):
    files = []
    for raw in paths:
        p = Path(raw).expanduser().resolve()
        if p.is_dir():
            files.extend(x for x in p.rglob('*') if x.is_file() and x.suffix.lower() in EXTENSIONS)
        elif p.is_file() and p.suffix.lower() in EXTENSIONS:
            files.append(p)
        else:
            raise ValueError('路径不存在或不是支持的素材：' + str(p))
        if len(files) > 1000:
            raise ValueError('一次最多登记 1000 个素材，请选择更小的目录')
    added = []
    with LOCK:
        for p in files:
            existing = next((a for a in ASSET_MAP.values() if a['path'] == str(p)), None)
            info = p.stat()
            a = {'id': existing['id'] if existing else str(uuid.uuid4()), 'path': str(p), 'name': p.name,
                 'size': info.st_size, 'modified': int(info.st_mtime * 1000),
                 'type': mimetypes.guess_type(str(p))[0] or 'video/mp4'}
            if batch_label:
                a['batchLabel'] = batch_label
            elif existing and existing.get('batchLabel'):
                a['batchLabel'] = existing['batchLabel']
            ASSET_MAP[a['id']] = a
            added.append(a)
        write(ASSETS, ASSET_MAP)
    return added


def persist(j):
    write(DATA / 'jobs' / j['id'] / 'status.json', j)


def submit(data):
    kind = data.get('kind')
    if kind not in ('analyze', 'resolve', 'jianying', 'workflow', 'speaker'):
        raise ValueError('未知任务')
    if not data.get('projectId'):
        raise ValueError('缺少工作台项目 ID')
    if kind == 'workflow':
        if 'editMode' in data and not isinstance(data['editMode'],str):
            raise ValueError('剪辑设置无效，请刷新工作台后重试')
        settings=edit_policy.policy(data.get('editMode'))
        # Never trust client-provided preparation: recompute from the preserved
        # original, including old projects that have not used the intake page.
        preparation=script_normalize.normalize_script(data.get('editingScript') or data.get('script',''))
        supplied_script=data.get('editingScript') or data.get('script','')
        if data.get('unattended') is True and supplied_script.strip() and not preparation['strictReady']:
            raise ValueError('已导入脚本，但没有找到明确台词。请检查是否选中了正确脚本；本次已停止，不会把全部口播当成按脚本粗剪。')
        if data.get('unattended') is True:
            if not preparation['strictReady']:
                raise ValueError('请把含人物台词的剧本与素材一起导入。系统会自动整理；没有剧本台词时不会生成全量口播。')
    if kind in ('workflow', 'speaker'):
        with LOCK:
            for existing in JOBS.values():
                if existing['kind'] == kind and existing['projectId'] == data['projectId']:
                    if data.get('clientRequestId') and data['clientRequestId'] == existing.get('clientRequestId'):
                        return public_job(existing)
                    if existing['status'] in ('queued', 'running'):
                        raise ValueError('本项目已有三步任务运行中，请先等待结束')
    identifier = str(uuid.uuid4())
    created = time.time()
    folder = DATA / 'jobs' / identifier
    folder.mkdir(parents=True)
    if kind == 'workflow':
        if not data.get('confirmed'):
            raise ValueError('请确认新建独立达芬奇项目并导出剪映草稿')
        chosen = list(dict.fromkeys(data.get('assetIds', [])))
        if not 1 <= len(chosen) <= 1000:
            raise ValueError('请选择 1～1000 个本机素材')
        if data.get('model', 'small') not in ('tiny', 'base', 'small') or data.get('fps', 30) not in (24, 25, 30, 50, 60):
            raise ValueError('模型或帧率不支持')
        caps = capabilities(check_resolve=False)
        if not caps['resolve'].get('installed') or not Path(RESOLVE).is_file():
            raise ValueError('未安装支持脚本接口的达芬奇；请先完成安装')
        if not caps['jianying']['available'] or not caps['jianying']['app']:
            raise ValueError('请先安装剪映并确认本机草稿转换器就绪')
        vision = model_startup.choose(data.get('visionModel'), caps['localModels'], [model_startup.DEFAULT])
        speech = model_startup.choose(data.get('model'), caps['modelCache'], ['small', 'base', 'tiny'])
        if not vision or not speech:
            raise ValueError('本机模型文件确实缺失，请修复完整安装包；服务未启动时会自动启动，不需要重复安装。')
        payload = dict(data, assets=[asset_for(ASSET_MAP, a) for a in chosen], autoLaunchEditors=True,
                       visionModel=vision, model=speech, editMode=settings['mode'],scriptPreparation=preparation)
        payload['projectNaming'] = make_project_naming(payload['assets'], identifier, created)
        if data.get('unattended'):
            payload['voiceProfile']=voice_guard.choose_profile(payload['assets'],data['projectId'])
            if not isinstance(data.get('targetSeconds',180),(int,float)) or not 60<=data.get('targetSeconds',180)<=3600:
                raise ValueError('目标时长需为 60～3600 秒')
        command = [sys.executable, str(ROOT / 'backend/workflow.py')]
    elif kind == 'speaker':
        payload = speaker.validate_confirmation(data.get('batch'), data.get('labels'), data.get('acknowledged'))
        command = [sys.executable, str(ROOT / 'backend/speaker.py')]
    elif kind == 'analyze':
        chosen = data.get('assetIds', [])
        if not 1 <= len(chosen) <= 100:
            raise ValueError('一次选择 1～100 个素材')
        payload = dict(data, assets=[asset_for(ASSET_MAP, a) for a in chosen])
        command = [sys.executable, str(ROOT / 'backend/analyze.py')]
    elif kind == 'jianying':
        if not data.get('confirmed') or not data.get('ackLimitations'):
            raise ValueError('请确认时间线和转换限制')
        if not isinstance(data.get('source'), dict) or not all(data['source'].get(k) for k in ('projectId', 'timelineId')):
            raise ValueError('请先读取当前达芬奇时间线')
        payload = data
        command = [sys.executable, str(ROOT / 'backend/jianying.py')]
    else:
        if not data.get('confirmed'):
            raise ValueError('执行前需要明确确认新建达芬奇项目')
        selected_ids = {s.get('assetId') for s in data.get('plan',{}).get('selectedSpeech',[])}
        naming_assets = [asset_for(ASSET_MAP, a) for a in selected_ids]
        payload = {'data': dict(data, projectNaming=make_project_naming(naming_assets,identifier,created)),
                   'assets': dict(ASSET_MAP)}
        command = [sys.executable, str(ROOT / 'backend/execute.py')]
    write(folder / 'input.json', payload)
    reuse = data.get('reuseAnalysisJobId') if kind == 'workflow' else None
    if reuse:
        previous = JOBS.get(reuse, {})
        if previous.get('kind') != 'workflow' or previous.get('projectId') != data['projectId'] or previous.get('status') not in ('succeeded', 'failed', 'interrupted'):
            raise ValueError('只能复用本项目已结束三步任务的分析')
        analysis = read(DATA / 'jobs' / previous['id'] / 'analysis/result.json', {})
        if not analysis.get('items'):
            raise ValueError('旧任务没有可复用的分析')
        (folder / 'analysis').mkdir()
        # analyze.run revalidates every source/config fingerprint, never trusts a cached cut plan.
        write(folder / 'analysis/result.json', analysis)
    j = {'id': identifier, 'projectId': data['projectId'], 'kind': kind, 'status': 'queued',
         'created': created, 'message': '排队中', 'command': command, 'resolveASR': data.get('asrEngine') == 'resolve',
         'clientRequestId': data.get('clientRequestId')}
    if kind=='workflow':
        j['unattended']=data.get('unattended') is True
        j['editMode']=settings['mode']
    with LOCK:
        JOBS[identifier] = j
        persist(j)
    return public_job(j)


def public_job(j):
    result = {k: v for k, v in j.items() if k != 'command'}
    if j['kind'] == 'workflow':
        result['progress'] = read(DATA / 'jobs' / j['id'] / 'progress.json', {})
        result['timing'] = telemetry.summary(DATA / 'jobs' / j['id'] / 'timing.jsonl')
        if j['status'] in ('queued', 'running'):
            root=DATA/'jobs'/j['id']
            result['analysisProcessed']=len(read(root/'analysis/result.json',{}).get('items',[]))
            result['analysisTotal']=len(read(root/'input.json',{}).get('assets',[]))
            result['lastLogAt']=(root/'log.txt').stat().st_mtime if (root/'log.txt').exists() else None
            result['queueAhead']=sum(1 for other in JOBS.values() if other['id']!=j['id'] and
                (other['status']=='running' or other['status']=='queued' and other['created']<j['created']))
    return result


def worker():
    while True:
        with LOCK:
            j = next((j for j in JOBS.values() if j['status'] == 'queued' and j.get('nextAttemptAt',0)<=time.time()), None)
            if j:
                j.update(status='running', started=time.time(), message='任务启动')
                j['attempts']=j.get('attempts',0)+1
                persist(j)
        if not j:
            time.sleep(.3)
            continue
        folder = DATA / 'jobs' / j['id']
        awake=None
        attempt_start = time.perf_counter()
        try:
            with LOCK:
                if j['status'] == 'cancelled':
                    continue
                process = subprocess.Popen(j['command'] + [str(folder / 'input.json')], stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT, text=True, bufsize=1, start_new_session=True)
                PROCESSES[j['id']] = process
                j['workerPid']=process.pid
                persist(j)
                if j.get('unattended') and Path('/usr/bin/caffeinate').exists():
                    awake=subprocess.Popen(['/usr/bin/caffeinate','-i','-w',str(process.pid)])
            with (folder / 'log.txt').open('a') as log:
                log.write(f'\n--- attempt {j["attempts"]} ---\n')
                for line in process.stdout:
                    log.write(line)
                    log.flush()
                    with LOCK:
                        j['message'] = line.strip()[-500:]
                        persist(j)
            code = process.wait()
            with LOCK:
                if j['status'] != 'cancelled':
                    j['status'] = 'succeeded' if code == 0 else 'failed'
                    j['result'] = read(folder / 'result.json', {}) if code == 0 else {}
                    if code == 0 and any(i.get('error') or any('失败' in w for w in i.get('warnings', [])) for i in j['result'].get('items', [])):
                        j['status'] = 'partial'
                    j['message'] = '执行完成，请核对结果' if code == 0 else '执行失败：' + j['message']
        except Exception as e:
            j.update(status='failed', message=str(e))
        finally:
            if awake and awake.poll() is None:awake.terminate()
            with LOCK:
                j['finished'] = time.time()
                j.setdefault('attemptHistory', []).append({
                    'attempt': j['attempts'], 'started': j['started'], 'finished': j['finished'],
                    'seconds': round(time.perf_counter() - attempt_start, 3), 'status': j['status']})
                PROCESSES.pop(j['id'], None)
                j.pop('workerPid',None)
                if j['status']=='failed' and j.get('unattended') and j['attempts']<3 and safe_resume(folder) and not read(folder/'startup-failure.json',{}).get('needsUserAction'):
                    j.update(status='queued',nextAttemptAt=time.time()+15*j['attempts'],message='暂时失败，稍后自动续跑已完成检查点（最多 3 次）')
                persist(j)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass  # Never log pairing tokens or media paths.

    def allowed(self):
        return self.headers.get('Host') in HOSTS and self.headers.get('Origin', LOCAL) in ORIGINS

    def auth(self):
        return self.allowed() and secrets.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + TOKEN)

    def reply(self, status, data, mime='application/json'):
        body = json.dumps(data, ensure_ascii=False).encode() if mime == 'application/json' else data
        self.send_response(status)
        origin = self.headers.get('Origin')
        if origin in ORIGINS:
            self.send_header('Access-Control-Allow-Origin', origin)
            self.send_header('Vary', 'Origin')
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        if not self.allowed():
            return self.reply(403, {'error': '不允许的来源'})
        self.send_response(204)
        self.send_header('Access-Control-Allow-Origin', self.headers.get('Origin', LOCAL))
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Authorization, Content-Type, X-CJH-Local')
        self.send_header('Access-Control-Allow-Private-Network', 'true')
        self.end_headers()

    def do_GET(self):
        if not self.allowed():
            return self.reply(403, {'error': '不允许的来源或主机'})
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        if path == '/media':
            query = urllib.parse.parse_qs(parsed.query)
            identifier = query.get('id', [''])[0]
            expires = query.get('expires', ['0'])[0]
            signature = query.get('signature', [''])[0]
            expected = hmac.new(TOKEN.encode(), (identifier + ':' + expires).encode(), hashlib.sha256).hexdigest()
            if not expires.isdigit() or int(expires) < time.time() or not secrets.compare_digest(signature, expected):
                return self.reply(403, {'error': '预览链接失效，请重新打开预览'})
            try:
                if identifier.startswith('spk:'):
                    a = {'path': str(speaker.media_path(identifier)), 'type': 'audio/wav'}
                else:
                    a = asset_for(ASSET_MAP, identifier)
                file = Path(a['path'])
                size = file.stat().st_size
                start, end = 0, size - 1
                partial = self.headers.get('Range')
                if partial:
                    import re
                    match = re.fullmatch(r'bytes=(\d+)-(\d*)', partial)
                    if not match:
                        return self.reply(416, {'error': '不支持的范围'})
                    start = int(match[1])
                    end = min(size - 1, int(match[2])) if match[2] else size - 1
                    if start > end:
                        return self.reply(416, {'error': '范围超出素材'})
                self.send_response(206 if partial else 200)
                self.send_header('Content-Type', a['type'])
                self.send_header('Accept-Ranges', 'bytes')
                self.send_header('Content-Length', str(end - start + 1))
                self.send_header('Cache-Control', 'no-store')
                self.send_header('Referrer-Policy', 'no-referrer')
                if partial:
                    self.send_header('Content-Range', f'bytes {start}-{end}/{size}')
                self.end_headers()
                with file.open('rb') as source:
                    source.seek(start)
                    left = end - start + 1
                    while left:
                        chunk = source.read(min(left, 1024 * 1024))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)
                return
            except (BrokenPipeError, ConnectionResetError):
                return
            except Exception as e:
                return self.reply(400, {'error': str(e)})
        if path == '/api/health':
            return self.reply(200, {'service': 'CJH Local Engine', 'version': beta.VERSION,
                                   'instance': hashlib.sha256(str(ROOT).encode()).hexdigest()[:16]})
        if path == '/api/session':
            # Only the local UI may bootstrap a session; hosted UI needs manual pairing.
            if self.headers.get('Origin', LOCAL) not in (LOCAL, f'http://localhost:{PORT}') or self.headers.get('Sec-Fetch-Site', 'same-origin') not in ('same-origin', 'none') or self.headers.get('X-CJH-Local') != '1':
                return self.reply(403, {'error': '请从本机工作台获取配对码'})
            return self.reply(200, {'token': TOKEN})
        if path.startswith('/api/'):
            if not self.auth():
                return self.reply(401, {'error': '请先连接本机执行服务'})
            if path == '/api/status':
                return self.reply(200, capabilities())
            if path == '/api/beta/status':
                return self.reply(200, beta.environment(capabilities()))
            if path == '/api/beta/report':
                identifier = urllib.parse.parse_qs(parsed.query).get('id', [''])[0]
                with LOCK:
                    job = JOBS.get(identifier, {})
                    if job.get('kind') != 'workflow':
                        return self.reply(404, {'error': '粗剪任务不存在'})
                    return self.reply(200, beta.report(job))
            if path == '/api/speaker-batch':
                return self.reply(200, speaker.public_manifest())
            if path == '/api/assets':
                return self.reply(200, {'assets': list(ASSET_MAP.values())})
            if path == '/api/resolve-current':
                try:
                    proc = subprocess.run([RESOLVE, str(ROOT / 'backend/resolve_snapshot.py'), 'current'],
                                          capture_output=True, text=True, timeout=12)
                    if proc.returncode:
                        raise ValueError(proc.stderr.strip() or '读取达芬奇时间线失败')
                    return self.reply(200, json.loads(proc.stdout.strip().splitlines()[-1]))
                except Exception as e:
                    return self.reply(400, {'error': str(e)})
            if path == '/api/media-url':
                identifier = urllib.parse.parse_qs(parsed.query).get('id', [''])[0]
                if identifier.startswith('spk:'):
                    try:
                        speaker.media_path(identifier)
                    except ValueError as e:
                        return self.reply(404, {'error': str(e)})
                elif identifier not in ASSET_MAP:
                    return self.reply(404, {'error': '素材未登记'})
                expires = str(int(time.time()) + 3600)
                signature = hmac.new(TOKEN.encode(), (identifier + ':' + expires).encode(), hashlib.sha256).hexdigest()
                return self.reply(200, {'url': LOCAL + '/media?' + urllib.parse.urlencode({'id': identifier, 'expires': expires, 'signature': signature})})
            if path == '/api/jobs':
                with LOCK:
                    return self.reply(200, {'jobs': [public_job(j) for j in JOBS.values()]})
            if path == '/api/workflow-analysis':
                identifier = urllib.parse.parse_qs(parsed.query).get('id', [''])[0]
                job = JOBS.get(identifier, {})
                if job.get('kind') != 'workflow':
                    return self.reply(404, {'error': '三步任务不存在'})
                return self.reply(200, read(DATA / 'jobs' / job['id'] / 'analysis/result.json', {'items': []}))
            if path == '/api/workflow-plan':
                identifier = urllib.parse.parse_qs(parsed.query).get('id', [''])[0]
                job = JOBS.get(identifier, {})
                if job.get('kind') != 'workflow':
                    return self.reply(404, {'error': '三步任务不存在'})
                plan = read(DATA / 'jobs' / job['id'] / 'auto-plan.json')
                if not plan:
                    return self.reply(404, {'error': '剪辑决策尚未生成'})
                return self.reply(200, plan)
            if path == '/api/morning-review':
                identifier=urllib.parse.parse_qs(parsed.query).get('id',[''])[0]
                job=JOBS.get(identifier,{})
                if job.get('kind')!='workflow':return self.reply(404,{'error':'任务不存在'})
                report=read(DATA/'jobs'/job['id']/'review-report.json')
                return self.reply(200,report) if report else self.reply(404,{'error':'复核清单尚未生成'})
            if path == '/api/frame':
                rel = urllib.parse.parse_qs(parsed.query).get('file', [''])[0]
                target = (DATA / rel).resolve()
                if target.is_relative_to((DATA / 'jobs').resolve()) and target.suffix == '.jpg' and target.is_file():
                    return self.reply(200, target.read_bytes(), 'image/jpeg')
                return self.reply(404, {'error': '抽帧不存在'})
            return self.reply(404, {'error': '未知接口'})
        name = 'index.html' if path == '/' else path.lstrip('/')
        target = (ROOT / 'dist' / name).resolve()
        if not target.is_relative_to(ROOT / 'dist') or not target.is_file():
            return self.reply(404, b'Not found', 'text/plain')
        return self.reply(200, target.read_bytes(), mimetypes.guess_type(str(target))[0] or 'application/octet-stream')

    def do_POST(self):
        if not self.auth():
            return self.reply(401, {'error': '请先连接本机执行服务'})
        try:
            length = int(self.headers.get('Content-Length', 0))
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path == '/api/intake/chunk':
                if not 0 < length <= intake.CHUNK or self.headers.get('Content-Type') != 'application/octet-stream':
                    raise ValueError('分块大小或类型无效')
                query = urllib.parse.parse_qs(parsed.query)
                self.connection.settimeout(60)
                data = self.rfile.read(length)
                if len(data) != length:
                    raise ValueError('分块传输中断，未保存此块')
                return self.reply(200, intake.chunk(query.get('id',[''])[0], int(query.get('index',['-1'])[0]),
                                  int(query.get('offset',['-1'])[0]), data))
            if not 0 < length <= 4_000_000:
                raise ValueError('请求大小超出限制')
            body = json.loads(self.rfile.read(length))
            if self.path == '/api/intake/prepare':
                return self.reply(200, script_normalize.normalize_script(body.get('text','')))
            if self.path == '/api/intake/scan':
                paths=body.get('paths')
                label=None
                if isinstance(paths,list) and len(paths)==1 and isinstance(paths[0],str):
                    selected=Path(paths[0]).expanduser().resolve()
                    label=selected.name if selected.is_dir() else selected.stem
                return self.reply(200, intake.scan(paths, lambda files:register(files,batch_label=label)))
            if self.path == '/api/intake/begin':
                return self.reply(200, intake.begin(body))
            if self.path == '/api/intake/finish':
                return self.reply(200, intake.finish(body.get('id'), register))
            if self.path == '/api/intake/status':
                return self.reply(200, intake.offsets(body.get('id')))
            if self.path == '/api/beta/feedback':
                with LOCK:
                    job = JOBS.get(body.get('id'), {})
                    if job.get('kind') != 'workflow':
                        raise ValueError('粗剪任务不存在')
                    return self.reply(200, beta.feedback(job, body))
            if self.path == '/api/register':
                paths = body.get('paths')
                if not isinstance(paths, list) or len(paths) > 20:
                    raise ValueError('请输入 1～20 个本地路径')
                return self.reply(200, {'assets': register(paths)})
            if self.path == '/api/jobs':
                # Serialize acceptance too, so two tabs cannot enqueue the same workflow.
                with LOCK:
                    submitted = submit(body)
                return self.reply(202, submitted)
            if self.path == '/api/retry':
                with LOCK:
                    j = JOBS.get(body.get('id'), {})
                    if j.get('kind') != 'workflow' or j.get('status') not in ('failed', 'interrupted'):
                        raise ValueError('只有失败或中断的三步任务可以继续')
                    if not safe_resume(DATA/'jobs'/j['id']):
                        raise ValueError('编辑器写入结果不明，不能盲目重复。请先核对独立项目/草稿。')
                    if j.get('workerPid'):
                        try:os.kill(j['workerPid'],0)
                        except ProcessLookupError:pass
                        else:raise ValueError('上次进程仍在运行，请等待或先检查，不能重复执行')
                    j.update(status='queued', message='已排队继续本次任务')
                    j.pop('result', None)
                    persist(j)
                return self.reply(202, public_job(j))
            if self.path == '/api/jianying-open':
                j = JOBS.get(body.get('id'), {})
                if j.get('kind') not in ('jianying', 'workflow') or j.get('status') != 'succeeded':
                    raise ValueError('请先完成剪映草稿导出')
                result = j.get('result') or read(DATA/'jobs'/j['id']/'result.json', {})
                draft = result.get('draft', result)
                opened = jianying.launch_app(draft.get('draftPath'))
                with LOCK:
                    draft.update(opened)
                    j['result'] = result
                    write(DATA/'jobs'/j['id']/'result.json', result)
                    if j['kind'] == 'workflow':
                        write(DATA/'jobs'/j['id']/'jianying/result.json', draft)
                    persist(j)
                return self.reply(200, opened)
            if self.path == '/api/cancel':
                with LOCK:
                    j = JOBS[body['id']]
                    if (j['kind'] in ('resolve', 'workflow') or j.get('resolveASR')) and j['status'] == 'running':
                        raise ValueError('达芬奇写入中不可强制终止；请等待结束，失败项目会单独保留')
                    if j['status'] not in ('running', 'queued'):
                        raise ValueError('任务已结束')
                    j.update(status='cancelled', message='用户已取消任务')
                    process = PROCESSES.get(j['id'])
                    if process:
                        process.terminate()
                    persist(j)
                return self.reply(200, public_job(j))
            return self.reply(404, {'error': '未知接口'})
        except Exception as e:
            return self.reply(400, {'error': str(e)})


if __name__ == '__main__':
    try:
        server = ThreadingHTTPServer(('127.0.0.1', PORT), Handler)
        threading.Thread(target=worker, daemon=True).start()
        print('CJH 本机执行工作台：' + LOCAL + '/#engines', flush=True)
        print('仅监听本机；视频不上传云端。按 Ctrl+C 停止。', flush=True)
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()
    except OSError as e:
        print('无法启动：' + str(e), file=sys.stderr)
        sys.exit(1)
