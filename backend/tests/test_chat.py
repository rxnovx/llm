import asyncio
import json
import time
from uuid import UUID, uuid4

from fastapi import HTTPException
import httpx
import pytest
from sqlalchemy import select

from app.core.config import Settings, get_settings
from app.modules.chat import providers
from app.modules.chat.models import Conversation, Message
from app.modules.chat.router import TurnInput, prepare


def auth(account):
    return {'Authorization': f'Bearer {account["access_token"]}'}


def create(client, headers):
    result = client.post('/chat/conversations', headers=headers, json={})
    assert result.status_code == 201, result.text
    return result.json()['id']


def send(client, headers, conversation, content='안녕, 함께 정리해 보자', **extra):
    return client.post(f'/chat/conversations/{conversation}/messages', headers=headers,
        json={'content': content, 'request_id': str(uuid4()), **extra})


def events(response):
    result = []
    for block in response.text.strip().split('\n\n'):
        lines = block.splitlines()
        result.append((lines[0].removeprefix('event: '), json.loads(lines[1].removeprefix('data: '))))
    return result


def test_chat_requires_login(client):
    assert client.get('/chat/config').status_code == 401
    assert client.get('/chat/conversations').status_code == 401
    assert client.post('/chat/conversations', json={}).status_code == 401


def test_demo_conversation_stream_persistence_rename_delete(client, account, monkeypatch):
    monkeypatch.setattr(providers, 'client', lambda *args: pytest.fail('Demo must never call a network provider'))
    headers = auth(account())
    config = client.get('/chat/config', headers=headers).json()
    assert config['connections'][0]['is_test']
    conversation = create(client, headers)
    response = send(client, headers, conversation)
    assert response.status_code == 200
    assert response.headers['x-accel-buffering'] == 'no'
    stream = events(response)
    assert stream[0][0] == 'start' and stream[-1][0] == 'done'
    text = ''.join(data['content'] for kind, data in stream if kind == 'delta')
    assert text in providers.DEMO_REPLIES
    saved = client.get(f'/chat/conversations/{conversation}', headers=headers).json()
    assert not saved['conversation']['busy']
    assert len(saved['messages']) == 2
    assert saved['messages'][1]['content'] == text and saved['messages'][1]['is_test']
    assert saved['conversation']['title'] == '안녕, 함께 정리해 보자'
    assert client.patch(f'/chat/conversations/{conversation}', headers=headers, json={'title': '내 대화'}).json()['title'] == '내 대화'
    assert client.get('/chat/conversations', headers=headers).json()['items'][0]['title'] == '내 대화'
    assert client.delete(f'/chat/conversations/{conversation}', headers=headers).status_code == 204
    assert client.get(f'/chat/conversations/{conversation}', headers=headers).status_code == 404
    with client.db_factory() as db:
        assert not db.scalars(select(Message).where(Message.conversation_id == conversation)).all()


def test_other_user_cannot_access_or_mutate_conversation(client, account):
    owner, other = auth(account('owner')), auth(account('other'))
    conversation = create(client, owner)
    assert client.get('/chat/conversations', headers=other).json()['items'] == []
    path = f'/chat/conversations/{conversation}'
    for method, suffix, data in [('GET', '', None), ('PATCH', '', {'title': 'stolen'}),
        ('DELETE', '', None), ('POST', '/cancel', {'request_id': str(uuid4())})]:
        assert client.request(method, path + suffix, headers=other, json=data).status_code == 404
    assert send(client, other, conversation).status_code == 404


def test_test_mode_disabled_does_not_fall_back_to_mock(client, account, monkeypatch):
    monkeypatch.setenv('LLM_TEST_MODE', 'false')
    get_settings.cache_clear()
    headers = auth(account())
    assert client.get('/chat/config', headers=headers).json()['connections'] == []
    assert client.post('/chat/conversations', headers=headers, json={}).status_code == 503


def test_client_cannot_supply_arbitrary_server_or_key(client, account):
    headers = auth(account())
    assert client.post('/chat/conversations', headers=headers, json={'base_url': 'http://attacker.invalid'}).status_code == 422
    conversation = create(client, headers)
    assert send(client, headers, conversation, '   ').status_code == 422
    assert send(client, headers, conversation, 'x' * 16001).status_code == 422
    assert send(client, headers, conversation, api_key='untrusted').status_code == 422


def test_failure_saves_partial_reply_and_retry_reuses_last_turn(client, account, monkeypatch):
    headers = auth(account())
    conversation = create(client, headers)
    async def broken(*args):
        yield '여기까지 받은 답변'
        raise providers.ProviderError('테스트 연결 중단')
    monkeypatch.setattr(providers, 'stream_reply', broken)
    response = events(send(client, headers, conversation, '다시 해볼까요'))
    assert response[-1][0] == 'error'
    assert response[-1][1]['message']['content'] == '여기까지 받은 답변'
    assert not response[-1][1]['conversation']['busy']
    history = []
    async def repaired(connection, model, messages, settings):
        history.extend(messages)
        yield '다시 생성한 답변'
    monkeypatch.setattr(providers, 'stream_reply', repaired)
    assert events(send(client, headers, conversation, '다시 해볼까요', retry=True))[-1][0] == 'done'
    detail = client.get(f'/chat/conversations/{conversation}', headers=headers).json()
    assert len(detail['messages']) == 2
    assert detail['messages'][1]['content'] == '다시 생성한 답변'
    assert history == [{'role': 'user', 'content': '다시 해볼까요'}]


