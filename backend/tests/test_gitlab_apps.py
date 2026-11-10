"""OAuth and live GitLab contracts use local protocol doubles, never real tokens."""
import asyncio
import base64
import hashlib
import json
import time
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

from cryptography.fernet import Fernet
from fastapi import HTTPException
import httpx
import pytest
from sqlalchemy import select, update

from app.core.config import get_settings
from app.modules.chat import providers
from app.modules.chat.models import Admission, Conversation
from app.modules.integrations import config, service, transport
from app.modules.integrations.models import AppConnection, MessageContext, OAuthAttempt
from app.modules.integrations.tools import GitlabSelection, read_context
from test_chat import auth, create, events, send

INSTANCE = {'id':'office','name':'사내 GitLab','base_url':'https://gitlab.example.internal','client_id':'test-client','secret_ref':'office'}
BROWSER = 'only-a-local-test-browser-nonce-1234567890'
SHA = 'a'*40


@pytest.fixture
def gitlab(client,monkeypatch):
    monkeypatch.setenv('GITLAB_INSTANCES_JSON',json.dumps([INSTANCE]))
    monkeypatch.setenv('GITLAB_CLIENT_SECRETS_JSON',json.dumps({'office':'local-test-client-secret'}))
    monkeypatch.setenv('INTEGRATIONS_ENCRYPTION_KEYS',json.dumps([Fernet.generate_key().decode()]))
    get_settings.cache_clear()
    calls = []
    async def remote(provider,method,path,**kwargs):
        calls.append((method,path,kwargs))
        if path=='/oauth/token':
            number = len([call for call in calls if call[1]=='/oauth/token'])
            return {'access_token':f'local-access-{number}','refresh_token':f'local-refresh-{number}',
                    'token_type':'Bearer','scope':'read_user read_api read_repository','expires_in':7200}
        if path=='/oauth/revoke':
            return {}
        if path=='/api/v4/user':
            return {'id':42,'username':'alice-gl','name':'Alice GitLab'}
        if path=='/api/v4/projects':
            return [{'id':7,'path_with_namespace':'team/service','default_branch':'main'}]
        if path in {'/api/v4/projects/7','/api/v4/projects/team%2Fservice'}:
            return {'id':7,'path_with_namespace':'team/service','default_branch':'main','web_url':'https://untrusted.example/ignored'}
        if '/repository/commits/' in path:
            return {'id':SHA}
        if path.endswith('/branches'):
            return [{'name':'main'},{'name':'feature/한글'}]
        if path.endswith('/tree'):
            return [{'name':'main.py','path':'app/main.py','type':'blob'},
                    {'name':'.env','path':'.env','type':'blob'},{'name':'app','path':'app','type':'tree'}]
        if path.endswith('/raw'):
            assert kwargs['params']=={'ref':SHA}
            return 'import os\n\ndef login(user):\n    return user.is_active\n'
        raise AssertionError(path)
    monkeypatch.setattr(transport,'request',remote)
    return calls,remote


def begin(client,headers,**extra):
    response = client.post('/apps/gitlab/office/authorize',headers=headers|{'x-oauth-browser':BROWSER},json=extra)
    assert response.status_code==200,response.text
    return parse_qs(urlsplit(response.json()['authorize_url']).query),response.json()


def finish(client,headers,state,**extra):
    return client.post('/apps/gitlab/oauth/complete',headers=headers|{'x-oauth-browser':BROWSER},json={'state':state,'code':'test-code',**extra})


def connect(client,headers):
    query,_ = begin(client,headers)
    response = finish(client,headers,query['state'][0])
    assert response.status_code==200,response.text
    return response.json()['connection']['id']


def selection(connection,**extra):
    return {'app':'gitlab','connection_id':connection,'project_id':7,'ref':'feature/한글','paths':['app/main.py'],**extra}


def test_catalog_requires_auth_and_empty_config_needs_no_crypto(client,account,monkeypatch):
    assert client.get('/apps/catalog').status_code==401
    headers = auth(account())
    monkeypatch.setenv('GITLAB_INSTANCES_JSON','[]')
    get_settings.cache_clear()
    monkeypatch.setattr(config,'vault',lambda:pytest.fail('Empty catalog must not import cryptography'))
    assert client.get('/apps/catalog',headers=headers).json()['apps'][0]['providers']==[]


