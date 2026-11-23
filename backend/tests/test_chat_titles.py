"""자동 제목의 실제 모델 계약·SSE 분리·경합·권한·실패 복구."""
import asyncio
import json
import threading
import time
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

import app.main as main_module
from app.core.config import get_settings
from app.modules.auth.models import User
from app.modules.chat import providers,titles
from app.modules.chat.jobs import GenerationJobs
from app.modules.chat.models import Admission,Conversation,ConversationTitle,GenerationRun
from app.modules.chat.usage import TokenCall
from test_chat import auth,events,send
from test_model_controls import configure

QUESTION='FastAPI 로그인 토큰 오류 원인을 분석해 줘.'
GENERATED='FastAPI 로그인 토큰 오류 분석'


def eventually(check):
    end=time.monotonic()+4
    while time.monotonic()<end:
        if check():return
        time.sleep(.02)
    pytest.fail('제목 작업이 제한 시간 안에 끝나지 않았습니다.')


@pytest.fixture
def model(client,account,monkeypatch):
    configure(monkeypatch,supports_reasoning=True,reasoning_visibility='public')
    monkeypatch.setenv('ASSISTANT_AUTOMATIC_ENABLED','false')
    monkeypatch.setenv('LLM_TITLE_ENABLED','true')
    monkeypatch.setenv('LLM_USER_CONCURRENCY','1')
    get_settings.cache_clear()
    identity=account();headers=auth(identity)
    response=client.post('/chat/conversations',headers=headers,json={'connection_id':'office','model':'model-a'})
    assert response.status_code==201,response.text
    control={'calls':[],'title':GENERATED,'release':threading.Event(),'started':threading.Event(),'status':200}
    control['release'].set()

    async def upstream(request):
        payload=json.loads(request.content)
        control['calls'].append(payload)
        if 'response_format' not in payload:
            body='data: '+json.dumps({'choices':[{'delta':{'content':'토큰 만료 조건을 확인해 보세요.'},'finish_reason':'stop'}]},ensure_ascii=False)+'\n\n'
            body+='data: '+json.dumps({'choices':[],'usage':{'prompt_tokens':12,'completion_tokens':9}})+'\n\ndata: [DONE]\n\n'
            return httpx.Response(200,text=body,headers={'content-type':'text/event-stream'})
        control['started'].set()
        deadline=time.monotonic()+5
        while not control['release'].is_set() and time.monotonic()<deadline:await asyncio.sleep(.01)
        assert control['release'].is_set(),'테스트 제목 응답을 해제해야 합니다.'
        if control['status']!=200:return httpx.Response(control['status'],json={'error':'private server error'})
        return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps({'title':control['title']},ensure_ascii=False),
            'reasoning':'제목 작업의 추론은 원래 대화에 섞이면 안 됩니다.'}}],
            'usage':{'prompt_tokens':16,'completion_tokens':11}})

    monkeypatch.setattr(providers,'client',lambda *_args:httpx.AsyncClient(transport=httpx.MockTransport(upstream)))
    try:yield identity,headers,response.json()['id'],control
    finally:control['release'].set()


def state(client,conversation):
    with client.db_factory() as db:
        row=db.get(Conversation,conversation)
        return row.title,titles.public(row.title_state)


def finish(client,conversation):
    eventually(lambda:state(client,conversation)[1] and state(client,conversation)[1]['status'] not in titles.PENDING)
    return state(client,conversation)


