import asyncio
from types import SimpleNamespace
from urllib.parse import parse_qs
from uuid import uuid4

from fastapi import HTTPException
import httpx
import pytest
from sqlalchemy import select

from app.modules.assistant import context, personal_tools, web_search
from app.modules.assistant.models import Memory
from app.modules.chat.models import Conversation, Message
from test_chat import auth, create


def account_chat(client, account_data, *, project_id=None):
    headers = auth(account_data)
    if not project_id:
        return create(client, headers)
    response = client.post('/chat/conversations', headers=headers, json={'project_id': project_id})
    assert response.status_code == 201, response.text
    return response.json()['id']


def add_message(client, chat, content, *, status='complete'):
    with client.db_factory() as db:
        messages = list(db.scalars(select(Message.position).where(Message.conversation_id == chat)))
        message = Message(conversation_id=chat, position=max(messages, default=-1) + 1, role='assistant',
                          content=content, status=status)
        db.add(message)
        db.commit()
        return message.id


def bind(client):
    with client.db_factory() as db:
        return db.get_bind()


def test_memory_commands_own_explicit_content_and_reuse_preference_settings(client, account):
    alice, bob = account(), account('bob')
    headers = auth(alice)
    uid = alice['user']['subject']
    chat = account_chat(client, alice)

    def run(question, *, reply_id=None):
        return personal_tools.run_memory(bind(client), uid, personal_tools.memory_command(question),
            conversation_id=chat, assistant_id=reply_id or add_message(client, chat, ''))

    assert personal_tools.memory_command('“이건 기억해 줘”') is None
    assert personal_tools.memory_command('> 이건 기억해 줘') is None
    assert personal_tools.memory_command('기억해 달라고 말하면 어떻게 돼?') is None
    assert personal_tools.memory_command('이 파일을 삭제해 줘') is None
    assert personal_tools.memory_command('이걸 기억하지 말아 줘') is None
    assert personal_tools.memory_command('다음 내용:\n\n표 먼저\n\n기억해 줘.').target == '표 먼저'
    assert '저장했습니다' in run('“표 먼저”라고 기억해 줘.')
    assert client.get('/assistant/preferences', headers=headers).json()['memory_enabled']
    assert client.get('/assistant/memories', headers=auth(bob)).json()['items'] == []
    assert '인증 정보' in run('비밀번호는 examplepassword라고 기억해 줘.')

    # API and chat use the same idempotent write path. A previously disabled
    # preference stays disabled when a later chat memory is saved.
    assert client.post('/assistant/memories', headers=headers, json={'content': '표 먼저'}).status_code == 201
    assert len(client.get('/assistant/memories', headers=headers).json()['items']) == 1
    assert client.put('/assistant/preferences', headers=headers,
                      json={'revision': 1, 'memory_enabled': False, 'instructions': ''}).status_code == 200
    add_message(client, chat, '## 내용\n\n- private-data sample')
    response = personal_tools.run_memory(bind(client), uid, personal_tools.memory_command('이 답변 형식을 다음에도 기억해 줘.'),
        conversation_id=chat, assistant_id=add_message(client, chat, ''))
    assert '사용을 켜' in response
    memories = client.get('/assistant/memories', headers=headers).json()['items']
    assert len(memories) == 2
    assert any('제목으로 내용을 나누기' in item['content'] for item in memories)
    assert all('private-data' not in item['content'] for item in memories)
    assert not client.get('/assistant/preferences', headers=headers).json()['memory_enabled']
    assert '표 먼저' in run('기억 목록 보여 줘.')
    reply = add_message(client, chat, '')
    assert '삭제했습니다' in run('1번 기억을 잊어 줘.', reply_id=reply)
    assert '이미 반영' in run('1번 기억을 잊어 줘.', reply_id=reply)
    assert len(client.get('/assistant/memories', headers=headers).json()['items']) == 1
    with client.db_factory() as db:
        assert len(list(db.scalars(select(Memory)))) == 1