def test_concurrent_generation_lock_cancel_and_expired_lease(client, account):
    identity = account()
    headers, user_id = auth(identity), identity['user']['subject']
    conversation = create(client, headers)
    connection = providers.Connection(id='demo', name='테스트', provider='test', model='demo')
    first = TurnInput(content='첫 메시지', request_id=uuid4())
    with client.db_factory() as db:
        prepare(db, UUID(conversation), user_id, first, connection)
    with client.db_factory() as db, pytest.raises(HTTPException) as caught:
        prepare(db, UUID(conversation), user_id, TurnInput(content='경합', request_id=uuid4()), connection)
    assert caught.value.status_code == 409
    assert client.delete(f'/chat/conversations/{conversation}', headers=headers).status_code == 409
    client.post(f'/chat/conversations/{conversation}/cancel', headers=headers, json={'request_id': str(uuid4())})
    with client.db_factory() as db:
        assert not db.get(Conversation, conversation).cancel_requested
    client.post(f'/chat/conversations/{conversation}/cancel', headers=headers, json={'request_id': str(first.request_id)})
    with client.db_factory() as db:
        item = db.get(Conversation, conversation)
        assert item.cancel_requested
        item.generation_until = int(time.time()) - 1
        db.commit()
    with client.db_factory() as db:
        prepare(db, UUID(conversation), user_id, TurnInput(content='복구', request_id=uuid4()), connection)
        rows = db.scalars(select(Message).where(Message.conversation_id == conversation).order_by(Message.position)).all()
        assert rows[1].status == 'error' and rows[-1].status == 'pending'


@pytest.mark.parametrize('kind', ['vllm', 'ollama'])
def test_provider_wire_protocol_models_key_history_and_stream(kind, monkeypatch):
    requests = []
    connection = providers.Connection(id='office', name='내부 모델', provider=kind,
        base_url='http://inference.internal:8001/v1' if kind == 'vllm' else 'http://inference.internal:11434',
        model='private-model', api_key='server-only-key')
    class Fragments(httpx.AsyncByteStream):
        async def __aiter__(self):
            if kind == 'vllm':
                raw = 'data: {"choices":[{"delta":{"content":"안녕하세요"}}]}\n\ndata: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
            else:
                raw = '{"message":{"content":"안녕하세요"},"done":false}\n{"message":{"content":""},"done":true}\n'
            data = raw.encode()
            for index in range(0, len(data), 3):
                yield data[index:index + 3]
    def upstream(request):
        requests.append(request)
        assert request.headers['authorization'] == 'Bearer server-only-key'
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'private-model'}]} if kind == 'vllm' else {'models': [{'name': 'private-model'}]})
        return httpx.Response(200, stream=Fragments())
    constructor = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kwargs: constructor(transport=httpx.MockTransport(upstream), **kwargs))
    async def run():
        assert await providers.list_models(connection) == ['private-model']
        return ''.join([part async for part in providers.stream_reply(connection, 'private-model',
            [{'role': 'user', 'content': '내 질문'}], Settings(_env_file=None))])
    assert asyncio.run(run()) == '안녕하세요'
    assert requests[0].url.path == ('/v1/models' if kind == 'vllm' else '/api/tags')
    assert requests[1].url.path == ('/v1/chat/completions' if kind == 'vllm' else '/api/chat')
    body = json.loads(requests[1].content)
    assert body['messages'] == [{'role': 'user', 'content': '내 질문'}] and body['stream'] is True
    assert 'api_key' not in connection.public() and 'base_url' not in connection.public()


@pytest.mark.parametrize('body', [b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n', b'data: invalid-json\n\n'])
def test_broken_provider_stream_is_not_reported_as_complete(body, monkeypatch):
    connection = providers.Connection(id='local', name='local', provider='vllm', base_url='http://local/v1')
    monkeypatch.setattr(providers, 'client', lambda *args: httpx.AsyncClient(trust_env=False, transport=httpx.MockTransport(lambda request: httpx.Response(200, content=body))))
    async def consume():
        return [part async for part in providers.stream_reply(connection, 'model', [], Settings(_env_file=None))]
    with pytest.raises(providers.ProviderError):
        asyncio.run(consume())


def test_invalid_connection_configuration_does_not_expose_secret(client, account, monkeypatch):
    headers = auth(account())
    monkeypatch.setenv('LLM_CONNECTIONS_JSON', '[{"id":"broken","api_key":"do-not-expose"}]')
    get_settings.cache_clear()
    response = client.get('/chat/config', headers=headers)
    assert response.status_code == 503 and 'do-not-expose' not in response.text


def test_failed_regeneration_keeps_previous_successful_answer(client, account, monkeypatch):
    headers = auth(account())
    conversation = create(client, headers)
    result = events(send(client, headers, conversation, '좋은 답변 보존'))[-1][1]['message']['content']
    async def broken(*args):
        yield '실패한 새 답변'
        raise providers.ProviderError('연결 중단')
    monkeypatch.setattr(providers, 'stream_reply', broken)
    response = events(send(client, headers, conversation, '좋은 답변 보존', retry=True))
    assert response[-1][0] == 'error'
    saved = client.get(f'/chat/conversations/{conversation}', headers=headers).json()['messages']
    assert len(saved) == 2 and saved[-1]['content'] == result and saved[-1]['status'] == 'complete'
