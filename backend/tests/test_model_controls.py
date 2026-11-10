import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import subprocess
import sys
import threading
import time
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.core.config import Settings, get_settings
from app.modules.auth.models import User
from app.modules.chat import context as budget, providers, scheduler
from app.modules.chat.models import Admission, ContextNote, Conversation, GenerationRun, Message
from app.modules.chat.router import TurnInput, prepare
from test_admin import headers, operator
from test_chat import create, events, send


def payload(**extra):
    return {'id': 'office', 'name': '내부 서버', 'provider': 'vllm', 'base_url': 'http://model.internal:8001/v1',
        'model': 'model-a', 'context_window': 4096, 'max_output_tokens': 256, 'max_concurrent': 1, **extra}


def configure(monkeypatch, **extra):
    monkeypatch.setenv('LLM_CONNECTIONS_JSON', json.dumps([payload(**extra)], ensure_ascii=False))
    get_settings.cache_clear()


def pending(client, identity, text='대기 질문', *, connection_id='office'):
    response = client.post('/chat/conversations', headers=headers(identity), json={'connection_id': connection_id, 'model': 'model-a'})
    assert response.status_code == 201, response.text
    with client.db_factory() as db:
        connection = providers.resolve(get_settings(), connection_id, db, db.get(User, identity['user']['subject']))
        turn = prepare(db, UUID(response.json()['id']), identity['user']['subject'],
            TurnInput(connection_id=connection_id, model='model-a', content=text, request_id=uuid4()), connection)
    return turn


@pytest.mark.parametrize('method,path', [('GET','connections'),('POST','connections'),('PATCH','connections/office')])
def test_registry_requires_operator(client, account, method, path):
    identity = account()
    assert client.request(method, '/admin/'+path, json=payload()).status_code == 401
    assert client.request(method, '/admin/'+path, headers=headers(identity), json=payload()).status_code == 403


def test_database_registry_live_updates_permissions_and_no_credentials(client, account, monkeypatch):
    admin = operator(client, account)
    alice, bob = account('alice'), account('bob')
    monkeypatch.setenv('LLM_PROVIDER_KEYS_JSON', '{"office-key":"never-send-this-key"}')
    get_settings.cache_clear()
    result = client.post('/admin/connections', headers=headers(admin), json=payload(
        allowed_users=['ALICE'], auth_mode='reference', key_ref='office-key',
        models=[{'name':'model-a','context_window':4096,'max_output_tokens':256,'max_concurrent':1}]))
    assert result.status_code == 201, result.text
    row = result.json()
    assert row['revision'] == 1 and row['has_key']
    assert 'never-send-this-key' not in result.text
    assert client.get('/admin/connections', headers=headers(admin)).json()['key_refs'] == ['office-key']
    for identity, allowed in [(alice, True), (bob, False)]:
        config = client.get('/chat/config', headers=headers(identity))
        assert any(item['id']=='office' for item in config.json()['connections']) == allowed
        assert 'model.internal' not in config.text and 'office-key' not in config.text
        created = client.post('/chat/conversations', headers=headers(identity), json={'connection_id':'office','model':'model-a'})
        assert created.status_code == (201 if allowed else 503)
    assert client.post('/chat/conversations', headers=headers(alice), json={'connection_id':'office','model':'other'}).status_code == 422
    with client.db_factory() as db:
        connection = providers.resolve(get_settings(), 'office', db, db.get(User, alice['user']['subject']))
        assert connection.api_key.get_secret_value() == 'never-send-this-key'
    draft = payload(allowed_users=['alice'], auth_mode='reference', key_ref='office-key', revision=1, enabled=False)
    assert client.patch('/admin/connections/office', headers=headers(admin), json=draft).status_code == 200
    assert client.patch('/admin/connections/office', headers=headers(admin), json=draft).status_code == 409
    assert client.post('/chat/conversations', headers=headers(alice), json={'connection_id':'office'}).status_code == 503
    audit = client.get('/admin/audit', headers=headers(admin)).text
    assert 'admin.connection.update' in audit and 'never-send-this-key' not in audit