def test_memory_command_cancelled_before_mutation_and_mismatched_assistant(client, account):
    alice = account()
    uid = alice['user']['subject']
    chat, other_chat = account_chat(client, alice), account_chat(client, alice)
    reply, other_reply = add_message(client, chat, ''), add_message(client, other_chat, '')
    request_id = str(uuid4())
    with client.db_factory() as db:
        conversation = db.get(Conversation, chat)
        conversation.generation_id = request_id
        conversation.cancel_requested = True
        db.commit()
    command = personal_tools.memory_command('테이블 형식이라고 기억해 줘.')
    with pytest.raises(HTTPException, match='진행 중인 대화'):
        personal_tools.run_memory(bind(client), uid, command, conversation_id=chat,
                                  assistant_id=reply, request_id=request_id)
    with pytest.raises(HTTPException, match='진행 중인 대화'):
        personal_tools.run_memory(bind(client), uid, command, conversation_id=chat, assistant_id=other_reply)
    assert client.get('/assistant/memories', headers=auth(alice)).json()['items'] == []


def test_history_uses_same_project_and_completed_messages_and_rechecks_moves(client, account):
    alice, bob, carol = account(), account('bob'), account('carol')
    ah, bh = auth(alice), auth(bob)
    uid = alice['user']['subject']
    project_id = client.post('/workflows/projects', headers=ah, json={'name': '팀 데이터'}).json()['id']
    assert client.post(f'/workflows/projects/{project_id}/members', headers=ah,
                       json={'username': 'bob', 'role': 'editor'}).status_code == 200
    project_current = account_chat(client, alice, project_id=project_id)
    project_history = account_chat(client, bob, project_id=project_id)
    private_current, private_history = account_chat(client, alice), account_chat(client, alice)
    add_message(client, account_chat(client, carol), '건축회의 다른 사용자의 비공개 대화')
    project_message = add_message(client, project_history, '건축회의 결정을 검토했습니다.')
    add_message(client, project_history, '건축회의 아직 생성 중인 답변', status='pending')
    add_message(client, project_current, '건축회의 지금 대화')
    private_message = add_message(client, private_history, '건축회의 개인 결정')

    assert personal_tools.history_requested('이전 대화에서 건축회의 결정을 찾아서 알려 줘.')
    assert personal_tools.history_query('이전 대화에서 건축회의 결정을 찾아서 알려 줘.') == '건축회의'
    assert personal_tools.history_query('전에 얘기한 건축회의가 뭐였지?') == '건축회의가'
    assert personal_tools.history_query('지난번에 얘기한 건축회의 다시 알려 줘.') == '건축회의'
    engine = bind(client)
    personal = personal_tools.search_history(engine, uid, private_current, '건축회의')
    shared = personal_tools.search_history(engine, uid, project_current, '건축회의')
    assert personal_tools.search_history(engine, uid, project_current, '건축회의가') == shared
    assert [source['message_id'] for source in personal] == [private_message]
    assert [source['message_id'] for source in shared] == [project_message]
    assert '생성 중' not in shared[0]['excerpt']
    personal_tools.verify_history(engine, uid, project_current, shared)
    with client.db_factory() as db:
        db.get(Message, project_message).content = '수정했습니다.'
        db.commit()
    with pytest.raises(HTTPException, match='내용이 바뀌었습니다'):
        personal_tools.verify_history(engine, uid, project_current, shared)
    assert client.delete(f"/workflows/projects/{project_id}/members/{bob['user']['subject']}", headers=ah).status_code == 200
    with pytest.raises(HTTPException, match='범위가 바뀌었습니다'):
        personal_tools.verify_history(engine, uid, project_current, shared)
    assert client.get('/chat/conversations/' + project_history, headers=bh).status_code == 200

    reply = add_message(client, project_current, '')
    result = personal_tools.run_memory(engine, uid, personal_tools.memory_command('기억 목록'),
        conversation_id=project_current, assistant_id=reply)
    assert '개인 대화에서' in result
    assert client.put('/assistant/preferences', headers=ah,
                      json={'revision': 0, 'memory_enabled': True, 'instructions': '개인 지침'}).status_code == 200
    with client.db_factory() as db:
        assert context.personal(db, uid, conversation_id=project_current) == ''
        assert '개인 지침' in context.personal(db, uid, conversation_id=private_current)


