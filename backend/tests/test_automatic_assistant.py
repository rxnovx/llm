"""Behavior contracts for automatic routing, ACLs, buffered drafts and bounded retries."""
import asyncio
import json
import sys
import time

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import select,update

from app.core.config import get_settings
from app.modules.assistant import routing
from app.modules.auth.models import User
from app.modules.chat import providers
from app.modules.chat.models import Admission
from app.modules.knowledge import service as knowledge
from app.modules.knowledge.models import Document
from app.modules.workspaces.models import Workspace
from test_chat import auth,create,events,send
from test_gitlab_apps import gitlab,connect
from test_knowledge import base,upload,index
from test_workspace_flow import workspace_stack


def saved(client,headers,conversation):
    return client.get('/chat/conversations/'+conversation,headers=headers).json()['messages'][-1]


@pytest.mark.filterwarnings('error:Pydantic serializer warnings:UserWarning')
def test_plain_project_question_resolves_clones_indexes_and_persists_without_picker(client,account,workspace_stack,monkeypatch):
    _,commit=workspace_stack
    headers=auth(account());connection=connect(client,headers);conversation=create(client,headers)
    proposals=[]
    async def decide(connection,model,question,available,settings):
        proposals.append(available)
        return routing.Plan(route='gitlab',project='team/service',needs_review=True)
    monkeypatch.setattr(routing,'decide',decide)
    response=send(client,headers,conversation,content='team/service 소스코드를 분석해줘')
    assert events(response)[-1][0]=='done',response.text[-2000:]
    message=saved(client,headers,conversation)
    assert message['routing']['automatic_project'] and message['routing']['status']=='complete'
    assert message['apps'][0]['connection_id']==connection and message['apps'][0]['mode']=='workspace'
    assert message['apps'][0]['workspace_id']
    assert any(s['commit']==commit and s['content_hash'] for s in message['sources'])
    assert any(t['name']=='repository_index' for t in message['tools'])
    assert proposals[0]['gitlab_connections'][0]['connection_id']==connection
    assert 'local-access' not in json.dumps(proposals) and 'cipher' not in json.dumps(proposals)
    with client.db_factory() as db:
        assert db.scalar(select(Workspace)).allow_execution is False
        assert db.scalar(select(Admission)).state=='complete'


def test_auto_checkout_requires_oauth_even_when_terminal_is_available(client,account,workspace_stack):
    headers=auth(account());conversation=create(client,headers)
    response=send(client,headers,conversation,content='team/service를 분석해줘')
    assert events(response)[-1][0]=='error'
    assert '연결' in response.text
    with client.db_factory() as db:assert db.scalar(select(Workspace)) is None


def test_automatic_project_resolution_refuses_ambiguous_connected_servers(client,account,workspace_stack):
    headers=auth(account());connection=connect(client,headers)
    with client.db_factory() as db:
        uid=db.scalar(select(User.id).where(User.username=='alice'));bind=db.get_bind()
    candidates=[{'connection_id':connection,'provider_id':p,'server':p} for p in ['one','two']]
    with pytest.raises(HTTPException) as error:
        asyncio.run(routing.resolve_project(bind,uid,routing.Plan(route='gitlab',project='team/service'),candidates))
    assert error.value.status_code==422
    # A model may choose only a provider ID present in the host catalog.
    value=asyncio.run(routing.resolve_project(bind,uid,routing.Plan(route='gitlab',project='team/service',provider_id='one'),candidates))
    assert value.mode=='workspace' and value.project_id==7
    with pytest.raises(HTTPException) as error:
        asyncio.run(routing.resolve_project(bind,uid,routing.Plan(route='gitlab',project='team/service',provider_id='unknown'),candidates))
    assert error.value.status_code==409


