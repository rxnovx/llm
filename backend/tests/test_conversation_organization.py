"""대화 정리의 저장·검색·경합·공유 범위와 기존 메시지 호환성."""
import time

import pytest

from app.modules.chat.models import Conversation,ConversationOrganization
from test_chat import auth,create,events,send
from test_workflows import project


def organize(client,headers,conversation,revision=0,**values):
    return client.patch(f'/chat/conversations/{conversation}/organization',headers=headers,json={'revision':revision,**values})


def test_pin_order_is_stable_across_pagination_without_changing_chat_content(client,account):
    headers=auth(account())
    ids=[create(client,headers) for _ in range(3)]
    with client.db_factory() as db:
        for index,identifier in enumerate(ids):db.get(Conversation,identifier).updated_at=100+index
        db.commit()
    result=organize(client,headers,ids[0],pinned=True)
    assert result.status_code==200 and result.json()['pinned'] and result.json()['updated_at']==100
    first=client.get('/chat/conversations?limit=1',headers=headers).json()
    second=client.get('/chat/conversations?limit=1&offset=1',headers=headers).json()
    assert first['items'][0]['id']==ids[0] and first['has_more']
    assert second['items'][0]['id']==ids[2]
    detail=client.get('/chat/conversations/'+ids[0],headers=headers).json()
    assert detail['messages']==[] and detail['conversation']['title']=='새 대화'
    assert organize(client,headers,ids[0],revision=1,pinned=False).status_code==200
    assert client.get('/chat/conversations?limit=1',headers=headers).json()['items'][0]['id']==ids[2]


def test_archive_keeps_messages_search_and_direct_link_then_restores(client,account):
    headers=auth(account());conversation=create(client,headers)
    assert events(send(client,headers,conversation,'보관해도 찾을 수 있는 배포 지침'))[-1][0]=='done'
    before=client.get('/chat/conversations/'+conversation,headers=headers).json()
    assert organize(client,headers,conversation,pinned=True).status_code==200
    archived=organize(client,headers,conversation,revision=1,archived=True).json()
    assert archived['archived'] and not archived['pinned']
    assert client.get('/chat/conversations',headers=headers).json()['items']==[]
    stored=client.get('/chat/conversations?view=archived',headers=headers).json()['items']
    assert stored[0]['id']==conversation and stored[0]['organization_revision']==2
    search=client.get('/chat/conversations',headers=headers,params={'q':'배포 지침'}).json()['items']
    assert search[0]['archived'] and search[0]['match']['message_id']
    after=client.get('/chat/conversations/'+conversation,headers=headers).json()
    assert after['messages']==before['messages']
    assert after['conversation']['updated_at']==before['conversation']['updated_at']
    assert organize(client,headers,conversation,revision=2,archived=False).status_code==200
    assert client.get('/chat/conversations?view=archived',headers=headers).json()['items']==[]
    assert client.get('/chat/conversations',headers=headers).json()['items'][0]['id']==conversation


def test_new_question_restores_archive_but_regeneration_does_not(client,account):
    headers=auth(account());conversation=create(client,headers)
    question='이전 기록을 보존해 줘'
    send(client,headers,conversation,question)
    organize(client,headers,conversation,archived=True)
    retried=events(send(client,headers,conversation,question,retry=True))[-1]
    assert retried[0]=='done' and retried[1]['conversation']['archived']
    new=events(send(client,headers,conversation,'이어서 새 질문입니다.'))[-1]
    assert new[0]=='done' and not new[1]['conversation']['archived']
    assert new[1]['conversation']['organization_revision']==2


def test_stale_revision_and_other_users_cannot_override_organization(client,account):
    owner,other=auth(account()),auth(account('bob'))
    conversation=create(client,owner)
    assert organize(client,owner,conversation,archived=True).status_code==200
    assert organize(client,owner,conversation,pinned=True).status_code==409
    assert organize(client,other,conversation,revision=1,archived=False).status_code==404
    assert organize(client,{},conversation,revision=1,archived=False).status_code==401
    current=client.get('/chat/conversations/'+conversation,headers=owner).json()['conversation']
    assert current['archived'] and not current['pinned'] and current['organization_revision']==1
    assert client.get('/chat/conversations?view=archived',headers=other).json()['items']==[]


@pytest.mark.parametrize('payload',[{}, {'revision':0}, {'revision':-1,'pinned':True},
    {'revision':0,'pinned':'false'}, {'revision':False,'pinned':True},
    {'revision':0,'pinned':True,'archived':True},{'revision':0,'archived':True,'user_id':'other'}])
def test_malformed_organization_does_not_modify_the_conversation(client,account,payload):
    headers=auth(account());conversation=create(client,headers)
    result=client.patch(f'/chat/conversations/{conversation}/organization',headers=headers,json=payload)
    assert result.status_code==422
    with client.db_factory() as db:assert db.get(ConversationOrganization,conversation) is None


def test_personal_organization_is_not_exposed_to_project_members(client,account):
    owner,viewer=auth(account()),auth(account('viewer'))
    shared=project(client,owner)
    assert client.post(f'/workflows/projects/{shared["id"]}/members',headers=owner,
        json={'username':'viewer','role':'viewer'}).status_code==200
    conversation=client.post('/chat/conversations',headers=owner,json={'project_id':shared['id']}).json()['id']
    organize(client,owner,conversation,archived=True)
    listing=client.get('/chat/conversations',headers=viewer,params={'project_id':shared['id']}).json()['items']
    assert listing[0]['id']==conversation and listing[0]['read_only']
    assert not listing[0]['archived'] and not listing[0]['pinned'] and listing[0]['organization_revision']==0
    assert client.get('/chat/conversations',headers=viewer,params={'project_id':shared['id'],'view':'archived'}).json()['items']==[]
    assert organize(client,viewer,conversation,pinned=True).status_code==404


def test_archiving_a_running_chat_does_not_cancel_it_and_deletion_cascades(client,account):
    headers=auth(account());conversation=create(client,headers)
    with client.db_factory() as db:
        row=db.get(Conversation,conversation);row.generation_id='not-a-real-run';row.generation_until=int(time.time())+60;db.commit()
    assert organize(client,headers,conversation,archived=True).status_code==200
    with client.db_factory() as db:
        row=db.get(Conversation,conversation)
        assert row.generation_id=='not-a-real-run' and not row.cancel_requested
        row.generation_id=None;row.generation_until=0;db.commit()
    assert client.delete('/chat/conversations/'+conversation,headers=headers).status_code==204
    with client.db_factory() as db:assert db.get(ConversationOrganization,conversation) is None