def test_oauth_pkce_browser_binding_encryption_and_replay(client,account,gitlab):
    headers = auth(account())
    query,value = begin(client,headers,popup=True)
    assert query['scope']==['read_user read_api read_repository'] and query['code_challenge_method']==['S256']
    assert query['redirect_uri']==['http://localhost:3000/apps/gitlab/callback']
    assert 'secret' not in value['authorize_url']
    with client.db_factory() as db:
        attempt = db.get(OAuthAttempt,config.digest(query['state'][0]))
        verifier = config.unseal(attempt.verifier_cipher)
        assert query['code_challenge']==[base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b'=').decode()]
        assert verifier not in attempt.verifier_cipher
    wrong = client.post('/apps/gitlab/oauth/complete',headers=headers|{'x-oauth-browser':'wrong-browser'},
                        json={'state':query['state'][0],'code':'test-code'})
    assert wrong.status_code==400 and gitlab[0]==[]
    response = finish(client,headers,query['state'][0])
    assert response.status_code==200,response.text
    assert response.json()['popup'] is True and response.json()['connection']['username']=='alice-gl'
    assert finish(client,headers,query['state'][0]).status_code==400
    assert len([call for call in gitlab[0] if call[1]=='/oauth/token'])==1
    with client.db_factory() as db:
        row = db.scalar(select(AppConnection))
        assert 'local-access' not in row.access_cipher and config.unseal(row.access_cipher)=='local-access-1'
        assert 'local-refresh' not in row.refresh_cipher
    catalog = client.get('/apps/catalog',headers=headers).text
    assert 'local-access' not in catalog and 'local-test-client-secret' not in catalog and 'cipher' not in catalog


def test_oauth_survives_app_token_rotation_but_rejects_other_login(client,account,gitlab):
    tokens = account()
    query,_ = begin(client,auth(tokens))
    rotated = client.post('/auth/refresh',json={'refresh_token':tokens['refresh_token']}).json()
    other = client.post('/auth/login',data={'username':'alice','password':'StrongPass123!'}).json()
    assert finish(client,auth(other),query['state'][0]).status_code==400
    assert finish(client,auth(rotated),query['state'][0]).status_code==200


def test_oauth_cross_user_expiry_denial_and_changed_provider(client,account,gitlab,monkeypatch):
    alice,bob = auth(account()),auth(account('bob'))
    query,_ = begin(client,alice)
    assert finish(client,bob,query['state'][0]).status_code==400
    with client.db_factory() as db:
        db.get(OAuthAttempt,config.digest(query['state'][0])).expires_at = int(time.time())-1
        db.commit()
    assert finish(client,alice,query['state'][0]).status_code==400
    query,_ = begin(client,alice)
    assert finish(client,alice,query['state'][0],code='',error='access_denied').status_code==400
    query,_ = begin(client,alice)
    monkeypatch.setenv('GITLAB_INSTANCES_JSON',json.dumps([INSTANCE|{'base_url':'https://different.internal'}]))
    get_settings.cache_clear()
    assert finish(client,alice,query['state'][0]).status_code==409
    assert gitlab[0]==[]


def test_logout_during_token_exchange_does_not_save_connection(client,account,gitlab,monkeypatch):
    tokens = account()
    headers = auth(tokens)
    query,_ = begin(client,headers)
    async def remote(provider,method,path,**kwargs):
        if path=='/oauth/token':
            from app.modules.auth.models import RefreshSession
            with client.db_factory() as db:
                db.execute(update(RefreshSession).values(revoked_at=int(time.time())))
                db.commit()
        return await gitlab[1](provider,method,path,**kwargs)
    monkeypatch.setattr(transport,'request',remote)
    assert finish(client,headers,query['state'][0]).status_code==401
    with client.db_factory() as db:
        assert db.scalar(select(AppConnection)) is None
    assert any(call[1]=='/oauth/revoke' for call in gitlab[0])