def transport(monkeypatch, callback):
    monkeypatch.setattr(web_search, 'client', lambda: httpx.AsyncClient(
        transport=httpx.MockTransport(callback), trust_env=False, follow_redirects=False))


def test_web_search_fixed_endpoint_sanitizes_results_and_only_sends_current_public_terms(monkeypatch):
    calls = []

    def response(request):
        calls.append((request.method, str(request.url), parse_qs(request.content.decode())))
        return httpx.Response(200, json={'results': [
            {'url': 'https://python.org/downloads/', 'title': '<b>Python</b>', 'content': '공식 릴리스 &amp; 안내'},
            {'url': 'https://python.org/downloads/', 'title': 'duplicate', 'content': 'duplicate'},
            {'url': 'javascript:alert(1)', 'title': 'unsafe', 'content': 'unsafe'},
            {'url': 'https://someone:password@example.test/', 'title': 'userinfo', 'content': 'userinfo'},
        ]})

    transport(monkeypatch, response)
    settings = SimpleNamespace(assistant_web_search_url='http://search.internal')
    assert web_search.requested('Python 최신 버전 공식 문서를 찾아 줘.')
    assert not web_search.requested('첨부한 자료를 웹 검색에 사용해 줘.')
    assert not web_search.requested('Python 최신 버전을 검색하지 말고 답해 줘.')
    assert not web_search.requested('Python 최신 버전을 인터넷 사용하지 말고 정리해 줘.')
    assert not web_search.requested('“웹 검색해 줘”')
    result = asyncio.run(web_search.search(settings, 'Python PrivateAttachmentContent',
        question='Python을 웹에서 검색해 줘. alice@example.com sk-FAKEEXAMPLE1234 https://internal.test/private'))
    assert calls == [('POST', 'http://search.internal/search', {'format': ['json'], 'q': ['Python을']})]
    assert result == [{'app': 'web', 'title': 'Python', 'url': 'https://python.org/downloads/', 'excerpt': '공식 릴리스 & 안내'}]


def test_web_search_rejects_missing_configuration_redirect_large_or_nonmatching_queries(monkeypatch):
    settings = SimpleNamespace(assistant_web_search_url='https://operator.test/search')
    calls = []

    def redirect(request):
        calls.append(str(request.url))
        return httpx.Response(302, headers={'location': 'http://somewhere.test'})

    transport(monkeypatch, redirect)
    with pytest.raises(HTTPException, match='주제를'):
        asyncio.run(web_search.search(settings, 'SecretFromPreviousTurn', question='Python 웹 검색해 줘.'))
    assert calls == []
    with pytest.raises(HTTPException, match='요청을 거절'):
        asyncio.run(web_search.search(settings, 'Python', question='Python 웹 검색해 줘.'))
    assert calls == ['https://operator.test/search']
    for url in ['', 'https://name:password@operator.test/search', 'https://operator.test/search?existing=1',
                'file:///tmp/config']:
        assert not web_search.available(SimpleNamespace(assistant_web_search_url=url))
    settings.assistant_web_search_url = ''
    with pytest.raises(HTTPException, match='서버를 연결'):
        asyncio.run(web_search.search(settings, '', question='Python 웹 검색해 줘.'))
    settings.assistant_web_search_url = 'https://operator.test/search'
    transport(monkeypatch, lambda _: httpx.Response(200, content=b'x' * (web_search.MAX_RESPONSE + 1)))
    with pytest.raises(HTTPException, match='크기 제한'):
        asyncio.run(web_search.search(settings, '', question='Python 웹 검색해 줘.'))