def test_env_connection_override_preserves_key_and_checks_validation(client, account, monkeypatch):
    configure(monkeypatch, api_key='environment-secret')
    admin = operator(client, account)
    row = next(item for item in client.get('/admin/connections', headers=headers(admin)).json()['items'] if item['id']=='office')
    assert row['source'] == 'environment' and row['revision'] == 0 and row['has_key']
    assert 'environment-secret' not in json.dumps(row)
    assert client.post('/admin/connections', headers=headers(admin), json=payload()).status_code == 409
    assert client.patch('/admin/connections/office', headers=headers(admin), json=payload(auth_mode='inherit')).status_code == 200
    with client.db_factory() as db:
        assert providers.resolve(get_settings(), 'office', db).api_key.get_secret_value() == 'environment-secret'
    bad = [payload(id='demo'), payload(base_url='http://name:secret@model/v1'), payload(context_window=1024, max_output_tokens=1024),
        payload(allowed_users=['nobody']), payload(auth_mode='reference',key_ref='missing'),
        payload(models=[{'name':'x'},{'name':'x'}])]
    for value in bad:
        assert client.patch('/admin/connections/office', headers=headers(admin), json=value).status_code in {409,422}
    malformed = client.post('/admin/connections',headers=headers(admin),json=payload(api_key='accidentally-submitted-secret'))
    assert malformed.status_code == 422 and 'accidentally-submitted-secret' not in malformed.text
    monkeypatch.setenv('LLM_CONNECTIONS_JSON','[]'); get_settings.cache_clear()
    assert not any(item['id']=='office' for item in client.get('/chat/config',headers=headers(admin)).json()['connections'])
    managed = client.get('/admin/connections',headers=headers(admin)).json()['items']
    assert next(item for item in managed if item['id']=='office')['has_environment'] is False


def test_context_budget_keeps_whole_recent_turns_and_reports_omissions(client, account):
    identity = account()
    conversation = create(client, headers(identity))
    connection = providers.Connection(id='local',name='local',provider='test',context_window=4096,max_output_tokens=256)
    with client.db_factory() as db:
        for position in range(8):
            db.add(Message(conversation_id=conversation,position=position,role='user' if position%2==0 else 'assistant',
                content=('가' if position%2==0 else '나')*200,status='complete'))
        db.commit()
        history, report = budget.plan(db,conversation,connection,'demo','현재 질문')
        assert report['omitted_turns'] > 0 and report['included_turns'] > 0
        assert history[0]['role']=='user' and history[-1]['content']=='현재 질문'
        assert all(history[i]['role'] != history[i+1]['role'] for i in range(len(history)-1))
        assert report['estimated_input_tokens'] <= report['input_budget']
        assert report['is_estimate'] and report['estimate_method'].startswith('utf8')
        _, large = budget.plan(db,conversation,connection,'demo','가'*2000,strict=False)
        assert large['too_large']
    preview = client.post(f'/chat/conversations/{conversation}/context/preview', headers=headers(identity), json={'content':'현재 질문'})
    assert preview.status_code == 200 and preview.json()['last_complete_position']==7
    data = {'summary':'중요한 이전 결정 사항','through_position':7,'revision':0}
    result = client.patch(f'/chat/conversations/{conversation}/context',headers=headers(identity),json=data)
    assert result.status_code == 200 and result.json()['revision']==1
    assert client.patch(f'/chat/conversations/{conversation}/context',headers=headers(identity),json=data).status_code == 409
    with client.db_factory() as db:
        history, report = budget.plan(db,conversation,connection,'demo','새 질문')
        assert report['summarized_turns']==4 and report['omitted_turns']==0
        assert len(history)==2 and history[0]['role']=='system' and '중요한 이전 결정 사항' in history[0]['content']
        assert db.scalar(select(func.count(Message.id)).where(Message.conversation_id==conversation))==8


def test_context_ownership_range_and_rejected_input_roll_back(client, account, monkeypatch):
    identity, stranger = account(), account('bob')
    conversation = create(client, headers(identity))
    path = f'/chat/conversations/{conversation}/context'
    assert client.post(path+'/preview',headers=headers(stranger),json={}).status_code==404
    assert client.patch(path,headers=headers(stranger),json={}).status_code==404
    assert client.patch(path,headers=headers(identity),json={'summary':'내용','through_position':77}).status_code==422
    response = send(client,headers(identity),conversation,'가'*16000)
    assert response.status_code==422
    detail=client.get(f'/chat/conversations/{conversation}',headers=headers(identity)).json()
    assert not detail['conversation']['busy'] and detail['messages']==[]
    assert client.patch(path,headers=headers(identity),json={'summary':'메모만','through_position':-1}).status_code==200
    assert events(send(client,headers(identity),conversation))[-1][0]=='done'