def test_auto_knowledge_scope_includes_only_current_authorized_ready_documents(client,account):
    alice,bob=auth(account()),auth(account('bob'))
    ca,cb,other=create(client,alice),create(client,bob),create(client,alice)
    own=base(client,alice);shared=base(client,bob);private=base(client,bob,'비공개')
    docs=[]
    for headers,options in [(alice,{'base_id':own}),(bob,{'base_id':shared}),(bob,{'base_id':private}),
                            (alice,{'conversation':ca}),(alice,{'conversation':other})]:
        docs.append(upload(client,headers,**options));index(client)
    client.post(f'/knowledge/bases/{shared}/members',headers=bob,json={'username':'alice'})
    with client.db_factory() as db:
        uid=db.scalar(select(User.id).where(User.username=='alice'))
        ids={d.id for d in knowledge.automatic_documents(db,uid,ca)}
        assert ids=={docs[0]['id'],docs[1]['id'],docs[3]['id']}
        attachment=db.get(Document,docs[3]['id']);attachment.expires_at=int(time.time())-1;db.commit()
        assert {d.id for d in knowledge.automatic_documents(db,uid,ca)}=={docs[0]['id'],docs[1]['id']}
    member=client.get(f'/knowledge/bases/{shared}/members',headers=bob).json()['items'][0]['id']
    client.delete(f'/knowledge/bases/{shared}/members/{member}',headers=bob)
    with client.db_factory() as db:assert {d.id for d in knowledge.automatic_documents(db,uid,ca)}=={docs[0]['id']}
    assert client.get('/chat/conversations/'+cb,headers=alice).status_code==404


def test_unknown_or_private_question_automatically_retrieves_without_manual_scope(client,account):
    headers=auth(account());conversation=create(client,headers);kb=base(client,headers)
    upload(client,headers,base_id=kb);index(client)
    response=send(client,headers,conversation,content='우리 회사 연차 휴가 규정은?')
    assert events(response)[-1][0]=='done',response.text[-2000:]
    message=saved(client,headers,conversation)
    assert message['routing']['route']=='knowledge' and message['sources'][0]['app']=='knowledge'
    assert '[KS1]' in message['content']
    assert not message['assistant'].get('_auto_scope')


def test_uncertain_draft_is_not_sent_then_rag_regenerates_from_real_evidence(client,account,monkeypatch):
    headers=auth(account());conversation=create(client,headers);kb=base(client,headers)
    upload(client,headers,base_id=kb);index(client);reviews=[]
    async def decide(*_):return routing.Plan(needs_review=True)
    async def draft(*_):yield '연차는 99일입니다.'
    async def answer(connection,model,messages,tools,settings):
        assert '15일' in messages[-1]['content']
        return {'role':'assistant','content':'연차는 15일입니다. [KS1]','tool_calls':[]}
    async def assess(connection,model,question,text,evidence,settings):
        reviews.append((text,evidence))
        return routing.Review(verdict='needs_search',query='연차 휴가 규정') if len(reviews)==1 else routing.Review(verdict='supported')
    monkeypatch.setattr(routing,'decide',decide);monkeypatch.setattr(routing,'assess',assess)
    monkeypatch.setattr(providers,'stream_reply',draft);monkeypatch.setattr(providers,'tool_reply',answer)
    response=send(client,headers,conversation,content='연차에 대해 알려줘')
    assert events(response)[-1][0]=='done',response.text[-2000:]
    deltas=''.join(v['content'] for k,v in events(response) if k=='delta')
    assert '99일' not in deltas and '15일' in deltas
    assert len(reviews)==2 and not reviews[0][1] and reviews[1][1][0]['id']=='KS1'
    assert saved(client,headers,conversation)['routing']['rounds']==2
    assert saved(client,headers,conversation)['routing']['route']=='knowledge'


def test_invented_citations_are_rejected_even_if_model_review_approves(client,account,monkeypatch):
    headers=auth(account());conversation=create(client,headers);kb=base(client,headers)
    upload(client,headers,base_id=kb);index(client)
    async def answer(*_):return {'role':'assistant','content':'근거 없는 답변 [KS999]','tool_calls':[]}
    monkeypatch.setattr(providers,'tool_reply',answer)
    response=send(client,headers,conversation,content='우리 회사 휴가 규정은?')
    assert events(response)[-1][0]=='done',response.text[-2000:]
    message=saved(client,headers,conversation)
    assert message['routing']['status']=='unverified' and message['routing']['rounds']==2
    assert '[KS999]' not in message['content'] and '단정하지' in message['content']