def test_first_reply_finishes_while_title_model_is_still_waiting(client,model):
    identity,headers,conversation,control=model
    control['release'].clear()
    response=send(client,headers,conversation,QUESTION,connection_id='office',model='model-a')
    stream=events(response)
    assert stream[-1][0]=='done',response.text[-1500:]
    assert not control['release'].is_set()
    assert stream[-1][1]['conversation']['title_generation']['status']=='queued'
    assert control['started'].wait(2)
    assert stream[-1][1]['conversation']['title']==QUESTION
    with client.db_factory() as db:
        runs=list(db.scalars(select(GenerationRun)))
        assert sum(run.status=='running' for run in runs)==1
        assert next(run for run in runs if run.conversation_id==conversation).status=='complete'
    control['release'].set()
    title,metadata=finish(client,conversation)
    assert title==GENERATED and metadata['status']=='complete'
    assert metadata['checks']['semantic_verified'] is False
    assert len(control['calls'])==2
    assert control['calls'][1]['max_tokens']==256
    assert control['calls'][1]['stream'] is False
    assert '제목 작업의 추론' not in response.text
    with client.db_factory() as db:
        calls=list(db.scalars(select(TokenCall)))
        assert len(calls)==2 and len({call.request_id for call in calls})==2
        assert all(call.reservation==0 for call in calls)
    assert stream[-1][1]['message']['generation_trace']['metrics']['model_calls']==1


@pytest.mark.parametrize('aba',[False,True])
def test_manual_title_wins_even_if_renamed_back_to_fallback(client,model,aba):
    _,headers,conversation,control=model
    control['release'].clear()
    assert events(send(client,headers,conversation,QUESTION,connection_id='office',model='model-a'))[-1][0]=='done'
    assert control['started'].wait(2)
    custom='직접 붙인 로그인 작업'
    assert client.patch('/chat/conversations/'+conversation,headers=headers,json={'title':custom}).status_code==200
    if aba:
        custom=QUESTION
        assert client.patch('/chat/conversations/'+conversation,headers=headers,json={'title':custom}).status_code==200
    control['release'].set()
    eventually(lambda:not main_module.app.state.generation_jobs.tasks)
    title,metadata=state(client,conversation)
    assert title==custom and metadata['status']=='manual'


@pytest.mark.parametrize(('candidate','code'),[
    ('비트코인 투자 수익 전망','no_source_anchor'),
    ('FastAPI 로그인 오류 100% 해결','unanchored_numbers'),
    ('FastAPI 로그인 수정 완료','unsupported_outcome'),
    ('FastAPI\n로그인 분석','invalid_format'),
    ('<script>FastAPI</script>','invalid_format'),
    ('FastAPI '+('긴'*65),'invalid_format'),
    ('FastAPI access_token=glpat-veryprivatesecret','secret_output'),
    ('FastAPI\u202e로그인','invalid_format'),
    ('FastAPI login token errors','language_mismatch'),
])
def test_invalid_or_unrelated_title_does_not_destroy_answer_or_fallback(client,model,candidate,code):
    _,headers,conversation,control=model
    control['title']=candidate
    response=send(client,headers,conversation,QUESTION,connection_id='office',model='model-a')
    assert events(response)[-1][0]=='done'
    title,metadata=finish(client,conversation)
    assert title==QUESTION and metadata['status']=='error' and metadata['error_code']==code
    assert candidate not in json.dumps(metadata,ensure_ascii=False)
    saved=client.get('/chat/conversations/'+conversation,headers=headers).json()
    assert saved['messages'][-1]['status']=='complete' and saved['messages'][-1]['content']


def test_title_provider_failure_keeps_successful_chat_and_never_uses_demo(client,model):
    _,headers,conversation,control=model
    control['status']=503
    assert events(send(client,headers,conversation,QUESTION,connection_id='office',model='model-a'))[-1][0]=='done'
    title,metadata=finish(client,conversation)
    assert title==QUESTION and metadata['error_code']=='provider_error'
    assert len(control['calls'])==2
    with client.db_factory() as db:
        assert not db.scalars(select(Admission).where(Admission.state.in_(['queued','running']))).all()


def test_title_timeout_releases_slot_without_affecting_answer(client,model,monkeypatch):
    _,headers,conversation,control=model
    monkeypatch.setenv('LLM_TITLE_TIMEOUT_SECONDS','2');get_settings.cache_clear()
    control['release'].clear()
    assert events(send(client,headers,conversation,QUESTION,connection_id='office',model='model-a'))[-1][0]=='done'
    title,metadata=finish(client,conversation)
    assert title==QUESTION and metadata['error_code']=='timeout'
    with client.db_factory() as db:
        assert not db.scalars(select(Admission).where(Admission.state.in_(['queued','running']))).all()


