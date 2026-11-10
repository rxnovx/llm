import asyncio
from contextlib import contextmanager
import json
import socket
import threading
import time
import pytest
import uvicorn
from mcp.server import MCPServer
from sqlalchemy import select
from app.core.config import get_settings
from app.modules.assistant import mcp
from app.modules.auth.models import User
from app.modules.chat import providers
from test_chat import auth,create,events,send

def admin(client):
    with client.db_factory() as db:
        user=db.scalar(select(User).where(User.username=='alice'));user.is_global_admin=True;db.commit()

@contextmanager
def remote_mcp(label):
    server=MCPServer('test-'+label);calls=[];headers=[]
    @server.tool()
    def lookup(query:str)->dict[str,object]:
        calls.append(('read',query));return {'text':label+' '+query}
    @server.tool()
    def change(value:str)->dict[str,object]:
        calls.append(('write',value));return {'changed':True}
    @server.tool()
    def hidden()->dict[str,object]:
        calls.append(('hidden',''));return {'secret':'never exposed'}
    app=server.streamable_http_app(stateless_http=True,json_response=True)
    class Capture:
        def __init__(self,app):self.app=app
        async def __call__(self,scope,receive,send):
            if scope['type']=='http':headers.append(dict(scope['headers']).get(b'authorization'))
            await self.app(scope,receive,send)
    app.add_middleware(Capture)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    running=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    thread=threading.Thread(target=running.run,daemon=True);thread.start()
    for _ in range(100):
        if running.started:break
        time.sleep(.02)
    assert running.started
    try:yield f'http://127.0.0.1:{port}/mcp',calls,headers
    finally:running.should_exit=True;thread.join(timeout=5)

def registration(id,name,url):return {'id':id,'name':name,'url':url,'read_tools':['lookup'],'write_tools':['change'],'allowed_users':['alice'],'secret_ref':'internal'}

def test_multiple_real_mcp_servers_namespace_tools_keys_and_live_permissions(client,account,monkeypatch):
    headers=auth(account());other=auth(account('bob'));admin(client)
    monkeypatch.setenv('MCP_SECRETS_JSON','{"internal":"server-only-test-token"}');get_settings.cache_clear()
    with remote_mcp('Alpha') as (url1,calls1,auth1),remote_mcp('Beta') as (url2,calls2,auth2):
        for id,name,url in [('alpha','Alpha',url1),('beta','Beta',url2)]:
            r=client.put('/assistant/mcp/servers/'+id,headers=headers,json=registration(id,name,url));assert r.status_code==200,r.text
        assert client.get('/assistant/catalog',headers=other).json()['servers']==[]
        assert client.put('/assistant/mcp/servers/evil',headers=other,json=registration('evil','Evil',url1)).status_code==403
        with client.db_factory() as db:bind=db.get_bind();user_id=db.scalar(select(User.id).where(User.username=='alice'));bob_id=db.scalar(select(User.id).where(User.username=='bob'))
        tools,mapping=asyncio.run(mcp.manifest(bind,user_id,['alpha','beta']))
        assert len(tools)==2 and len({t['function']['name'] for t in tools})==2
        assert 'hidden' not in json.dumps(tools) and 'server-only' not in json.dumps(tools)
        for name in mapping:asyncio.run(mcp.call(bind,user_id,mapping,name,{'query':'hello'}))
        assert calls1==[('read','hello')] and calls2==[('read','hello')]
        assert set(auth1+auth2)=={b'Bearer server-only-test-token'}
        with pytest.raises(Exception) as error:asyncio.run(mcp.manifest(bind,bob_id,['alpha']))
        assert error.value.status_code==403
        saved=client.get('/assistant/mcp/servers',headers=headers).json()['items'][0]
        client.put('/assistant/mcp/servers/'+saved['id'],headers=headers,json=saved|{'enabled':False})
        name=next(n for n,(s,_,_) in mapping.items() if s.id==saved['id'])
        with pytest.raises(Exception) as error:asyncio.run(mcp.call(bind,user_id,mapping,name,{'query':'no'}))
        assert error.value.status_code==403

