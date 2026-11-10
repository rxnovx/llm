"""Real MCP HTTP + git-http-backend + broker HTTP; GitLab account APIs use a protocol double."""
import asyncio
import base64
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
from urllib.parse import urlsplit

import httpx
import pytest
import uvicorn
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.chat import providers
from app.modules.chat.models import Admission,GenerationRun
from app.modules.workspaces.models import Workspace
from terminal_mcp.config import TerminalSettings
from terminal_mcp.server import build_server,http_app
from test_chat import auth,create,events,send
from test_gitlab_apps import gitlab,connect,INSTANCE


def git(*args,cwd):
    return subprocess.check_output(['git',*args],cwd=cwd,stderr=subprocess.DEVNULL).decode().strip()


@contextmanager
def http_server(handler):
    server=ThreadingHTTPServer(('127.0.0.1',0),handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield server
    finally:server.shutdown();server.server_close();thread.join(timeout=3)


@pytest.fixture
def workspace_stack(client,gitlab,monkeypatch,tmp_path):
    source=tmp_path/'source';source.mkdir()
    git('init','-b','main',cwd=source)
    (source/'main.py').write_text('def login(user):\n    return user.active\n')
    (source/'README.md').write_text('# Local protocol fixture\n')
    git('add','.',cwd=source)
    git('-c','user.name=Local Test','-c','user.email=test@example.invalid','commit','-m','fixture',cwd=source)
    sha=git('rev-parse','HEAD',cwd=source)
    repositories=tmp_path/'repositories';(repositories/'team').mkdir(parents=True)
    git('clone','--bare',str(source),str(repositories/'team'/'service.git'),cwd=tmp_path)
    class GitHTTP(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_GET(self):self.respond()
        def do_POST(self):self.respond()
        def respond(self):
            auth_header=self.headers.get('Authorization','')
            if not auth_header.startswith('Basic '):
                self.send_response(401);self.send_header('WWW-Authenticate','Basic realm="local-git"');self.end_headers();return
            assert base64.b64decode(auth_header[6:]).decode().startswith('oauth2:local-access-')
            url=urlsplit(self.path)
            body=self.rfile.read(int(self.headers.get('Content-Length','0')))
            env={'PATH':os.environ.get('PATH','/usr/bin:/bin'),'GIT_PROJECT_ROOT':str(repositories),'GIT_HTTP_EXPORT_ALL':'1',
                'PATH_INFO':url.path,'QUERY_STRING':url.query,'REQUEST_METHOD':self.command,'REMOTE_USER':'alice',
                'CONTENT_TYPE':self.headers.get('Content-Type',''),'CONTENT_LENGTH':str(len(body)),
                'GIT_PROTOCOL':self.headers.get('Git-Protocol',''),'SERVER_PROTOCOL':'HTTP/1.1'}
            output=subprocess.run(['git','http-backend'],input=body,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,env=env,check=True).stdout
            header,_,payload=output.partition(b'\r\n\r\n')
            if not _:header,_,payload=output.partition(b'\n\n')
            self.send_response(200)
            for line in header.decode().splitlines():
                key,_,value=line.partition(':')
                if key.lower() not in {'status','content-length','transfer-encoding'}:self.send_header(key,value.strip())
            self.send_header('Content-Length',str(len(payload)));self.end_headers();self.wfile.write(payload)
    class Broker(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def do_POST(self):
            value=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            response=client.post('/workspaces/internal/checkout',headers={'authorization':self.headers.get('Authorization','')},json=value)
            self.send_response(response.status_code);self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(response.content)
    with http_server(GitHTTP) as upstream,http_server(Broker) as broker:
        monkeypatch.setenv('GITLAB_INSTANCES_JSON',json.dumps([INSTANCE|{'base_url':f'http://127.0.0.1:{upstream.server_port}'}]))
        secret='local-test-mcp-app-secret-at-least-32-bytes'
        monkeypatch.setenv('TERMINAL_SHARED_SECRET',secret)
        monkeypatch.setenv('TERMINAL_EXECUTION_ENABLED','true')
        sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
        monkeypatch.setenv('TERMINAL_MCP_URL',f'http://127.0.0.1:{port}/mcp');get_settings.cache_clear()
        config=TerminalSettings(terminal_shared_secret=secret,terminal_broker_url=f'http://127.0.0.1:{broker.server_port}',
            terminal_execution_mode='trusted_local',app_env='test',terminal_data_directory=tmp_path/'terminal',terminal_port=port)
        mcp,service=build_server(config)
        server=uvicorn.Server(uvicorn.Config(http_app(mcp,config),host='127.0.0.1',port=port,log_level='error'))
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        for _ in range(100):
            if server.started:break
            time.sleep(.05)
        assert server.started
        async def remote(provider,method,path,**kwargs):
            if '/repository/commits/' in path:return {'id':sha}
            return await gitlab[1](provider,method,path,**kwargs)
        monkeypatch.setattr(__import__('app.modules.integrations.transport',fromlist=['request']),'request',remote)
        try:yield service,sha
        finally:server.should_exit=True;thread.join(timeout=8)


def test_gitlab_checkout_agent_sources_reuse_permissions_and_close(client,account,workspace_stack):
    service,sha=workspace_stack
    alice=auth(account());bob=auth(account('bob'))
    connection=connect(client,alice);conversation=create(client,alice)
    selection={'app':'gitlab','connection_id':connection,'project_id':7,'ref':'main','paths':[],'mode':'workspace'}
    response=send(client,alice,conversation,content='login 함수 분석',apps=[selection])
    stream=events(response)
    assert stream[-1][0]=='done',response.text[-2500:]
    workspace=next(value for kind,value in stream if kind=='workspace')
    wid=workspace['id']
    assert workspace['source']['commit']==sha
    saved=client.get(f'/chat/conversations/{conversation}',headers=alice).json()['messages'][-1]
    assert saved['apps'][0]['workspace_id']==wid and saved['sources'][0]['commit']==sha
    assert saved['tools'] and '[GL1]' in saved['content']
    assert client.get('/workspaces',headers=bob,params={'conversation_id':conversation}).json()['items']==[]
    endpoint=f'/workspaces/{wid}/tools'
    assert client.post(endpoint,headers=bob,json={'name':'repo_map'}).status_code==404
    assert client.post(endpoint,headers=alice,json={'name':'terminal_exec','arguments':{'argv':['echo','no']}}).status_code==403
    symbols=client.post(endpoint,headers=alice,json={'name':'symbol_lookup','arguments':{'path':'main.py'}})
    assert symbols.status_code==200,symbols.text
    assert symbols.json()['symbols'][0]['name']=='login'
    assert client.post(endpoint,headers=alice,json={'name':'repo_map','arguments':{'workspace_id':'other'}}).status_code==403
    repeated=send(client,alice,conversation,content='구조도 확인',apps=[selection])
    assert next(value for kind,value in events(repeated) if kind=='workspace')['id']==wid
    with client.db_factory() as db:
        assert db.scalar(select(Admission).where(Admission.state.in_(['running','queued','tool_waiting']))) is None
    assert client.delete('/workspaces/'+wid,headers=alice).status_code==200
    assert not service.store.repo(wid).exists()


def test_agent_execution_releases_inference_permit_and_cleans_jobs(client,account,workspace_stack,monkeypatch):
    _,_=workspace_stack
    headers=auth(account())
    connection=connect(client,headers);conversation=create(client,headers)
    calls=[];tool_states=[]
    from app.modules.workspaces import service as workspaces
    original=workspaces.invoke
    async def observed(bind,row,name,args):
        if name=='terminal_read':
            with client.db_factory() as db:tool_states.append(db.scalar(select(Admission.state)))
        return await original(bind,row,name,args)
    monkeypatch.setattr(workspaces,'invoke',observed)
    async def model(connection,model,history,tools,settings):
        with client.db_factory() as db:
            calls.append(db.scalar(select(Admission.state)))
        if not any(m['role']=='tool' for m in history):
            return {'role':'assistant','content':'','tool_calls':[{'id':'test-run','type':'function','function':{'name':'terminal_exec',
                'arguments':{'argv':[sys.executable,'-c','print("real worker")'],'timeout_seconds':5}}}]}
        return {'role':'assistant','content':'명령 실행을 확인했습니다.','tool_calls':[]}
    monkeypatch.setattr(providers,'tool_reply',model)
    response=send(client,headers,conversation,apps=[{'app':'gitlab','connection_id':connection,'project_id':7,'ref':'main','paths':[],'mode':'execute'}])
    assert events(response)[-1][0]=='done',response.text[-2000:]
    assert calls==['running','running']
    assert tool_states and set(tool_states)=={'tool_waiting'}
    assert 'real worker' in response.text
    with client.db_factory() as db:
        assert db.scalar(select(GenerationRun)).status=='complete'
        assert db.scalar(select(Admission)).state=='complete'


def test_stop_during_command_cancels_worker_and_preserves_terminal_event(client,account,workspace_stack,monkeypatch):
    terminal,_=workspace_stack
    headers=auth(account());connection=connect(client,headers);conversation=create(client,headers)
    async def model(*args):
        return {'role':'assistant','content':'','tool_calls':[{'id':'sleep','type':'function','function':{'name':'terminal_exec',
            'arguments':{'argv':[sys.executable,'-c','import time; print("waiting",flush=True); time.sleep(20)']}}}]}
    monkeypatch.setattr(providers,'tool_reply',model)
    from app.modules.workspaces import service as workspaces
    from app.modules.chat.models import Conversation
    original=workspaces.invoke
    async def stop(bind,row,name,args):
        result=await original(bind,row,name,args)
        if name=='terminal_read':
            with client.db_factory() as db:
                db.get(Conversation,conversation).cancel_requested=True;db.commit()
        return result
    monkeypatch.setattr(workspaces,'invoke',stop)
    response=send(client,headers,conversation,apps=[{'app':'gitlab','connection_id':connection,'project_id':7,'ref':'main','paths':[],'mode':'execute'}])
    assert events(response)[-1][0]=='cancelled',response.text[-2000:]
    with terminal.store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]==0
    with client.db_factory() as db:
        assert db.scalar(select(Admission)).state=='cancelled'


def test_disconnect_closes_workspace_and_revokes_cached_source_access(client,account,workspace_stack):
    terminal,_=workspace_stack
    headers=auth(account());connection=connect(client,headers);conversation=create(client,headers)
    selected={'app':'gitlab','connection_id':connection,'project_id':7,'ref':'main','paths':[],'mode':'workspace'}
    response=send(client,headers,conversation,apps=[selected])
    wid=next(value['id'] for kind,value in events(response) if kind=='workspace')
    assert client.delete('/apps/connections/'+connection,headers=headers).status_code==200
    assert not terminal.store.repo(wid).exists()
    assert client.post(f'/workspaces/{wid}/tools',headers=headers,json={'name':'repo_map'}).status_code==409


def test_internal_broker_and_model_permissions_are_not_public(client,account,workspace_stack):
    headers=auth(account())
    assert client.post('/workspaces/internal/checkout',headers=headers,json={'workspace_id':str(__import__('uuid').uuid4()),'ticket':'x'*43}).status_code==401
    connection=connect(client,headers);conversation=create(client,headers)
    with client.db_factory() as db:
        from app.modules.chat.registry import ConnectionInput
        from app.modules.chat.models import ConnectionSetting
        value=ConnectionInput(id='no-tools',name='No Tools',provider='ollama',base_url='http://127.0.0.1:11434',model='local')
        db.add(ConnectionSetting(id='no-tools',configuration=value.model_dump_json(),revision=1));db.commit()
    response=send(client,headers,conversation,connection_id='no-tools',model='local',apps=[{'app':'gitlab','connection_id':connection,'project_id':7,'ref':'main','paths':[],'mode':'workspace'}])
    assert response.status_code==422
