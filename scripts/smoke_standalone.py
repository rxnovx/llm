"""Exercise the real Python + built Next.js servers with an isolated test database.

Run with backend/.venv/bin/python scripts/smoke_standalone.py after npm run build.
"""
import io
import base64
import hashlib
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from urllib.parse import parse_qs, urlencode, urlsplit
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading

import httpx
from PIL import Image

root = Path(__file__).resolve().parents[1]
web = root / "frontend"
backend = root / "backend"
python = Path(sys.executable)


class GitlabDouble(BaseHTTPRequestHandler):
    """Local HTTP protocol double. No external GitLab or user credentials."""
    challenge = ''
    generation = 0
    def log_message(self,*_):
        pass
    def reply(self,value,code=200,raw=False):
        self.send_response(code)
        self.send_header('Content-Type','text/plain' if raw else 'application/json')
        self.end_headers()
        self.wfile.write((value if raw else json.dumps(value)).encode())
    def do_GET(self):
        url = urlsplit(self.path)
        if url.path=='/oauth/authorize':
            query = parse_qs(url.query)
            type(self).challenge = query['code_challenge'][0]
            assert query['scope']==['read_user read_api read_repository']
            self.send_response(302)
            self.send_header('Location',query['redirect_uri'][0]+'?'+urlencode({'state':query['state'][0],'code':'wire-gitlab-code'}))
            self.end_headers();return
        assert self.headers.get('Authorization')==f'Bearer wire-gitlab-access-{self.generation}'
        assert not self.headers.get('Cookie')
        if url.path=='/api/v4/user':return self.reply({'id':77,'username':'wire-user','name':'HTTP GitLab'})
        if url.path=='/api/v4/projects':return self.reply([{'id':7,'path_with_namespace':'team/service','default_branch':'main'}])
        if url.path=='/api/v4/projects/7':return self.reply({'id':7,'path_with_namespace':'team/service'})
        if url.path.endswith('/branches'):return self.reply([{'name':'main'}])
        if url.path.endswith('/tree'):return self.reply([{'name':'main.py','path':'main.py','type':'blob'}])
        if '/commits/' in url.path:return self.reply({'id':'a'*40})
        if url.path.endswith('/raw'):
            assert parse_qs(url.query)['ref']==['a'*40]
            return self.reply('def login(user):\n    return user.is_active\n',raw=True)
        self.reply({},404)
    def do_POST(self):
        form = parse_qs(self.rfile.read(int(self.headers['Content-Length'])).decode())
        assert form['client_id']==['wire-client'] and form['client_secret']==['wire-test-secret']
        assert not self.headers.get('Cookie')
        if self.path=='/oauth/revoke':return self.reply({})
        assert self.path=='/oauth/token'
        if form['grant_type']==['authorization_code']:
            challenge = base64.urlsafe_b64encode(hashlib.sha256(form['code_verifier'][0].encode()).digest()).rstrip(b'=').decode()
            assert challenge==self.challenge and form['code']==['wire-gitlab-code']
            assert form['redirect_uri']==['http://127.0.0.1:3100/apps/gitlab/callback']
        else:
            assert form['refresh_token']==[f'wire-gitlab-refresh-{self.generation}']
        type(self).generation += 1
        self.reply({'access_token':f'wire-gitlab-access-{self.generation}','refresh_token':f'wire-gitlab-refresh-{self.generation}',
            'token_type':'Bearer','scope':'read_user read_api read_repository','expires_in':30 if self.generation==1 else 7200})