def test_own_connection_permissions_browsing_and_local_disconnect(client,account,gitlab,monkeypatch):
    alice,bob = auth(account()),auth(account('bob'))
    connection = connect(client,alice)
    path = f'/apps/connections/{connection}'
    for suffix in ['/projects','/projects/7/tree','/projects/7/branches']:
        assert client.get(path+suffix,headers=bob).status_code==404
        assert client.get(path+suffix,headers=alice).status_code==200
    tree = client.get(path+'/projects/7/tree',headers=alice).json()['items']
    assert next(item for item in tree if item['name']=='.env')['selectable'] is False
    assert client.get(path+'/projects/7/tree?path=../secret',headers=alice).status_code==422
    assert client.delete(path,headers=bob).status_code==404
    async def offline(*args,**kwargs):
        raise transport.GitlabError('GitLab 서버에 연결할 수 없습니다.')
    monkeypatch.setattr(transport,'request',offline)
    response = client.delete(path,headers=alice)
    assert response.status_code==200 and response.json()=={'disconnected':True,'remote_revoked':False}
    assert client.get(path+'/projects',headers=alice).status_code==404


def test_refresh_is_serialized_and_never_uses_another_users_token(client,account,gitlab,monkeypatch):
    tokens = account()
    connection = connect(client,auth(tokens))
    with client.db_factory() as db:
        row = db.get(AppConnection,connection)
        row.expires_at=0
        user_id,bind = row.user_id,db.bind
        db.commit()
    async def remote(provider,method,path,**kwargs):
        if path=='/oauth/token':
            await asyncio.sleep(.2)
        return await gitlab[1](provider,method,path,**kwargs)
    monkeypatch.setattr(transport,'request',remote)
    async def concurrent():
        return await asyncio.gather(*(service.credentials(bind,user_id,connection) for _ in range(4)))
    values = asyncio.run(concurrent())
    assert {item.token for item in values}=={'local-access-2'}
    refreshes = [call for call in gitlab[0] if call[1]=='/oauth/token' and call[2]['form']['grant_type']=='refresh_token']
    assert len(refreshes)==1 and refreshes[0][2]['form']['refresh_token']=='local-refresh-1'


def test_refresh_rejection_requires_reconnect_without_disclosing_errors(client,account,gitlab,monkeypatch):
    headers = auth(account())
    connection = connect(client,headers)
    with client.db_factory() as db:
        db.get(AppConnection,connection).expires_at=0
        db.commit()
    async def rejected(*args,**kwargs):
        raise transport.GitlabError('GitLab 인증이 만료되었습니다. 다시 연결해 주세요.',400)
    monkeypatch.setattr(transport,'request',rejected)
    assert client.get(f'/apps/connections/{connection}/projects',headers=headers).status_code==502
    status = client.get('/apps/catalog',headers=headers).json()['apps'][0]['connections'][0]['status']
    assert status=='needs_reconnect'


def test_explicit_app_fetch_sources_budget_and_persistence(client,account,gitlab,monkeypatch):
    headers = auth(account())
    connection = connect(client,headers)
    conversation = create(client,headers)
    captured = []
    async def model(connection,model,history,settings):
        captured.extend(history)
        yield '로그인은 활성 사용자만 허용합니다. [GL1]'
    monkeypatch.setattr(providers,'stream_reply',model)
    response = send(client,headers,conversation,content='login 함수 분석',apps=[selection(connection)])
    stream = events(response)
    assert stream[-1][0]=='done',response.text
    assert [kind for kind,_ in stream].index('sources') < [kind for kind,_ in stream].index('delta')
    source = next(data['sources'][0] for kind,data in stream if kind=='sources')
    assert source['commit']==SHA and source['start_line']==1 and source['end_line']==4
    assert source['url'].startswith(INSTANCE['base_url']+'/team/service/-/blob/'+SHA)
    assert any('def login(user)' in item['content'] for item in captured)
    assert all('local-access' not in item['content'] for item in captured)
    saved = client.get(f'/chat/conversations/{conversation}',headers=headers).json()['messages'][-1]
    assert saved['sources']==[source] and saved['apps']==[selection(connection)]
    assert send(client,headers,conversation,content='login 함수 분석',retry=True).status_code==200
    assert len([call for call in gitlab[0] if call[1].endswith('/raw')])==2
    assert client.delete(f'/chat/conversations/{conversation}',headers=headers).status_code==204
    with client.db_factory() as db:
        assert db.scalar(select(MessageContext)) is None


