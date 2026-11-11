import asyncio
import hashlib
import json
from pathlib import Path
import sys
import time
from uuid import uuid4

import pytest
from mcp import Client

from terminal_mcp.auth import issue,principal,verify
from terminal_mcp.config import TerminalSettings
from terminal_mcp.server import build_server,http_app
from terminal_mcp.analysis import safe_path,read_text


@pytest.fixture
def terminal(tmp_path):
    settings=TerminalSettings(terminal_shared_secret='local-test-only-terminal-secret-32-bytes',terminal_data_directory=tmp_path,
        terminal_execution_mode='trusted_local',app_env='test',terminal_job_timeout_seconds=2,terminal_max_output_bytes=65536)
    server,service=build_server(settings)
    workspace=str(uuid4())
    ctx={'sub':'alice','conversation':'chat-a','workspace':workspace,'execute':True,'provision':True}
    asyncio.run(service.create(ctx))
    repo=service.store.repo(workspace);repo.mkdir()
    (repo/'main.py').write_text('import os\n\ndef login(user):\n    return check(user)\n\ndef check(user):\n    return user.active\n')
    (repo/'client.ts').write_text('export function login(user: string) { return fetch(user); }\nconst logout = () => true;\n')
    (repo/'.env').write_text('SECRET=must-not-be-read')
    (repo/'node_modules').mkdir();(repo/'node_modules'/'lib.js').write_text('doNotRead()')
    with service.store.db() as db:
        db.execute("UPDATE workspaces SET status='ready' WHERE id=?",(workspace,))
        text=(repo/'main.py').read_text()
        db.execute('INSERT INTO baseline_files VALUES(?,?,?,?)',(workspace,'main.py',hashlib.sha256(text.encode()).hexdigest(),text))
    return settings,server,service,ctx


def test_signed_grants_expiry_audience_and_identity(terminal):
    settings,_,_,ctx=terminal
    secret=settings.terminal_shared_secret.get_secret_value()
    token=issue(secret,user='alice',conversation='chat-a',workspace=ctx['workspace'])
    assert verify(token,secret)['sub']=='alice'
    for other in ['wrong-secret-but-still-at-least-32-bytes','different-audience']:
        with pytest.raises(PermissionError):
            verify(token,other if other!='different-audience' else secret,audience='elsewhere' if other=='different-audience' else 'llm-terminal')
    with pytest.raises(PermissionError):
        verify(issue(secret,user='alice',conversation='chat-a',workspace=ctx['workspace'],seconds=-1),secret)


def test_analysis_excludes_secrets_and_caches_working_copy_symbols(terminal):
    settings,_,service,ctx=terminal
    wid=ctx['workspace']
    mapped=service.analyzer.repo_map(wid)
    assert {file['path'] for file in mapped['files']}=={'main.py','client.ts'}
    assert service.analyzer.search(wid,'SECRET')['matches']==[]
    result=service.analyzer.symbols(wid,'main.py')
    assert result['parser']=='python_ast' and result['cached'] is False
    assert next(s for s in result['symbols'] if s['name']=='login')['references']==['check']
    assert service.analyzer.symbols(wid,'main.py')['cached'] is True
    (service.store.repo(wid)/'main.py').write_text('def changed():\n    pass\n')
    changed=service.analyzer.symbols(wid,'main.py')
    assert changed['cached'] is False and changed['symbols'][0]['name']=='changed'
    ts=service.analyzer.symbols(wid,'client.ts')
    assert ts['parser']=='tree_sitter' and {s['name'] for s in ts['symbols']}=={'login','logout'}


@pytest.mark.parametrize('path',['../other.py','/etc/passwd','.env','.git/config','node_modules/lib.js','a/../../b.py'])
def test_path_boundaries_and_sensitive_files(terminal,path):
    settings,_,service,ctx=terminal
    with pytest.raises((ValueError,OSError)):
        read_text(service.store.repo(ctx['workspace']),path,settings)


def test_symlink_escape_and_invalid_utf8_are_rejected(terminal,tmp_path):
    settings,_,service,ctx=terminal
    root=service.store.repo(ctx['workspace'])
    (tmp_path/'private.py').write_text('secret')
    (root/'link.py').symlink_to(tmp_path/'private.py')
    (root/'dir').symlink_to(tmp_path,target_is_directory=True)
    (root/'binary.py').write_bytes(b'\xff\x00')
    for path in ['link.py','dir/private.py','binary.py']:
        with pytest.raises(ValueError):read_text(root,path,settings)