def gitlab_apps_smoke(client,access):
    catalog = client.get('/api/apps/catalog',headers=access)
    assert catalog.status_code==200 and catalog.json()['apps'][0]['providers'][0]['ready']
    started = client.post('/api/apps/gitlab/wire/authorize',headers=access,json={'popup':False})
    assert started.status_code==200,started.text
    assert 'cookie_key' not in started.json() and 'HttpOnly' in started.headers['set-cookie']
    with httpx.Client(trust_env=False,follow_redirects=False) as browser:
        approved = browser.get(started.json()['authorize_url'])
    assert approved.status_code==302
    callback = approved.headers['location']
    page = client.get(callback)
    assert page.status_code==200 and page.headers['referrer-policy']=='no-referrer'
    query = parse_qs(urlsplit(callback).query)
    # AuthInitializer in an actual callback tab rotates the app's refresh token.
    refreshed = client.post('/api/auth/refresh')
    assert refreshed.status_code==200
    access = {'authorization':'Bearer '+refreshed.json()['access_token']}
    completed = client.post('/api/apps/gitlab/oauth/complete',headers=access,json={
        'state':query['state'][0],'code':query['code'][0]})
    assert completed.status_code==200,completed.text
    assert 'Max-Age=0' in completed.headers['set-cookie']
    connection = completed.json()['connection']['id']
    path = f'/api/apps/connections/{connection}'
    assert client.get(path+'/projects',headers=access).json()['items'][0]['id']==7
    assert GitlabDouble.generation==2, 'Expiring access token was not refreshed through HTTP'
    assert client.get(path+'/projects/7/branches',headers=access).json()['items'][0]['name']=='main'
    assert client.get(path+'/projects/7/tree',headers=access).json()['items'][0]['selectable']
    conversation = client.post('/api/chat/conversations',headers=access,json={}).json()['id']
    chat = '/api/chat/conversations/'+conversation
    selection = {'app':'gitlab','connection_id':connection,'project_id':7,'ref':'main','paths':['main.py']}
    response = client.post(chat+'/messages',headers=access,json={'content':'login 함수를 설명해 줘',
        'request_id':str(uuid.uuid4()),'apps':[selection]})
    assert 'event: sources' in response.text and 'event: done' in response.text,response.text
    saved = client.get(chat,headers=access).json()['messages'][-1]
    assert saved['apps']==[selection] and saved['sources'][0]['commit']=='a'*40
    assert saved['sources'][0]['path']=='main.py' and saved['is_test']
    assert 'wire-gitlab-access' not in response.text and 'wire-test-secret' not in response.text
    removed = client.delete(path,headers=access)
    assert removed.status_code==200 and removed.json()['remote_revoked'] is True
    assert client.get(path+'/projects',headers=access).status_code==404
    assert client.get(chat,headers=access).json()['messages'][-1]['sources']==saved['sources']
    return access