def test_fifo_admission_and_user_limits(client, account, monkeypatch):
    configure(monkeypatch)
    alice,bob = account(),account('bob')
    first,second = pending(client,alice),pending(client,bob)
    settings=get_settings()
    with client.db_factory() as db:
        assert scheduler.poll(db,second.request_id,settings,providers.resolve)[0:2]==('queued',2)
        db.commit()
    with client.db_factory() as db:
        assert scheduler.poll(db,first.request_id,settings,providers.resolve)[0]=='running'
        db.commit()
    with client.db_factory() as db:
        assert scheduler.poll(db,second.request_id,settings,providers.resolve)[0]=='queued'
        scheduler.finish(db,first.request_id,'complete'); db.commit()
    with client.db_factory() as db:
        assert scheduler.poll(db,second.request_id,settings,providers.resolve)[0]=='running'
        db.commit()
    # Different connections still share the account's generation limit.
    other = pending(client,bob,connection_id='demo')
    with client.db_factory() as db:
        assert scheduler.poll(db,other.request_id,settings,providers.resolve)[0]=='queued'
        db.commit()


def test_atomic_admission_across_processes(client, account, monkeypatch):
    configure(monkeypatch)
    alice,bob = account(),account('bob')
    first,second=pending(client,alice),pending(client,bob)
    with client.db_factory() as db:
        url=str(db.bind.url)
    code = '''
import sys
from sqlalchemy.orm import Session
from app.db.session import create_db_engine
from app.core.config import get_settings
from app.modules.chat import providers, scheduler
with Session(create_db_engine(sys.argv[1])) as db:
 state, _, _ = scheduler.poll(db,sys.argv[2],get_settings(),providers.resolve)
 db.commit()
 print(state)
'''
    processes=[subprocess.Popen([sys.executable,'-c',code,url,turn.request_id],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True) for turn in [first,second]]
    results=[]
    for process in processes:
        out,err=process.communicate(timeout=15)
        assert process.returncode==0,err
        results.append(out.strip())
    assert sorted(results)==['queued','running']
    with client.db_factory() as db:
        assert db.scalar(select(func.count(Admission.id)).where(Admission.state=='running'))==1


@pytest.mark.parametrize('action', ['cancel','disable','timeout'])
def test_waiting_stream_cancel_revocation_timeout_release(client,account,monkeypatch,action):
    configure(monkeypatch)
    if action=='timeout':
        monkeypatch.setenv('LLM_QUEUE_WAIT_SECONDS','2'); get_settings.cache_clear()
    admin=operator(client,account)
    alice,bob=account(),account('bob')
    first=pending(client,alice)
    with client.db_factory() as db:
        assert scheduler.poll(db,first.request_id,get_settings(),providers.resolve)[0]=='running'; db.commit()
    response=client.post('/chat/conversations',headers=headers(bob),json={'connection_id':'office','model':'model-a'})
    conversation=response.json()['id']; request_id=str(uuid4())
    async def should_not_run(*args):
        pytest.fail('A queued/cancelled/disabled request must not reach inference')
        yield ''
    monkeypatch.setattr(providers,'stream_reply',should_not_run)
    with ThreadPoolExecutor() as executor:
        future=executor.submit(client.post,f'/chat/conversations/{conversation}/messages',headers=headers(bob),json={
            'connection_id':'office','model':'model-a','content':'대기 요청','request_id':request_id})
        deadline=time.monotonic()+5
        while time.monotonic()<deadline:
            with client.db_factory() as db:
                run=db.get(GenerationRun,request_id)
                if run and run.status=='queued': break
            time.sleep(.02)
        else: pytest.fail('Request did not enter queue')
        if action=='cancel':
            assert client.post(f'/chat/conversations/{conversation}/cancel',headers=headers(bob),json={'request_id':request_id}).status_code==204
        elif action=='disable':
            assert client.patch('/admin/connections/office',headers=headers(admin),json=payload(enabled=False,auth_mode='inherit')).status_code==200
        result=events(future.result(timeout=6))
    assert any(name=='queue' and data['status']=='queued' for name,data in result)
    assert result[-1][0]==('cancelled' if action=='cancel' else 'error')
    with client.db_factory() as db:
        admission=db.scalar(select(Admission).where(Admission.request_id==request_id))
        assert admission.state not in {'queued','running'}
        assert not db.get(Conversation,conversation).generation_id
        if action=='timeout': assert db.get(GenerationRun,request_id).error_code=='queue_timeout'