def test_mcp_tools_contract_permissions_and_edits(terminal):
    settings,server,service,ctx=terminal
    async def exercise():
        token=principal.set(ctx)
        try:
            async with asyncio.timeout(10),Client(server,mode='2026-07-28') as client:
                tools=(await client.list_tools()).tools
                names={t.name for t in tools}
                assert {'repo_map','gitlab_checkout','terminal_exec','terminal_input','workspace_diff'}<=names
                result=await client.call_tool('repo_map',{'workspace_id':ctx['workspace']})
                assert not result.is_error and result.structured_content['total_files']==2
                wrong=await client.call_tool('repo_map',{'workspace_id':str(uuid4())})
                assert wrong.is_error
                first=await client.call_tool('workspace_read_file',{'workspace_id':ctx['workspace'],'path':'main.py'})
                assert first.structured_content['working_copy'] is False
                edited=await client.call_tool('workspace_write_file',{'workspace_id':ctx['workspace'],'path':'main.py','content':'def fixed():\n    pass\n','expected_hash':first.structured_content['content_hash']})
                assert not edited.is_error
                stale=await client.call_tool('workspace_write_file',{'workspace_id':ctx['workspace'],'path':'main.py','content':'lost','expected_hash':'wrong'})
                assert stale.is_error
                diff=await client.call_tool('workspace_diff',{'workspace_id':ctx['workspace']})
                assert '+def fixed' in diff.structured_content['diff']
                principal.set(ctx|{'execute':False})
                denied=await client.call_tool('terminal_exec',{'workspace_id':ctx['workspace'],'argv':['echo','forbidden']})
                assert denied.is_error
                principal.set(ctx)
                settings.terminal_execution_mode='disabled'
                denied_edit=await client.call_tool('workspace_write_file',{'workspace_id':ctx['workspace'],'path':'new.py','content':'no','expected_hash':''})
                assert denied_edit.is_error and not (service.store.repo(ctx['workspace'])/'new.py').exists()
        finally:principal.reset(token)
    asyncio.run(exercise())


def test_runner_output_stdin_timeout_cancel_and_environment(terminal,monkeypatch):
    _,_,service,ctx=terminal
    monkeypatch.setenv('AUTH_PROXY_SECRET','never-inherit-application-secret')
    async def wait(job):
        for _ in range(60):
            value=service.runner.read(ctx,job)
            if value['status']!='running':return value
            await asyncio.sleep(.05)
        pytest.fail('Job did not finish')
    async def exercise():
        result=await service.runner.start(ctx,ctx['workspace'],[sys.executable,'-u','-c',
            'import os; print("한글",flush=True); print(input(),flush=True); print(os.getenv("AUTH_PROXY_SECRET"),flush=True)'])
        job=result['job_id'];await asyncio.sleep(.15)
        await service.runner.input(ctx,job,'hello\n')
        output=await wait(job)
        text=''.join(e['text'] for e in output['events'])
        assert output['status']=='succeeded' and output['exit_code']==0
        assert '한글' in text and 'hello' in text and 'None' in text and 'never-inherit' not in text
        with pytest.raises(PermissionError):service.runner.read(ctx|{'sub':'bob'},job)
        timed=await service.runner.start(ctx,ctx['workspace'],[sys.executable,'-c','import time; time.sleep(10)'],timeout_seconds=1)
        assert (await wait(timed['job_id']))['status']=='timed_out'
        cancelled=await service.runner.start(ctx,ctx['workspace'],[sys.executable,'-c','import time; time.sleep(10)'])
        await asyncio.sleep(.1)
        assert (await service.runner.cancel(ctx,cancelled['job_id']))['status']=='cancelled'
        assert service.runner.tasks=={}
    asyncio.run(exercise())


def test_output_is_bounded_and_exit_failure_is_not_success(terminal):
    _,_,service,ctx=terminal
    async def exercise():
        result=await service.runner.start(ctx,ctx['workspace'],[sys.executable,'-c','print("x"*100000);raise SystemExit(7)'])
        await asyncio.sleep(.7)
        output=service.runner.read(ctx,result['job_id'],max_bytes=32000)
        assert output['status']=='failed' and output['exit_code']==7 and output['truncated']
        with service.store.db() as db:
            assert db.execute("SELECT COUNT(*) FROM jobs WHERE status='running'").fetchone()[0]==0
    asyncio.run(exercise())


def test_docker_profile_has_no_host_privileges_and_no_credentials(terminal):
    settings,_,service,ctx=terminal
    settings.terminal_execution_mode='docker';settings.terminal_workspace_host_path='/srv/llm/terminal'
    argv,cwd,name=service.runner.command(ctx['workspace'],['python','main.py'],'.',str(uuid4()),False)
    assert '--network=none' in argv and '--read-only' in argv and '--cap-drop=ALL' in argv
    assert '--user' in argv and '10001:10001' in argv and '--pids-limit=128' in argv
    assert not any('/var/run/docker.sock' in a or 'AUTH_PROXY_SECRET' in a for a in argv)