def test_active_metadata_updates_only_owned_titles_and_does_not_fetch_messages(client,account,model):
    _,headers,conversation,_=model
    send(client,headers,conversation,QUESTION,connection_id='office',model='model-a')
    finish(client,conversation)
    path=f'/chat/active?title_id={conversation}'
    data=client.get(path,headers=headers).json()
    assert data['titles'][0]['title']==GENERATED and 'messages' not in data
    assert client.get(path,headers=auth(account('bob'))).json()['titles']==[]
    assert client.get(path).status_code==401
    assert client.get('/chat/active?title_id=invalid',headers=headers).status_code==422
    assert client.get('/chat/active?'+('&'.join('title_id='+str(uuid4()) for _ in range(101))),headers=headers).status_code==422


def test_followup_and_regeneration_do_not_generate_a_second_title(client,model):
    _,headers,conversation,control=model
    send(client,headers,conversation,QUESTION,connection_id='office',model='model-a')
    finish(client,conversation)
    assert events(send(client,headers,conversation,QUESTION,retry=True,connection_id='office',model='model-a'))[-1][0]=='done'
    assert events(send(client,headers,conversation,'고마워',connection_id='office',model='model-a'))[-1][0]=='done'
    eventually(lambda:not main_module.app.state.generation_jobs.tasks)
    assert len([call for call in control['calls'] if 'response_format' in call])==1
    assert state(client,conversation)[0]==GENERATED


def test_disabled_feature_and_manual_new_chat_never_call_title_model(client,model,monkeypatch):
    _,headers,conversation,control=model
    monkeypatch.setenv('LLM_TITLE_ENABLED','false');get_settings.cache_clear()
    send(client,headers,conversation,QUESTION,connection_id='office',model='model-a')
    assert state(client,conversation)[1] is None and len(control['calls'])==1
    monkeypatch.setenv('LLM_TITLE_ENABLED','true');get_settings.cache_clear()
    new=client.post('/chat/conversations',headers=headers,json={'connection_id':'office','model':'model-a'}).json()['id']
    client.patch('/chat/conversations/'+new,headers=headers,json={'title':'새 대화'})
    send(client,headers,new,QUESTION,connection_id='office',model='model-a')
    assert state(client,new)[0]=='새 대화' and state(client,new)[1]['status']=='manual'
    assert len(control['calls'])==2


def test_lease_expiry_and_account_revocation_prevent_title_commit(client,model):
    identity,headers,conversation,control=model
    control['release'].clear()
    send(client,headers,conversation,QUESTION,connection_id='office',model='model-a')
    assert control['started'].wait(2)
    with client.db_factory() as db:
        db.get(ConversationTitle,conversation).expires_at=int(time.time())-1;db.commit()
    expired=client.get('/chat/active?title_id='+conversation,headers=headers).json()['titles'][0]
    assert expired['title_generation']['error_code']=='interrupted'
    with client.db_factory() as db:
        db.get(User,identity['user']['subject']).is_active=False;db.commit()
    control['release'].set()
    eventually(lambda:not main_module.app.state.generation_jobs.tasks)
    assert state(client,conversation)[0]==QUESTION


def test_shutdown_does_not_accept_new_background_work():
    async def scenario():
        jobs=GenerationJobs();await jobs.close()
        async def forbidden():raise AssertionError('종료한 작업 관리자는 모델을 호출하면 안 됩니다.')
        assert jobs.start_background(forbidden()) is None
        assert not jobs.tasks
    asyncio.run(scenario())


def test_duplicate_title_worker_cannot_repeat_the_model_call(client,model):
    identity,headers,conversation,control=model
    control['release'].clear()
    send(client,headers,conversation,QUESTION,connection_id='office',model='model-a')
    assert control['started'].wait(2)
    with client.db_factory() as db:
        work=db.get(ConversationTitle,conversation).work_id
        connection=providers.resolve(get_settings(),'office',db,db.get(User,identity['user']['subject']))
        bind=db.get_bind()
    asyncio.run(titles.generate(bind,identity['user']['subject'],conversation,work,connection,'model-a',get_settings()))
    assert len(control['calls'])==2
    control['release'].set()
    assert finish(client,conversation)[0]==GENERATED