def model_controls_smoke(client, access):
    """Actual BFF/API traffic with a local protocol double, never a real model."""
    entered, release = threading.Event(), threading.Event()
    release_background = threading.Event()
    requests = []
    class ModelServer(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            assert self.path == '/v1/models'
            self.send_response(200); self.send_header('Content-Type', 'application/json'); self.end_headers()
            self.wfile.write(b'{"data":[{"id":"wire"}]}')
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append(payload)
            index = len(requests)
            self.send_response(200); self.send_header('Content-Type','text/event-stream'); self.end_headers()
            if index == 1:
                entered.set(); release.wait(8)
            self.wfile.write(('data: '+json.dumps({'choices':[{'delta':{'content':'모델 응답'},'finish_reason':None}]})+'\n\n').encode())
            self.wfile.flush()
            if payload['messages'][-1]['content'] == '백그라운드 첫 요청':
                release_background.wait(10)
            self.wfile.write(('data: '+json.dumps({'choices':[{'delta':{},'finish_reason':'stop'}]})+'\n\n'
                +'data: '+json.dumps({'choices':[],'usage':{'prompt_tokens':12,'completion_tokens':4}})+'\n\ndata: [DONE]\n\n').encode())
            self.wfile.flush()
    server=ThreadingHTTPServer(('127.0.0.1',0),ModelServer)
    thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
    try:
        connection={'id':'wire-model','name':'HTTP 연동 검증','provider':'vllm','base_url':f'http://127.0.0.1:{server.server_port}/v1',
            'model':'wire','max_concurrent':1,'context_window':4096,'max_output_tokens':256}
        assert client.post('/api/admin/connections',headers=access,json=connection).status_code==201
        paths=[]
        for _ in range(2):
            result=client.post('/api/chat/conversations',headers=access,json={'connection_id':'wire-model','model':'wire'})
            assert result.status_code==201
            paths.append('/api/chat/conversations/'+result.json()['id'])
        def body(content):
            return {'connection_id':'wire-model','model':'wire','content':content,'request_id':str(uuid.uuid4())}
        with ThreadPoolExecutor() as executor:
            first=executor.submit(client.post,paths[0]+'/messages',headers=access,json=body('첫 요청'))
            assert entered.wait(5), 'First request never reached the local model double'
            second=body('대기 취소 요청'); seen_queue=False; terminal=None
            with client.stream('POST',paths[1]+'/messages',headers=access,json=second) as response:
                assert response.status_code==200
                kind=''
                for line in response.iter_lines():
                    if line.startswith('event: '): kind=line[7:]
                    elif line.startswith('data: '):
                        value=json.loads(line[6:])
                        if kind=='queue' and value['status']=='queued' and not seen_queue:
                            seen_queue=True
                            assert not first.done() and len(requests)==1
                            assert client.post(paths[1]+'/cancel',headers=access,json={'request_id':second['request_id']}).status_code==204
                        if kind in {'done','error','cancelled'}: terminal=kind
            assert seen_queue and terminal=='cancelled' and len(requests)==1
            release.set()
            assert 'event: done' in first.result(timeout=5).text
        assert client.patch(paths[0]+'/context',headers=access,json={
            'summary':'유지할 결정 사항','through_position':1,'revision':0}).status_code==200
        response=client.post(paths[0]+'/messages',headers=access,json=body('요약 이후 질문'))
        assert 'event: done' in response.text and len(requests)==2
        assert requests[-1]['messages']==[
            {'role':'system','content':'사용자가 작성한 이전 대화 요약 메모:\n유지할 결정 사항'},
            {'role':'user','content':'요약 이후 질문'}]
        assert requests[-1]['max_tokens']==256
        stats=client.get('/api/admin/overview?mode=live',headers=access).json()
        assert stats['requests_24h']['total']==3 and stats['requests_24h']['complete']==2 and stats['requests_24h']['cancelled']==1
        assert stats['generating']==0 and stats['queued']==0

        # Closing the first HTTP stream should only detach the viewer. With a
        # second permit available, another chat completes while the first waits.
        updated = client.patch('/api/admin/connections/wire-model', headers=access,
            json=connection | {'max_concurrent': 2, 'revision': 1})
        assert updated.status_code == 200, updated.text
        with client.stream('POST', paths[0]+'/messages', headers=access, json=body('백그라운드 첫 요청')) as response:
            assert response.status_code == 200, response.status_code
            for line in response.iter_lines():
                if line == 'event: delta':
                    break
            else:
                raise AssertionError('The first background job did not start')
        active = client.get('/api/chat/active', headers=access).json()['ids']
        assert paths[0].rsplit('/', 1)[1] in active
        second = client.post(paths[1]+'/messages', headers=access, json=body('새 대화 요청'))
        assert 'event: done' in second.text, second.text[-1500:]
        reopened = client.get(paths[0], headers=access).json()
        assert reopened['generation']['status'] == 'running' and reopened['messages'][-1]['content'] == '모델 응답'
        release_background.set()
        for _ in range(40):
            reopened = client.get(paths[0], headers=access).json()
            if not reopened['generation']:
                break
            time.sleep(0.1)
        assert reopened['messages'][-1]['status'] == 'complete'
        assert client.get('/api/chat/active', headers=access).json()['ids'] == []
        print('PASS: SSE disconnect, concurrent chat and recovery through the BFF')

        checked = client.post('/api/admin/connections/wire-model/probe', headers=access, json={})
        assert checked.status_code == 200 and checked.json()['sample'] == '모델 응답', checked.text
        user_id = client.get('/api/account/users/me', headers=access).json()['subject']
        issued = client.post('/api/admin/service-keys', headers=access,
            json={'label':'HTTP smoke','user_id':user_id,'daily_tokens':0})
        assert issued.status_code == 201, issued.text
        key = issued.json()
        service = {'authorization': 'Bearer ' + key['secret']}
        assert client.get('/v1/models', headers=service).json()['data'][0]['id'] == 'wire-model:wire'
        body_gateway = {'model':'wire-model:wire','messages':[{'role':'user','content':'HTTP gateway'}],
            'stream':True,'stream_options':{'include_usage':True}}
        streamed = client.post('/v1/chat/completions', headers=service, json=body_gateway)
        assert streamed.status_code == 200 and 'data: [DONE]' in streamed.text and '"total_tokens": 16' in streamed.text
        keys = client.get('/api/admin/service-keys', headers=access, params={'user_id':user_id}).json()['items']
        assert keys[0]['reported_input'] == 12 and 'secret' not in keys[0]
        assert client.patch('/api/admin/service-keys/'+key['id'], headers=access, json={'revoke':True}).status_code == 200
        assert client.get('/v1/models', headers=service).status_code == 401
        print('PASS: saved-model probe, gateway SSE, per-key token reporting and revocation')
    finally:
        release.set(); release_background.set(); server.shutdown(); server.server_close(); thread.join(timeout=2)
def workflows_smoke(client,access):
    """Real BFF, upload session, branch, version, output bytes and feedback routes."""
    project=client.post('/api/workflows/projects',headers=access,json={'name':'HTTP 프로젝트','instructions':'한국어로 설명'});assert project.status_code==201,project.text
    p=project.json();conversation=client.post('/api/chat/conversations',headers=access,json={'project_id':p['id']}).json()['id']
    path='/api/chat/conversations/'+conversation
    blocked=client.post('/api/workflows/projects',headers=access|{'origin':'https://untrusted.invalid'},json={'name':'차단 확인'})
    assert blocked.status_code==403
    def run(text,retry=False):
        r=client.post(path+'/messages',headers=access,json={'content':text,'request_id':str(uuid.uuid4()),'retry':retry});assert 'event: done' in r.text,r.text[-2000:]
        return client.get(path,headers=access).json()['messages'][-1]
    answer=run('http 본문 고유 검색');run('http 본문 고유 검색',True)
    versions=client.get('/api/workflows/messages/'+answer['id']+'/versions',headers=access).json()['items'];assert len(versions)==2
    selected=client.post('/api/workflows/messages/'+answer['id']+'/versions/'+versions[0]['id']+'/select',headers=access)
    assert selected.status_code==200,selected.text
    matches=client.get('/api/chat/conversations',headers=access,params={'q':'고유 검색','project_id':p['id']}).json()['items'];assert matches[0]['match']['message_id']
    attempt=client.post('/api/workflows/uploads',headers=access,json={'filename':'첨부.md','conversation_id':conversation}).json()['id']
    doc=client.post('/api/knowledge/documents',headers=access,data={'conversation_id':conversation,'upload_id':attempt},files={'file':('첨부.md','한글 검증 자료'.encode())});assert doc.status_code==201,doc.text
    assert client.post('/api/workflows/uploads/'+attempt+'/cancel',headers=access).status_code==200
    assert client.get('/api/knowledge/documents',headers=access,params={'conversation_id':conversation}).json()['items']==[]
    large = client.post('/api/knowledge/documents', headers=access, data={'conversation_id':conversation},
        files={'file':('size-test.md', b' ' * (7 * 1024 * 1024))})
    assert large.status_code == 201, large.text
    assert client.delete('/api/knowledge/documents/'+large.json()['id'], headers=access).status_code == 200
    for format in ['md','txt','csv','xlsx','pdf']:
        r=client.post('/api/workflows/messages/'+answer['id']+'/export',headers=access,json={'format':format,'name':'한글 결과'});assert r.status_code==201,r.text
        file=r.json();assert client.get('/api/workflows/artifacts/'+file['id'],headers=access).status_code==200
        downloaded=client.get('/api/workflows/artifacts/'+file['id']+'/download',headers=access);assert downloaded.status_code==200 and 'filename*=' in downloaded.headers['content-disposition']
        if format=='pdf':assert downloaded.content.startswith(b'%PDF-')
        if format=='xlsx':assert downloaded.content.startswith(b'PK')
        assert client.delete('/api/workflows/artifacts/'+file['id'],headers=access).status_code==200
    result=client.post('/api/workflows/conversations/'+conversation+'/fork',headers=access,json={'message_id':answer['id']});assert result.status_code==201,result.text
    assert result.json()['conversation']['parent_id']==conversation
    report=client.post('/api/workflows/messages/'+answer['id']+'/feedback',headers=access,json={'category':'source','detail':'HTTP 신고 흐름 검증'});assert report.status_code==201
    assert client.get('/api/workflows/feedback',headers=access).json()['items'][0]['answer']
    assert client.patch('/api/workflows/feedback/'+report.json()['id'],headers=access,json={'status':'resolved'}).status_code==200
    artifact_answer=run('테스트 pdf 파일로 만들어줘');assert artifact_answer['artifacts']
    assert client.post('/api/workflows/conversations/'+conversation+'/continue',headers=access).status_code==201
    assert client.patch('/api/workflows/conversations/'+conversation+'/auto-summary',headers=access,json={'enabled':False}).status_code==200
    assert client.delete('/api/workflows/projects/'+p['id'],headers=access).status_code==200
    print('PASS: workflows BFF, project context/search, versions, branches, upload cancellation, five real export formats and operator feedback')


if not (web / ".next/standalone/server.js").is_file():
    raise SystemExit("Install backend dev dependencies in the active venv and run frontend npm run build first.")

# Assemble the same static assets that the frontend Dockerfile copies.
shutil.copytree(web / ".next/static", web / ".next/standalone/.next/static", dirs_exist_ok=True)
shutil.copytree(web / "public", web / ".next/standalone/public", dirs_exist_ok=True)

processes = []
gitlab_server = ThreadingHTTPServer(('127.0.0.1',0),GitlabDouble)
gitlab_thread = threading.Thread(target=gitlab_server.serve_forever,daemon=True)
gitlab_thread.start()
with tempfile.TemporaryDirectory(prefix="llm-accounts-smoke-") as temporary:
    scratch = Path(temporary)
    logs = []
    try:
        api_log, web_log = [(scratch / name).open("w") for name in ("api.log", "web.log")]
        logs = [api_log, web_log]
        api_env = os.environ | {
            "APP_ENV": "test", "DATABASE_URL": f"sqlite:///{scratch}/app.db",
            "UPLOAD_DIRECTORY": str(scratch / "avatars"), "SERVER_PORT": "8100", "SERVER_HOST": "127.0.0.1",
            "JWT_SECRET_KEY": "standalone-smoke-test-key-at-least-32-bytes",
            "EMAIL_PROVIDER": "disabled", "EMAIL_VERIFICATION_REQUIRED": "false",
            "GOOGLE_OAUTH_ENABLED": "false", "PUBLIC_REGISTRATION_ENABLED": "true",
            "LLM_TEST_MODE": "true", "LLM_CONNECTIONS_JSON": "[]",
            "FRONTEND_URL":"http://127.0.0.1:3100",
            "GITLAB_INSTANCES_JSON":json.dumps([{'id':'wire','name':'HTTP GitLab','base_url':f'http://127.0.0.1:{gitlab_server.server_port}',
                'client_id':'wire-client','secret_ref':'wire'}]),
            "GITLAB_CLIENT_SECRETS_JSON":json.dumps({'wire':'wire-test-secret'}),
            "INTEGRATIONS_ENCRYPTION_KEYS":json.dumps([base64.urlsafe_b64encode(os.urandom(32)).decode()]),
        }
        web_env = os.environ | {"APP_URL": "http://127.0.0.1:3100", "PORT": "3100", "HOSTNAME": "127.0.0.1",
            "AUTH_SERVER_URL": "http://127.0.0.1:8100", "AUTH_COOKIE_SECURE": "false"}
        processes.append(subprocess.Popen([str(python), "main.py"], cwd=backend, env=api_env, stdout=api_log, stderr=subprocess.STDOUT))
        processes.append(subprocess.Popen(["node", "server.js"], cwd=web / ".next/standalone", env=web_env, stdout=web_log, stderr=subprocess.STDOUT))
        with httpx.Client(base_url="http://127.0.0.1:3100", timeout=10, trust_env=False) as client:
            for url in ["http://127.0.0.1:8100/ready", "/health"]:
                for _ in range(80):
                    try:
                        if client.get(url).status_code == 200:
                            break
                    except httpx.HTTPError:
                        pass
                    time.sleep(0.1)
                else:
                    raise RuntimeError(f"Server did not start: {url}")
            assert all(process.poll() is None for process in processes), 'A newly launched server exited; refusing to test another process on the same port'
            client.headers.update({"x-llm-account": "1", "origin": "http://127.0.0.1:3100"})
            # KaTeX fonts must be served by this app, including offline deployments.
            document = client.get('/').text
            assert 'data-workspace-background' in document and re.search(r'<textarea\b[^>]*id="chat-message"',document)
            assert 'data-workspace-modal=' not in document
            modal_paths = {
                '/login':'auth','/user/signup':'auth','/user/reset-password':'auth','/user/verify-email':'auth','/user/recover':'auth',
                '/user/profile':'settings','/user/profile/edit':'settings','/user/settings':'settings','/admin':'admin',
                '/user/settings/apps':'settings','/apps/gitlab/callback':'auth',
            }
            for page_path,kind in modal_paths.items():
                page=client.get(page_path,params={'chat':'12345678-1234-1234-1234-123456789abc'})
                assert page.status_code==200, page_path
                assert re.search(r'<dialog\b[^>]*data-workspace-modal="'+kind+'"',page.text), page_path
                assert re.search(r'<textarea\b[^>]*id="chat-message"',page.text), page_path
                assert re.search(r'<div\b[^>]*data-workspace-background[^>]*inert=',page.text), page_path
            missing=client.get('/missing-workspace-page')
            assert missing.status_code==404 and 'data-workspace-modal="other"' in missing.text
            styles = set(re.findall(r'href="([^" ]+\.css(?:\?[^" ]*)?)"', document))
            fonts = set()
            for style in styles:
                response = client.get(style)
                assert response.status_code == 200
                fonts.update(re.findall(r'/_next/static/media/[^\s"\')]+\.woff2', response.text))
            assert fonts, 'Bundled math font assets were not found in the rendered styles'
            for font in fonts:
                response = client.get(font)
                assert response.status_code == 200 and len(response.content) > 100
            username = f"standalone_{uuid.uuid4().hex[:12]}"
            response = client.post("/api/auth/register", json={"username": username,
                "email": f"{username}@example.com", "password": "StandalonePass123!", "accept_terms": True})
            assert response.status_code == 201, response.text
            response = client.post("/api/auth/login", json={"loginId": username, "password": "StandalonePass123!"})
            assert response.status_code == 200, response.text
            token = response.json()
            assert "refresh_token" not in token
            assert "HttpOnly" in response.headers["set-cookie"]
            access = {"authorization": f"Bearer {token['access_token']}"}
            assert client.get("/api/account/users/me", headers=access).json()["username"] == username
            assert client.patch("/api/account/users/me", headers=access, json={"display_name": "실행 검증"}).json()["display_name"] == "실행 검증"
            image = io.BytesIO()
            Image.new("RGB", (10, 10), "blue").save(image, "PNG")
            response = client.post("/api/account/users/me/avatar", headers=access,
                files={"file": ("avatar.png", image.getvalue(), "image/png")})
            assert response.status_code == 201, response.text
            path = response.json()["avatar_url"].replace("/users/avatars/", "/api/avatars/")
            assert client.get(path).headers["content-type"] == "image/png"
            config = client.get('/api/chat/config', headers=access)
            assert config.status_code == 200 and config.json()['connections'][0]['is_test']
            response = client.post('/api/chat/conversations', headers=access, json={})
            assert response.status_code == 201, response.text
            conversation = response.json()['id']
            chat_path = f'/api/chat/conversations/{conversation}'
            def turn(content, retry=False, stop=False):
                request_id = str(uuid.uuid4())
                collected, first_delta, terminal = '', None, None
                started = time.monotonic()
                with client.stream('POST', chat_path + '/messages', headers=access, json={
                    'content': content, 'request_id': request_id, 'retry': retry}) as stream:
                    assert stream.status_code == 200
                    assert stream.headers['content-type'].startswith('text/event-stream')
                    kind = ''
                    for line in stream.iter_lines():
                        if line.startswith('event: '): kind = line[7:]
                        elif line.startswith('data: '):
                            data = json.loads(line[6:])
                            if kind == 'delta':
                                first_delta = first_delta or time.monotonic()
                                collected += data['content']
                                if stop:
                                    assert client.post(chat_path + '/cancel', headers=access, json={'request_id':request_id}).status_code == 204
                                    break
                            if kind in {'done', 'error', 'cancelled'}: terminal = kind
                if not stop:
                    assert terminal == 'done' and collected
                    assert first_delta and time.monotonic() - first_delta > .1
                    assert first_delta - started < 5
                return collected
            answer = turn('테스트 대화를 시작할게요')
            detail = client.get(chat_path, headers=access).json()
            assert len(detail['messages']) == 2 and detail['messages'][1]['content'] == answer
            assert detail['messages'][1]['is_test'] and not detail['conversation']['busy']
            partial = turn('응답 중지를 확인할게요', stop=True)
            for _ in range(40):
                detail = client.get(chat_path, headers=access).json()
                if not detail['conversation']['busy']: break
                time.sleep(.1)
            assert partial and not detail['conversation']['busy']
            assert detail['messages'][-1]['status'] == 'cancelled'
            turn('응답 중지를 확인할게요', retry=True)
            assert len(client.get(chat_path, headers=access).json()['messages']) == 4
            assert client.patch(chat_path, headers=access, json={'title':'스트리밍 검증'}).json()['title'] == '스트리밍 검증'
            preview = client.post(chat_path+'/context/preview',headers=access,json={'content':'이어질 질문'})
            assert preview.status_code == 200 and preview.json()['is_estimate'] and preview.json()['last_complete_position']==3
            note = {'summary':'이전 대화의 요약 메모','through_position':3,'revision':0}
            assert client.patch(chat_path+'/context',headers=access,json=note).status_code == 200
            assert client.patch(chat_path+'/context',headers=access,json=note).status_code == 409
            preview = client.post(chat_path+'/context/preview',headers=access,json={'content':'이어질 질문'}).json()
            assert preview['summary_included'] and preview['summarized_turns']==2 and preview['revision']==1
            assert client.get('/api/chat/conversations?offset=1', headers=access).json()['items'] == []
            assert client.delete(chat_path, headers=access).status_code == 204
            assert client.get('/api/admin/overview',headers=access).status_code == 403
            subprocess.run([str(python),'manage_admin.py','grant',username], cwd=backend, env=api_env,
                check=True, capture_output=True, text=True)
            operations = client.get('/api/admin/overview',headers=access)
            assert operations.status_code == 200, operations.text
            assert operations.json()['requests_24h']['total'] == 3
            assert operations.json()['requests_24h']['complete'] == 2 and operations.json()['requests_24h']['cancelled'] == 1
            assert client.get('/api/admin/overview?mode=live',headers=access).json()['requests_24h']['total'] == 0
            assert len(client.get('/api/admin/requests',headers=access).json()['items']) == 3
            members = client.get('/api/admin/users',headers=access).json()['items']
            assert len(members) == 1 and members[0]['is_admin']
            audits = client.get('/api/admin/audit',headers=access).json()['items']
            assert any(row['action']=='admin.role.grant' and row['target']==username for row in audits)
            assert client.post('/api/admin/connections/demo/check',headers=access).json()['status'] == 'test'
            connection = {'id':'smoke-local','name':'테스트 등록','provider':'ollama','base_url':'http://127.0.0.1:11434',
                'model':'local-model','context_window':4096,'max_output_tokens':256,'max_concurrent':1,'allowed_users':[username]}
            created = client.post('/api/admin/connections',headers=access,json=connection)
            assert created.status_code == 201 and created.json()['revision']==1
            assert any(row['id']=='smoke-local' for row in client.get('/api/chat/config',headers=access).json()['connections'])
            assert '127.0.0.1:11434' not in client.get('/api/chat/config',headers=access).text
            assert client.patch('/api/admin/connections/smoke-local',headers=access,json=connection|{'revision':1,'enabled':False}).status_code==200
            assert not any(row['id']=='smoke-local' for row in client.get('/api/chat/config',headers=access).json()['connections'])
            assert client.get('/api/admin/connections',headers=access).json()['items'][0]['enabled'] is False
            model_controls_smoke(client,access)
            access = gitlab_apps_smoke(client,access)
            # Real multipart BFF -> API -> automatic subprocess index worker -> RAG SSE.
            knowledge_base=client.post('/api/knowledge/bases',headers=access,json={'name':'HTTP 지식 검증'})
            assert knowledge_base.status_code==201,knowledge_base.text
            knowledge_id=knowledge_base.json()['id']
            uploaded=client.post('/api/knowledge/documents',headers=access,data={'base_id':knowledge_id},
                files={'file':('vacation.md','연차 휴가 규정: 연간 15일을 사용할 수 있습니다.'.encode(),'text/markdown')})
            assert uploaded.status_code==201,uploaded.text
            document_id=uploaded.json()['id']
            for _ in range(100):
                documents=client.get('/api/knowledge/documents',headers=access,params={'base_id':knowledge_id}).json()['items']
                if documents[0]['status']=='ready':break
                assert documents[0]['status']!='error',documents[0]['error']
                time.sleep(.1)
            else:raise AssertionError('Automatic knowledge worker did not finish')
            rag_conversation=client.post('/api/chat/conversations',headers=access,json={}).json()['id']
            rag=client.post(f'/api/chat/conversations/{rag_conversation}/messages',headers=access,
                json={'content':'연차 휴가 규정을 알려줘','request_id':str(uuid.uuid4()),'assistant':{'base_ids':[knowledge_id]}})
            assert rag.status_code==200 and 'event: done' in rag.text,rag.text[-2000:]
            detail=client.get(f'/api/chat/conversations/{rag_conversation}',headers=access).json()['messages'][-1]
            source=detail['sources'][0]
            assert source['app']=='knowledge' and source['document_id']==document_id and '[KS1]' in detail['content']
            citation=client.get(f"/api/knowledge/documents/{document_id}/chunks/{source['chunk_id']}",headers=access)
            assert citation.status_code==200 and '15일' in citation.json()['content']
            assert client.post('/api/assistant/memories',headers=access,json={'content':'검증용 개인 기억'}).status_code==201
            assert client.put('/api/assistant/preferences',headers=access,json={'instructions':'간결하게 답변','memory_enabled':True,'revision':0}).status_code==200
            assert client.get('/api/assistant/catalog',headers=access).status_code==200
            assert client.get('/api/assistant/operations',headers=access).json()['documents']['ready']==1
            assert client.delete('/api/knowledge/bases/'+knowledge_id,headers=access).status_code==200
            workflows_smoke(client,access)
            assert client.get('/api/admin/overview').status_code == 401
            assert client.get('/admin').status_code == 200
            response = client.post("/api/auth/refresh")
            assert response.status_code == 200, response.text
            refreshed = response.json()
            assert client.get("/api/account/users/me", headers=access).status_code == 401
            access = {"authorization": f"Bearer {refreshed['access_token']}"}
            assert client.get("/api/account/users/me", headers=access).status_code == 200
            assert client.post("/api/auth/logout").status_code == 204
            assert not any(c.name == "llm_accounts_refresh" for c in client.cookies.jar)
            assert client.get("/api/account/users/me", headers=access).status_code == 401
            assert client.get("/login").status_code == 200
            assert all(process.poll() is None for process in processes), 'A test server exited during validation'
            print("PASS: chat-first HTML, accounts, streaming, operator controls, context/queue, GitLab, multipart knowledge upload/automatic indexing/RAG/citations/deletion and personal preferences over HTTP")
    except Exception:
        for handle in logs:
            handle.flush()
        # Server logs contain request paths, not test credentials or token responses.
        for name in ("api.log", "web.log"):
            print((scratch / name).read_text()[-3000:])
        raise
    finally:
        gitlab_server.shutdown();gitlab_server.server_close();gitlab_thread.join(timeout=2)
        for process in reversed(processes):
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
        for handle in logs:
            handle.close()