@pytest.mark.parametrize('mode',['off','always'])
def test_auto_rag_modes_control_prefetch(client,account,monkeypatch,mode):
    headers=auth(account());conversation=create(client,headers);kb=base(client,headers)
    upload(client,headers,base_id=kb);index(client)
    async def plain(*_):yield '일반 답변'
    monkeypatch.setattr(providers,'stream_reply',plain)
    response=send(client,headers,conversation,content='15',assistant={'rag_mode':mode})
    assert events(response)[-1][0]=='done',response.text[-1500:]
    message=saved(client,headers,conversation)
    assert bool(message['sources'])==(mode=='always')


@pytest.mark.filterwarnings('error:Pydantic serializer warnings:UserWarning')
def test_execution_is_not_replayed_when_grounding_requests_more_evidence(client,account,workspace_stack,monkeypatch):
    terminal,_=workspace_stack
    headers=auth(account());connection=connect(client,headers);conversation=create(client,headers);kb=base(client,headers)
    upload(client,headers,base_id=kb);index(client)
    async def model(connection,model,messages,tools,settings):
        if not any(m['role']=='tool' for m in messages):
            return {'role':'assistant','content':'','tool_calls':[{'id':'once','type':'function','function':{'name':'terminal_exec',
                'arguments':{'argv':[sys.executable,'-c','print("one execution")'],'timeout_seconds':5}}}]}
        return {'role':'assistant','content':'I cannot verify the requested fact.','tool_calls':[]}
    async def review(*_):return routing.Review(verdict='needs_search',query='휴가 규정')
    monkeypatch.setattr(providers,'tool_reply',model);monkeypatch.setattr(routing,'assess',review)
    response=send(client,headers,conversation,apps=[{'app':'gitlab','connection_id':connection,'project_id':7,'ref':'main','mode':'execute'}])
    assert events(response)[-1][0]=='done',response.text[-2000:]
    with terminal.store.db() as db:assert db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0]==1
    assert saved(client,headers,conversation)['routing']['status']=='unverified'


def test_failed_regeneration_restores_good_answer_sources_and_routing(client,account,monkeypatch):
    headers=auth(account());conversation=create(client,headers);kb=base(client,headers)
    upload(client,headers,base_id=kb);index(client);question='우리 회사 연차 휴가 규정은?'
    assert events(send(client,headers,conversation,content=question))[-1][0]=='done'
    previous=saved(client,headers,conversation)
    async def broken(*_):raise providers.ProviderError('근거 점검 연결 실패')
    monkeypatch.setattr(routing,'assess',broken)
    response=send(client,headers,conversation,content=question,retry=True)
    assert events(response)[-1][0]=='error',response.text[-1500:]
    current=saved(client,headers,conversation)
    for key in ['content','sources','routing','assistant']:assert current[key]==previous[key]


def test_host_only_auto_scope_cannot_be_supplied_by_client(client,account):
    headers=auth(account());conversation=create(client,headers)
    response=send(client,headers,conversation,assistant={'_auto_scope':True})
    assert response.status_code==422


@pytest.mark.parametrize('provider',['vllm','ollama'])
def test_native_structured_decision_contract_uses_provider_schema_format(provider):
    captured=[]
    def remote(request):
        payload=json.loads(request.content);captured.append(payload)
        result=json.dumps({'route':'knowledge','query':'휴가 규정','needs_review':True})
        return httpx.Response(200,json={'choices':[{'message':{'content':result}}]} if provider=='vllm' else {'message':{'content':result}})
    connection=providers.Connection(id='local',name='Local',provider=provider,base_url='http://local/v1',model='local')
    original=providers.client
    providers.client=lambda *_:httpx.AsyncClient(transport=httpx.MockTransport(remote))
    try:
        result=asyncio.run(providers.structured_reply(connection,'local',[{'role':'user','content':'질문'}],routing.Plan.model_json_schema(),get_settings()))
    finally:providers.client=original
    assert routing.Plan.model_validate(result).route=='knowledge'
    if provider=='vllm':assert captured[0]['response_format']['json_schema']['schema']['properties']['route']
    else:assert captured[0]['format']['properties']['route']
    assert captured[0]['stream'] is False