def test_literal_mention_does_not_invoke_tools_and_failures_restore_retry_sources(client,account,gitlab,monkeypatch):
    headers = auth(account())
    connection = connect(client,headers)
    conversation = create(client,headers)
    assert events(send(client,headers,conversation,content='@GitLab 단어만 입력'))[-1][0]=='done'
    assert not any(call[1].endswith('/raw') for call in gitlab[0])
    send(client,headers,conversation,content='코드 확인',apps=[selection(connection)])
    before = client.get(f'/chat/conversations/{conversation}',headers=headers).json()['messages'][-1]
    async def denied(*args,**kwargs):
        raise transport.GitlabError('접근 권한이 없습니다.',403)
    monkeypatch.setattr(transport,'request',denied)
    response = send(client,headers,conversation,content='코드 확인',retry=True)
    assert events(response)[-1][0]=='error'
    after = client.get(f'/chat/conversations/{conversation}',headers=headers).json()['messages'][-1]
    assert after==before
    with client.db_factory() as db:
        assert db.scalar(select(Admission).where(Admission.state.in_(['queued','running']))) is None


@pytest.mark.parametrize('path',['../main.py','/main.py','a//b.py','a/%2e%2e/b.py','.env','.env.production','secrets.pem','node_modules/a.js'])
def test_sensitive_and_traversal_paths_rejected(path):
    with pytest.raises(ValueError):
        GitlabSelection.model_validate(selection(str(uuid4()),paths=[path]))


def test_disconnect_mid_read_stops_before_model_and_releases_queue(client,account,gitlab,monkeypatch):
    headers = auth(account())
    connection = connect(client,headers)
    conversation = create(client,headers)
    async def remote(provider,method,path,**kwargs):
        if path.endswith('/raw'):
            with client.db_factory() as db:
                db.delete(db.get(AppConnection,connection)); db.commit()
        return await gitlab[1](provider,method,path,**kwargs)
    monkeypatch.setattr(transport,'request',remote)
    monkeypatch.setattr(providers,'stream_reply',lambda *args:pytest.fail('Disconnected sources cannot reach model'))
    response = send(client,headers,conversation,apps=[selection(connection)])
    assert events(response)[-1][0]=='error'
    with client.db_factory() as db:
        assert db.scalar(select(Admission).where(Admission.state.in_(['queued','running']))) is None


@pytest.mark.parametrize('reason',['stop','timeout'])
def test_slow_gitlab_read_is_cancelled_and_releases_admission(client,account,gitlab,monkeypatch,reason):
    headers = auth(account())
    connection = connect(client,headers)
    conversation = create(client,headers)
    cancelled = []
    if reason=='timeout':
        import app.modules.chat.router as chat_router
        settings = get_settings().model_copy(update={'llm_timeout_seconds':.1})
        monkeypatch.setattr(chat_router,'get_settings',lambda:settings)
    async def remote(provider,method,path,**kwargs):
        if path.endswith('/raw'):
            if reason=='stop':
                with client.db_factory() as db:
                    db.get(Conversation,conversation).cancel_requested=True
                    db.commit()
            try:
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise
        return await gitlab[1](provider,method,path,**kwargs)
    monkeypatch.setattr(transport,'request',remote)
    monkeypatch.setattr(providers,'stream_reply',lambda *args:pytest.fail('Stopped/timed-out sources cannot reach model'))
    response = send(client,headers,conversation,apps=[selection(connection)])
    assert events(response)[-1][0]==('cancelled' if reason=='stop' else 'error')
    assert cancelled==[True]
    with client.db_factory() as db:
        assert db.scalar(select(Admission).where(Admission.state.in_(['queued','running']))) is None


def test_one_401_refreshes_once_and_403_does_not_revoke_account(client,account,gitlab,monkeypatch):
    headers = auth(account())
    connection = connect(client,headers)
    calls = []
    async def remote(provider,method,path,**kwargs):
        if path=='/api/v4/projects':
            calls.append(kwargs['token'])
            if len(calls)==1:
                raise transport.GitlabError('만료',401)
        return await gitlab[1](provider,method,path,**kwargs)
    monkeypatch.setattr(transport,'request',remote)
    assert client.get(f'/apps/connections/{connection}/projects',headers=headers).status_code==200
    assert calls==['local-access-1','local-access-2']
    async def forbidden(*args,**kwargs):
        raise transport.GitlabError('접근 권한이 없습니다.',403)
    monkeypatch.setattr(transport,'request',forbidden)
    assert client.get(f'/apps/connections/{connection}/projects',headers=headers).status_code==403
    assert client.get('/apps/catalog',headers=headers).json()['apps'][0]['connections'][0]['status']=='connected'