def test_general_tool_loop_and_write_opt_in(client,account,monkeypatch):
    headers=auth(account());admin(client);conversation=create(client,headers)
    monkeypatch.setenv('MCP_SECRETS_JSON','{"internal":"server-only-test-token"}');get_settings.cache_clear()
    with remote_mcp('Tools') as (address,calls,_):
        client.put('/assistant/mcp/servers/tools',headers=headers,json=registration('tools','Tools',address))
        schemas=[]
        async def model(connection,model,history,tools,settings):
            schemas.append(tools)
            if not any(m['role']=='tool' for m in history):
                name=next(t['function']['name'] for t in tools if 'lookup' in t['function']['description'])
                return {'role':'assistant','content':'','tool_calls':[{'id':'read','type':'function','function':{'name':name,'arguments':{'query':'자료'}}}]}
            return {'role':'assistant','content':'연결된 도구를 확인했습니다.','tool_calls':[]}
        monkeypatch.setattr(providers,'tool_reply',model)
        r=send(client,headers,conversation,content='자료 조회',assistant={'mcp_ids':['tools']})
        assert events(r)[-1][0]=='done',r.text[-1000:]
        assert calls==[('read','자료')] and all('change' not in json.dumps(s) for s in schemas)
        with client.db_factory() as db:bind=db.get_bind();uid=db.scalar(select(User.id).where(User.username=='alice'))
        _,mapping=asyncio.run(mcp.manifest(bind,uid,['tools'],True))
        write=next(name for name,(_,tool,_) in mapping.items() if tool=='change')
        with pytest.raises(Exception) as error:asyncio.run(mcp.call(bind,uid,mapping,write,{'value':'x'}))
        assert error.value.status_code==403
        asyncio.run(mcp.call(bind,uid,mapping,write,{'value':'x'},True));assert calls[-1]==('write','x')

def test_preferences_memory_scope_revision_and_actual_model_input(client,account,monkeypatch):
    alice,bob=auth(account()),auth(account('bob'));conversation=create(client,alice)
    row=client.post('/assistant/memories',headers=alice,json={'content':'나는 SQLite를 사용한다.'}).json()
    assert client.get('/assistant/memories',headers=bob).json()['items']==[]
    assert client.put('/assistant/preferences',headers=alice,json={'instructions':'한국어로 짧게 답변','memory_enabled':True,'revision':0}).status_code==200
    assert client.put('/assistant/preferences',headers=alice,json={'instructions':'stale','revision':0}).status_code==409
    captured=[]
    async def stream(connection,model,history,settings):captured.append(history);yield '확인했습니다.'
    monkeypatch.setattr(providers,'stream_reply',stream)
    r=send(client,alice,conversation)
    assert events(r)[-1][0]=='done',r.text[-1000:]
    assert 'SQLite' in captured[0][0]['content'] and '한국어로' in captured[0][0]['content']
    client.put('/assistant/preferences',headers=alice,json={'instructions':'','memory_enabled':False,'revision':1})
    client.delete('/assistant/memories/'+row['id'],headers=alice)
    assert client.get('/assistant/memories',headers=alice).json()['items']==[]

def test_registration_conflicts_credentials_urls_and_input_validation(client,account):
    headers=auth(account());admin(client)
    invalid=registration('bad','Bad','http://user:password@local/mcp')
    r=client.put('/assistant/mcp/servers/bad',headers=headers,json=invalid)
    assert r.status_code==422 and 'user:password' not in r.text
    r=client.put('/assistant/mcp/servers/test',headers=headers,json=registration('test','Test','http://local/mcp'))
    assert r.status_code==200
    assert client.put('/assistant/mcp/servers/test',headers=headers,json=registration('test','Test','http://local/mcp')).status_code==409

def test_external_mcp_schema_refs_are_not_fetched(client,account):
    headers=auth(account());admin(client)
    server=mcp.ServerInput(**registration('test','Test','http://local/mcp'))
    client.put('/assistant/mcp/servers/test',headers=headers,json=server.model_dump())
    server.revision=1
    with client.db_factory() as db:bind=db.get_bind();uid=db.scalar(select(User.id).where(User.username=='alice'))
    with pytest.raises(Exception) as error:asyncio.run(mcp.call(bind,uid,{'name':(server,'lookup',{'$ref':'https://forbidden/schema'})},'name',{}))
    assert error.value.status_code==422