def test_http_mcp_sdk_transport_and_authentication(terminal):
    settings,server,service,ctx=terminal
    import httpx2
    from mcp.client.streamable_http import streamable_http_client
    app=http_app(server,settings)
    async def exercise():
        token=issue(settings.terminal_shared_secret.get_secret_value(),user=ctx['sub'],conversation=ctx['conversation'],workspace=ctx['workspace'])
        async with app.router.lifespan_context(app):
            async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app),base_url='http://testserver',headers={'Authorization':'Bearer '+token}) as http:
                denied=await http.post('/mcp',headers={'Authorization':'Bearer wrong'},json={})
                assert denied.status_code==401
                async with asyncio.timeout(10),Client(streamable_http_client('http://testserver/mcp',http_client=http),mode='2026-07-28') as client:
                    result=await client.call_tool('repo_map',{'workspace_id':ctx['workspace']})
                    assert not result.is_error and result.structured_content['total_files']==2
    asyncio.run(exercise())


def test_stdio_official_sdk_roundtrip(tmp_path):
    from mcp.client.stdio import StdioServerParameters
    secret='local-stdio-secret-at-least-32-bytes'
    wid=str(uuid4())
    token=issue(secret,user='alice',conversation='stdio-chat',workspace=wid,provision=True)
    async def exercise():
        params=StdioServerParameters(command=sys.executable,args=[str(Path(__file__).parents[1]/'terminal_main.py'),'--transport','stdio'],
            env={'APP_ENV':'test','TERMINAL_DATA_DIRECTORY':str(tmp_path),'TERMINAL_SHARED_SECRET':secret,'TERMINAL_STDIO_TOKEN':token,'TERMINAL_EXECUTION_MODE':'disabled'})
        async with asyncio.timeout(15),Client(params,mode='2026-07-28') as client:
            tools=(await client.list_tools()).tools
            assert any(tool.name=='workspace_create' for tool in tools)
            result=await client.call_tool('workspace_create',{})
            assert not result.is_error and result.structured_content['id']==wid
    asyncio.run(exercise())


def test_agent_context_compaction_keeps_tool_call_result_pairs():
    from app.modules.workspaces.agent import compact,message_cost
    messages=[{'role':'system','content':'rules'},{'role':'user','content':'question'},
        {'role':'assistant','content':'','tool_calls':[{'id':'old'}]},{'role':'tool','content':'x'*600,'tool_call_id':'old'},
        {'role':'assistant','content':'','tool_calls':[{'id':'new'}]},{'role':'tool','content':'evidence','tool_call_id':'new'}]
    assert compact(messages,500)==1
    assert message_cost(messages)<=500 and messages[1]['content']=='question'
    assert [m.get('tool_call_id') for m in messages if m['role']=='tool']==['new']
    # Large command arguments count even when assistant content is empty.
    with_arguments=[{'role':'assistant','content':'','tool_calls':[{'function':{'arguments':{'command':'x'*1000}}}]}]
    assert message_cost(with_arguments)>1000


@pytest.mark.parametrize('provider',['vllm','ollama'])
def test_tool_provider_contract_formats_and_result_normalization(monkeypatch,provider):
    import httpx
    from app.modules.chat import providers
    from app.core.config import get_settings
    captured=[]
    def handler(request):
        captured.append(json.loads(request.content))
        message={'content':'','tool_calls':[{'id':'one','function':{'name':'code_search','arguments':'{"query":"login"}' if provider=='vllm' else {'query':'login'}}}]}
        return httpx.Response(200,json={'choices':[{'message':message}]} if provider=='vllm' else {'message':message,'done':True})
    monkeypatch.setattr(providers,'client',lambda *args:httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    connection=providers.Connection(id='tools',name='Tools',provider=provider,base_url='http://local',model='local',supports_tools=True)
    history=[{'role':'user','content':'question'},{'role':'assistant','content':'','tool_calls':[{'id':'before','function':{'name':'repo_map','arguments':{'limit':2}}}]},
        {'role':'tool','tool_call_id':'before','tool_name':'repo_map','content':'{}'}]
    reply=asyncio.run(providers.tool_reply(connection,'local',history,[{'type':'function','function':{'name':'code_search','parameters':{'type':'object'}}}],get_settings()))
    assert reply['tool_calls'][0]['function']['arguments']=={'query':'login'}
    args=captured[0]['messages'][1]['tool_calls'][0]['function']['arguments']
    assert isinstance(args,str if provider=='vllm' else dict)
    assert captured[0]['stream'] is False and captured[0]['tools']