def test_rotated_encryption_keys_decrypt_old_and_encrypt_new(client,account,gitlab,monkeypatch):
    headers = auth(account())
    connection = connect(client,headers)
    previous = json.loads(get_settings().integrations_encryption_keys.get_secret_value())
    new = Fernet.generate_key().decode()
    monkeypatch.setenv('INTEGRATIONS_ENCRYPTION_KEYS',json.dumps([new,*previous]))
    get_settings.cache_clear()
    assert client.get(f'/apps/connections/{connection}/projects',headers=headers).status_code==200
    with client.db_factory() as db:
        db.get(AppConnection,connection).expires_at=0
        db.commit()
    assert client.get(f'/apps/connections/{connection}/projects',headers=headers).status_code==200
    with client.db_factory() as db:
        cipher = db.get(AppConnection,connection).access_cipher
        assert Fernet(new).decrypt(cipher.encode()).decode()=='local-access-2'


def test_repository_context_exact_limit_and_generation_binding(client,account,gitlab):
    headers = auth(account())
    connection = connect(client,headers)
    with client.db_factory() as db:
        row = db.get(AppConnection,connection)
        bind,user_id = db.bind,row.user_id
    text,sources = asyncio.run(read_context(bind,user_id,GitlabSelection.model_validate(selection(connection)),'login',2500))
    assert len(text.encode())<=2500 and len(sources)==1
    with pytest.raises(HTTPException):
        asyncio.run(read_context(bind,user_id,GitlabSelection.model_validate(selection(connection)),'login',500))


def test_transport_refuses_redirects_bounds_content_and_keeps_credentials_in_headers(monkeypatch):
    original = httpx.AsyncClient
    seen = []
    def handler(request):
        seen.append(request)
        if request.url.path=='/redirect':
            return httpx.Response(302,headers={'location':'https://attacker.example'})
        return httpx.Response(200,content=b'x'*100)
    def client(**kwargs):
        assert kwargs['follow_redirects'] is False and kwargs['trust_env'] is False
        return original(transport=httpx.MockTransport(handler),**kwargs)
    monkeypatch.setattr(httpx,'AsyncClient',client)
    provider = config.GitlabInstance.model_validate(INSTANCE)
    with pytest.raises(transport.GitlabError):
        asyncio.run(transport.request(provider,'GET','/redirect',token='local-only-token'))
    assert len(seen)==1 and seen[0].headers['authorization']=='Bearer local-only-token'
    assert 'token' not in str(seen[0].url)
    with pytest.raises(transport.GitlabError,match='크기'):
        asyncio.run(transport.request(provider,'GET','/file',raw=True,limit=50))


def test_transport_deadline_bounds_even_a_slow_drip_response(monkeypatch):
    original = httpx.AsyncClient
    settings = get_settings().model_copy(update={'gitlab_timeout_seconds':1})
    monkeypatch.setattr(transport,'get_settings',lambda:settings)
    class Drip(httpx.AsyncByteStream):
        async def __aiter__(self):
            for _ in range(10):
                await asyncio.sleep(.3)
                yield b'x'
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:original(
        transport=httpx.MockTransport(lambda _:httpx.Response(200,stream=Drip())),**kwargs))
    started = time.monotonic()
    with pytest.raises(transport.GitlabError):
        asyncio.run(transport.request(config.GitlabInstance.model_validate(INSTANCE),'GET','/slow',raw=True))
    assert time.monotonic()-started<2


def test_invalid_config_and_missing_keys_are_publicly_redacted(client,account,monkeypatch):
    headers = auth(account())
    monkeypatch.setenv('GITLAB_INSTANCES_JSON',json.dumps([INSTANCE|{'base_url':'https://secret:credential@gitlab.internal'}]))
    get_settings.cache_clear()
    response = client.get('/apps/catalog',headers=headers)
    assert response.status_code==503 and 'credential' not in response.text
    monkeypatch.setenv('GITLAB_INSTANCES_JSON',json.dumps([INSTANCE]))
    monkeypatch.setenv('INTEGRATIONS_ENCRYPTION_KEYS','[]')
    get_settings.cache_clear()
    assert client.get('/apps/catalog',headers=headers).json()['apps'][0]['providers'][0]['ready'] is False