def test_expired_worker_leases_and_bounded_queue(client,account,monkeypatch):
    configure(monkeypatch)
    identity=account()
    first,second=pending(client,identity),pending(client,identity)
    response=client.post('/chat/conversations',headers=headers(identity),json={'connection_id':'office','model':'model-a'})
    conversation=response.json()['id']
    result=send(client,headers(identity),conversation,connection_id='office',model='model-a')
    assert result.status_code==429 and result.headers['retry-after']=='5'
    assert client.get(f'/chat/conversations/{conversation}',headers=headers(identity)).json()['messages']==[]
    with client.db_factory() as db:
        row=db.scalar(select(Admission).where(Admission.request_id==first.request_id))
        row.expires_at=int(time.time())-1; db.commit()
    with client.db_factory() as db:
        assert scheduler.poll(db,second.request_id,get_settings(),providers.resolve)[0]=='running'
        db.commit()


def test_global_queue_bound_is_shared_by_users(client,account,monkeypatch):
    configure(monkeypatch)
    monkeypatch.setenv('LLM_QUEUE_CAPACITY','1'); get_settings.cache_clear()
    alice,bob=account(),account('bob')
    pending(client,alice)
    conversation=client.post('/chat/conversations',headers=headers(bob),json={'connection_id':'office'}).json()['id']
    assert send(client,headers(bob),conversation,connection_id='office',model='model-a').status_code==429
    with client.db_factory() as db:
        assert db.scalar(select(func.count(Admission.id)))==1
        assert not db.get(Conversation,conversation).generation_id


def test_per_model_limit_and_missing_credential_fail_closed(client,account,monkeypatch):
    profiles=[{'name':name,'context_window':4096,'max_output_tokens':256,'max_concurrent':1} for name in ['model-a','model-b']]
    configure(monkeypatch,max_concurrent=3,models=profiles)
    alice,bob,charlie=account(),account('bob'),account('charlie')
    first,second=pending(client,alice),pending(client,bob)
    with client.db_factory() as db:
        assert scheduler.poll(db,first.request_id,get_settings(),providers.resolve)[0]=='running'; db.commit()
    with client.db_factory() as db:
        assert scheduler.poll(db,second.request_id,get_settings(),providers.resolve)[0]=='queued'
        scheduler.finish(db,second.request_id,'cancelled'); db.commit()
    third=pending(client,charlie)
    with client.db_factory() as db:
        db.get(GenerationRun,third.request_id).model='model-b'; db.commit()
    with client.db_factory() as db:
        assert scheduler.poll(db,third.request_id,get_settings(),providers.resolve)[0]=='running'; db.commit()
    admin=operator(client,account)
    monkeypatch.setenv('LLM_PROVIDER_KEYS_JSON','{"named":"private-secret"}'); get_settings.cache_clear()
    value=payload(id='keyed',auth_mode='reference',key_ref='named')
    assert client.post('/admin/connections',headers=headers(admin),json=value).status_code==201
    monkeypatch.setenv('LLM_PROVIDER_KEYS_JSON','{}'); get_settings.cache_clear()
    assert not any(item['id']=='keyed' for item in client.get('/chat/config',headers=headers(alice)).json()['connections'])
    managed=client.get('/admin/connections',headers=headers(admin)).json()['items']
    assert next(item for item in managed if item['id']=='keyed')['has_key'] is False


def test_stop_before_first_upstream_byte_closes_provider_and_releases_slot(client,account,monkeypatch):
    configure(monkeypatch)
    identity=account()
    conversation=client.post('/chat/conversations',headers=headers(identity),json={'connection_id':'office'}).json()['id']
    started,closed=threading.Event(),threading.Event()
    async def slow(*args):
        started.set()
        try:
            await asyncio.sleep(60)
            yield '늦은 답변'
        finally:
            closed.set()
    monkeypatch.setattr(providers,'stream_reply',slow)
    request_id=str(uuid4())
    with ThreadPoolExecutor() as executor:
        future=executor.submit(client.post,f'/chat/conversations/{conversation}/messages',headers=headers(identity),json={
            'connection_id':'office','model':'model-a','content':'느린 응답','request_id':request_id})
        assert started.wait(3)
        assert client.post(f'/chat/conversations/{conversation}/cancel',headers=headers(identity),json={'request_id':request_id}).status_code==204
        assert events(future.result(timeout=3))[-1][0]=='cancelled'
    assert closed.is_set()
    with client.db_factory() as db:
        run=db.get(GenerationRun,request_id)
        assert run.status=='cancelled' and run.first_token_ms is None
        assert db.scalar(select(Admission).where(Admission.request_id==request_id)).state=='cancelled'