def test_deleted_chat_is_not_recreated_by_a_late_title(client,model):
    _,headers,conversation,control=model
    control['release'].clear()
    send(client,headers,conversation,QUESTION,connection_id='office',model='model-a')
    assert control['started'].wait(2)
    assert client.delete('/chat/conversations/'+conversation,headers=headers).status_code==204
    control['release'].set()
    eventually(lambda:not main_module.app.state.generation_jobs.tasks)
    with client.db_factory() as db:
        assert db.get(Conversation,conversation) is None
        assert db.get(ConversationTitle,conversation) is None


def test_account_revoked_before_commit_preserves_fallback(client,model):
    identity,headers,conversation,control=model
    control['release'].clear()
    send(client,headers,conversation,QUESTION,connection_id='office',model='model-a')
    assert control['started'].wait(2)
    with client.db_factory() as db:
        db.get(User,identity['user']['subject']).is_active=False;db.commit()
    control['release'].set()
    title,metadata=finish(client,conversation)
    assert title==QUESTION and metadata['error_code']=='access_changed'


def test_model_permission_revoked_before_commit_preserves_fallback(client,model,monkeypatch):
    _,headers,conversation,control=model
    control['release'].clear()
    send(client,headers,conversation,QUESTION,connection_id='office',model='model-a')
    assert control['started'].wait(2)
    def denied(*_args):raise providers.ProviderError('사용할 수 없는 연결입니다.')
    monkeypatch.setattr(providers,'resolve',denied)
    control['release'].set()
    assert finish(client,conversation)[1]['error_code']=='access_changed'


def test_ollama_title_uses_bounded_structured_output_without_exposing_thinking(monkeypatch):
    settings=get_settings()
    connection=providers.Connection(id='ollama',name='사내 Ollama',provider='ollama',base_url='http://ollama.invalid',model='gemma',
        supports_reasoning=True,reasoning_visibility='public')
    captured=[]
    def response(request):
        payload=json.loads(request.content);captured.append(payload)
        return httpx.Response(200,json={'message':{'content':json.dumps({'title':GENERATED},ensure_ascii=False),
            'thinking':'표시하거나 저장할 필요 없는 별도 채널'},'done':True})
    monkeypatch.setattr(providers,'client',lambda *_args:httpx.AsyncClient(transport=httpx.MockTransport(response)))
    result,checks=asyncio.run(titles.propose(connection,'gemma',QUESTION,settings))
    assert result==GENERATED and not checks['semantic_verified']
    assert captured[0]['options']['num_predict']==256 and captured[0]['format']['properties']['title']['maxLength']==60
    assert captured[0]['stream'] is False


def test_evaluation_reference_rules_are_not_reported_as_model_accuracy():
    from evaluate_titles import evaluate
    cases=json.loads((Path(__file__).parents[1]/'evaluations/chat_titles.ko.json').read_text())
    report=evaluate(cases,{item['id']:item['reference_title'] for item in cases},mode='reference_rules_only_no_model')
    assert report['cases']==16 and report['format_and_safety_pass']==16 and report['topic_proxy_pass']==16
    assert report['semantic_accuracy'] is None and all(row['human_review_required'] for row in report['rows'])
    bad=evaluate(cases,{'auth':'FastAPI 비트코인 투자'},mode='provided_responses')
    assert bad['rows'][0]['format_and_safety_pass'] is True
    assert bad['rows'][0]['topic_proxy_pass'] is False and bad['semantic_accuracy'] is None


def test_fallback_and_known_model_secret_do_not_leak_credentials():
    assert 'veryprivatesecret' not in titles.fallback('API_KEY=veryprivatesecret\n설정을 확인해줘')
    with pytest.raises(titles.InvalidTitle,match='secret_output'):
        titles.check({'title':'FastAPI bareprivatevalue 설정'},QUESTION,secrets=['bareprivatevalue'])