@pytest.mark.parametrize('question,expected',[
    ('team/service를 분석해줘','team/service'),('src/app/main.py 코드를 검토해줘',''),('안녕',''),
    ('group/sub/project 구조 분석','group/sub/project'),
])
def test_explicit_project_hint_requires_analysis_intent_and_excludes_source_paths(question,expected):
    assert routing.project_hint(question)==expected

@pytest.mark.parametrize('verdict',['supported','needs_search'])
def test_revoked_share_during_review_prevents_final_draft_delivery(client,account,monkeypatch,verdict):
    from app.modules.knowledge.models import KnowledgeMember
    owner,reader=auth(account()),auth(account('bob'))
    kb=base(client,owner);upload(client,owner,base_id=kb);index(client)
    client.post(f'/knowledge/bases/{kb}/members',headers=owner,json={'username':'bob'})
    conversation=create(client,reader)
    async def revoke(*_):
        with client.db_factory() as db:
            uid=db.scalar(select(User.id).where(User.username=='bob'))
            db.delete(db.get(KnowledgeMember,(kb,uid)));db.commit()
        return routing.Review(verdict=verdict,query='연차 규정')
    monkeypatch.setattr(routing,'assess',revoke)
    response=send(client,reader,conversation,content='회사 연차 휴가 규정은?')
    assert events(response)[-1][0]=='error'
    assert not any(k=='delta' for k,_ in events(response))
    assert saved(client,reader,conversation)['content']==''

def test_model_can_treat_quoted_project_request_as_text_instead_of_cloning(client,account,workspace_stack,monkeypatch):
    headers=auth(account());connect(client,headers);conversation=create(client,headers)
    async def decide(*_):return routing.Plan(route='direct')
    async def answer(*_):yield 'Analyze team/service.'
    monkeypatch.setattr(routing,'decide',decide);monkeypatch.setattr(providers,'stream_reply',answer)
    response=send(client,headers,conversation,content='"team/service를 분석해줘"를 영어로 번역해줘')
    assert events(response)[-1][0]=='done',response.text[-2000:]
    message=saved(client,headers,conversation)
    assert not message['apps'] and message['content']=='Analyze team/service.'
    with client.db_factory() as db:assert db.scalar(select(Workspace)) is None


def test_grounding_review_checks_short_actual_excerpts_and_requires_all_cited_sources(monkeypatch):
    captured=[]
    async def structured(connection,model,messages,schema,settings):
        captured.append(json.loads(messages[-1]['content']))
        return {'verdict':'supported'}
    monkeypatch.setattr(providers,'structured_reply',structured)
    connection=providers.Connection(id='local',name='Local',provider='vllm',base_url='http://local/v1',model='local')
    review=asyncio.run(routing.assess(connection,'local','함수 설명','한 줄 함수입니다. [GL1]',
        [{'id':'GL1','content':'1: def f(): return 1'}],get_settings()))
    assert review.verdict=='supported' and captured[0]['evidence'][0]['id']=='GL1'
    review=asyncio.run(routing.assess(connection,'local','함수 설명','근거 [GL2]',
        [{'id':'GL1','content':'1: def f(): return 1'}],get_settings()))
    assert review.verdict=='insufficient' and len(captured)==1

def test_stop_during_planning_cancels_before_checkout_and_releases_permit(client,account,monkeypatch):
    from app.modules.chat.models import Conversation
    headers=auth(account());conversation=create(client,headers)
    async def planning(*_):
        with client.db_factory() as db:
            db.get(Conversation,conversation).cancel_requested=True;db.commit()
        await asyncio.sleep(2)
        return routing.Plan(route='gitlab',project='team/service')
    monkeypatch.setattr(routing,'decide',planning)
    response=send(client,headers,conversation,content='team/service를 분석해줘')
    assert events(response)[-1][0]=='cancelled',response.text[-1500:]
    assert not any(k=='delta' for k,_ in events(response))
    with client.db_factory() as db:
        assert db.scalar(select(Workspace)) is None
        assert db.scalar(select(Admission)).state=='cancelled'
