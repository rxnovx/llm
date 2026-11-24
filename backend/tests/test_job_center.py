"""실제 DB 대기열·작업 기록을 읽고 기존 취소/재시작 흐름을 재사용한다."""
import time
from uuid import UUID,uuid4

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.auth.models import User
from app.modules.chat import providers,scheduler,trace
from app.modules.chat.models import Admission,Conversation,GenerationRun,Message
from app.modules.chat.router import TurnInput,prepare
from app.modules.workspaces.models import ToolTrace
from test_chat import auth,create,send
from test_model_controls import configure,pending


def test_jobs_are_owner_scoped_bounded_and_exclude_independent_model_calls(client,account,monkeypatch):
    configure(monkeypatch)
    alice,bob=account(),account('bob')
    first,second=pending(client,alice),pending(client,alice)
    stranger=pending(client,bob)
    with client.db_factory() as db:
        db.add(GenerationRun(id=str(uuid4()),user_id=alice['user']['subject'],conversation_id=None,
            connection_id='office',provider='vllm',model='title-worker',is_test=False,status='running',
            started_at=int(time.time()),deadline_at=int(time.time())+60))
        db.commit()
    data=client.get('/chat/jobs?limit=1',headers=auth(alice)).json()
    assert data['active_count']==2 and data['has_more'] and len(data['items'])==1
    next_page=client.get('/chat/jobs?limit=1&offset=1',headers=auth(alice)).json()
    assert {data['items'][0]['id'],next_page['items'][0]['id']}=={first.request_id,second.request_id}
    assert stranger.request_id not in str(data) and 'title-worker' not in str(data)
    assert client.get('/chat/jobs').status_code==401
    for params in ['limit=101','offset=-1','offset=9223372036854775808','view=invalid']:
        assert client.get('/chat/jobs?'+params,headers=auth(alice)).status_code==422
    assert client.get('/chat/conversations?offset=9223372036854775808',headers=auth(alice)).status_code==422


def test_current_stage_and_queue_position_are_actual_records_without_private_output(client,account,monkeypatch):
    configure(monkeypatch)
    identity=account();first,second=pending(client,identity),pending(client,identity)
    now=int(time.time()*1000)
    with client.db_factory() as db:
        assert scheduler.poll(db,first.request_id,get_settings(),providers.resolve)[0]=='running'
        db.add(trace.GenerationTrace(message_id=first.assistant_id,user_id=identity['user']['subject'],
            request_id=first.request_id,status='running',reasoning='do-not-expose-reasoning',started_at_ms=now,updated_at_ms=now))
        tool=ToolTrace(id=str(uuid4()),message_id=first.assistant_id,name='gitlab_checkout',step=1,
            status='running',detail='API_KEY=private-model-secret')
        db.add(tool);db.flush()
        db.add(trace.TraceActivity(trace_id=tool.id,request_id=first.request_id,sequence=1,started_at_ms=now))
        db.commit()
    response=client.get('/chat/jobs',headers=auth(identity))
    rows={row['id']:row for row in response.json()['items']}
    assert rows[first.request_id]['status']=='running' and rows[first.request_id]['can_cancel']
    assert rows[first.request_id]['activity']=={'name':'gitlab_checkout','status':'running','duration_ms':None}
    assert rows[second.request_id]['position']==1 and rows[second.request_id]['activity'] is None
    assert 'private-model-secret' not in response.text and 'do-not-expose-reasoning' not in response.text
    assert 'content' not in rows[first.request_id] and 'detail' not in str(rows[first.request_id])


def test_cancel_uses_existing_request_id_and_does_not_affect_a_new_attempt(client,account,monkeypatch):
    configure(monkeypatch)
    identity=account();headers=auth(identity);first=pending(client,identity)
    endpoint=f'/chat/conversations/{first.conversation_id}/cancel'
    assert client.post(endpoint,headers=headers,json={'request_id':first.request_id}).status_code==204
    row=client.get('/chat/jobs',headers=headers).json()['items'][0]
    assert row['cancel_requested'] and not row['can_cancel']
    with client.db_factory() as db:
        scheduler.finish(db,first.request_id,'cancelled')
        db.get(GenerationRun,first.request_id).status='cancelled'
        conversation=db.get(Conversation,first.conversation_id)
        conversation.generation_id=None;conversation.generation_until=0
        db.get(Message,first.assistant_id).status='cancelled';db.commit()
        connection=providers.resolve(get_settings(),'office',db,db.get(User,identity['user']['subject']))
        following=prepare(db,UUID(first.conversation_id),identity['user']['subject'],
            TurnInput(connection_id='office',model='model-a',content='새 작업',request_id=uuid4()),connection)
    assert client.post(endpoint,headers=headers,json={'request_id':first.request_id}).status_code==204
    rows=client.get('/chat/jobs',headers=headers).json()['items']
    assert len(rows)==1 and rows[0]['id']==following.request_id and rows[0]['can_cancel'] and not rows[0]['cancel_requested']
    other=auth(account('bob'))
    assert client.post(endpoint,headers=other,json={'request_id':following.request_id}).status_code==404


@pytest.mark.parametrize('first_endpoint',['/chat/jobs','conversation'])
def test_restart_recovery_is_shared_and_preserves_partial_answer_without_replay(client,account,monkeypatch,first_endpoint):
    configure(monkeypatch)
    identity=account();headers=auth(identity);turn=pending(client,identity)
    with client.db_factory() as db:
        db.get(Conversation,turn.conversation_id).generation_until=1
        db.get(Message,turn.assistant_id).content='저장된 실제 부분 답변'
        db.commit()
    monkeypatch.setattr(providers,'client',lambda *_:pytest.fail('중단된 작업을 자동 재실행하면 안 됩니다.'))
    endpoint='/chat/conversations/'+turn.conversation_id if first_endpoint=='conversation' else first_endpoint
    assert client.get(endpoint,headers=headers).status_code==200
    data=client.get('/chat/jobs?view=finished',headers=headers).json()
    assert data['active_count']==0 and data['items'][0]['status']=='error'
    assert data['items'][0]['error_code']=='interrupted' and data['items'][0]['elapsed_ms'] is None
    assert data['items'][0]['finished_at'] is None and data['items'][0]['interrupted_detected_at'] is not None
    assert not data['items'][0]['can_cancel']
    saved=client.get('/chat/conversations/'+turn.conversation_id,headers=headers).json()
    assert saved['messages'][-1]['content']=='저장된 실제 부분 답변'
    assert saved['messages'][-1]['recovery']['code']=='interrupted'
    with client.db_factory() as db:
        assert db.scalar(select(Admission).where(Admission.request_id==turn.request_id)).state=='error'


def test_completed_history_survives_reload_and_deleted_chats_are_omitted(client,account):
    headers=auth(account());first,second=create(client,headers),create(client,headers)
    send(client,headers,first);send(client,headers,second)
    assert client.get('/chat/jobs',headers=headers).json()['active_count']==0
    history=client.get('/chat/jobs?view=finished&limit=1',headers=headers).json()
    assert history['has_more'] and history['items'][0]['status']=='complete' and not history['items'][0]['can_cancel']
    assert history['items'][0]['elapsed_ms']>=0
    assert client.delete('/chat/conversations/'+first,headers=headers).status_code==204
    rows=client.get('/chat/jobs?view=all',headers=headers).json()['items']
    assert len(rows)==1 and rows[0]['conversation_id']==second
