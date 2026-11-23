"""공개 추론 채널·최종 답변 분리와 작업 기록의 영속성 계약."""
import asyncio
from contextlib import aclosing
import json
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import event, select

from app.core.config import Settings
from app.modules.chat import providers, trace
from app.modules.chat.models import Conversation, Message
from app.modules.workspaces.models import ToolTrace


@pytest.fixture
def recorder(client, account):
    user_id = account()['user']['subject']
    conversation_id, message_id = str(uuid4()), str(uuid4())
    with client.db_factory() as db:
        db.add(Conversation(id=conversation_id, user_id=user_id, connection_id='local', model='fixture'))
        db.flush()
        db.add(Message(id=message_id, conversation_id=conversation_id, position=0, role='assistant', status='generating'))
        db.commit()
        return trace.TraceSession(db.get_bind(), user_id, message_id, str(uuid4()), secrets=('private-config-secret',))


def connection(kind='vllm', **extra):
    return providers.Connection(id='local', name='로컬', provider=kind, model='fixture',
        base_url='http://models.internal/v1' if kind == 'vllm' else 'http://models.internal',
        supports_reasoning=True, reasoning_visibility='public', supports_tools=True,
        api_key='upstream-secret-value', **extra)


def mock_stream(monkeypatch, kind, messages):
    def upstream(_request):
        if kind == 'vllm':
            body = ''.join('data: ' + json.dumps({'choices': [{'delta': message}]}, ensure_ascii=False) + '\n\n'
                           for message in messages) + 'data: [DONE]\n\n'
            return httpx.Response(200, text=body, headers={'content-type': 'text/event-stream'})
        body = ''.join(json.dumps({'message': message, 'done': False}, ensure_ascii=False) + '\n' for message in messages)
        body += json.dumps({'message': {}, 'done': True}) + '\n'
        return httpx.Response(200, text=body, headers={'content-type': 'application/x-ndjson'})
    monkeypatch.setattr(providers, 'client', lambda *_args: httpx.AsyncClient(transport=httpx.MockTransport(upstream)))


def collect(recorder, operation):
    async def run():
        return [item async for item in recorder.stream(operation())]
    return asyncio.run(run())


@pytest.mark.parametrize(('kind', 'field'), [('vllm', 'reasoning'), ('vllm', 'reasoning_content'), ('ollama', 'thinking')])
def test_public_reasoning_channel_streams_before_answer_and_is_persisted(recorder, client, monkeypatch, kind, field):
    mock_stream(monkeypatch, kind, [{field: '모델 서버가 제공한 공개 설명입니다. '}, {'content': '**한국어 답변**'}])
    metadata = {}
    async def operation():
        async for text in providers.stream_reply(connection(kind), 'fixture', [{'role': 'user', 'content': '안녕'}],
                Settings(_env_file=None), metadata=metadata):
            yield 'delta', {'content': text}
    events = collect(recorder, operation)
    assert events[0][0] == 'reasoning_delta'
    assert ''.join(item['content'] for kind, item in events if kind == 'delta') == '**한국어 답변**'
    assert ''.join(item['content'] for kind, item in events if kind == 'reasoning_delta') == '모델 서버가 제공한 공개 설명입니다. '
    assert metadata == {'has_reasoning': True, 'reasoning_chars': len('모델 서버가 제공한 공개 설명입니다. ')}
    with client.db_factory() as db:
        saved = trace.snapshot_for_message(db, recorder.message_id)
        assert saved['reasoning'] == recorder.reasoning
        assert saved['request_id'] == recorder.request_id and saved['status'] == 'complete'


@pytest.mark.parametrize(('supported', 'visibility'), [(False, 'public'), (True, 'off'), (False, 'off')])
def test_reasoning_disclosure_defaults_off_but_capability_detection_is_independent(recorder, monkeypatch, supported, visibility):
    mock_stream(monkeypatch, 'vllm', [{'reasoning': '공개하지 않을 추론'}, {'content': '최종 답변'}])
    config = connection().model_copy(update={'supports_reasoning': supported, 'reasoning_visibility': visibility})
    metadata = {}
    async def operation():
        yield 'activity', trace.record_activity('model', 'running', '서버 응답을 기다립니다.')
        async for text in providers.stream_reply(config, 'fixture', [{'role': 'user', 'content': '질문'}],
                Settings(_env_file=None), metadata=metadata):
            yield 'delta', {'content': text}
        yield 'activity', trace.record_activity('model', 'complete', '실제 응답을 받았습니다.')
    events = collect(recorder, operation)
    assert metadata['has_reasoning'] is True
    assert not any(kind == 'reasoning_delta' for kind, _ in events)
    assert recorder.reasoning == ''
    activities = [item for kind, item in events if kind == 'activity']
    assert len(activities) == 2 and activities[0]['id'] == activities[1]['id']


def test_no_invented_reasoning_from_think_tags_in_plain_content(recorder, monkeypatch):
    mock_stream(monkeypatch, 'vllm', [{'content': '<think>일반 본문</think> 한국어 답변'}])
    async def operation():
        async for text in providers.stream_reply(connection(), 'fixture', [{'role': 'user', 'content': '질문'}], Settings(_env_file=None)):
            yield 'delta', {'content': text}
    events = collect(recorder, operation)
    assert events == [('delta', {'content': '<think>일반 본문</think> 한국어 답변'})]
    assert not recorder.reasoning


def test_reasoning_redacts_split_credentials_and_configured_secrets(recorder, monkeypatch):
    raw = ('검토합니다.\nAuthorization: Bearer unknown-access-token\nAPI_KEY="private-api-secret"\n'
           'private-config-secret upstream-secret-value glpat-1234567890123456789\n'
           'https://service:private-password@gitlab.internal/path\n이후 공개 설명입니다.\n')
    # 경계가 자격 증명의 한가운데 와도 일부 비밀값을 먼저 보내지 않아야 한다.
    mock_stream(monkeypatch, 'vllm', [{'reasoning_content': raw[index:index + 3]} for index in range(0, len(raw), 3)] + [{'content': '완료'}])
    async def operation():
        async for text in providers.stream_reply(connection(), 'fixture', [{'role': 'user', 'content': '질문'}], Settings(_env_file=None)):
            yield 'delta', {'content': text}
    events = collect(recorder, operation)
    exposed = ''.join(item['content'] for kind, item in events if kind == 'reasoning_delta')
    for private in ('unknown-access-token', 'private-api-secret', 'private-config-secret', 'upstream-secret-value',
                    'glpat-1234567890123456789', 'private-password'):
        assert private not in exposed
    assert '이후 공개 설명' in exposed and '[자격 증명 숨김]' in exposed


@pytest.mark.parametrize('kind', ['vllm', 'ollama'])
@pytest.mark.parametrize('mode', ['tools', 'schema'])
def test_tool_and_schema_reasoning_streams_during_model_request(recorder, monkeypatch, kind, mode):
    if mode == 'schema':
        output = {'content': '{"ok":true}'}
    else:
        function = {'name': 'read_status', 'arguments': '{"label":"sample"}' if kind == 'vllm' else {'label': 'sample'}}
        output = {'content': '', 'tool_calls': [{'index': 0, 'id': 'tool-1', 'function': function}]}
    mock_stream(monkeypatch, kind, [{'reasoning' if kind == 'vllm' else 'thinking': '실제 모델 설명'}, output])
    async def operation():
        if mode == 'schema':
            result = await providers.structured_reply(connection(kind), 'fixture', [{'role': 'user', 'content': '질문'}],
                {'type': 'object'}, Settings(_env_file=None))
            assert result == {'ok': True}
        else:
            result = await providers.tool_reply(connection(kind), 'fixture', [{'role': 'user', 'content': '질문'}],
                [{'type': 'function', 'function': {'name': 'read_status', 'parameters': {'type': 'object'}}}], Settings(_env_file=None))
            assert result['tool_calls'][0]['function']['arguments'] == {'label': 'sample'}
        yield 'complete', {'ok': True}
    events = collect(recorder, operation)
    assert events[0][0] == 'reasoning_delta' and events[-1][0] == 'complete'
    assert recorder.reasoning == '실제 모델 설명'


def test_terminal_logs_coalesce_and_stage_times_are_transactional(recorder, client):
    with recorder.scope():
        started = trace.record_activity('workspace_run', 'running', 'pytest 실행', 3)
        for _ in range(300):
            trace.record_activity('terminal_log', 'running', '실제 출력\n', 3)
        finished = trace.record_activity('workspace_run', 'complete', '종료 코드: 0', 3)
    assert started['id'] == finished['id']
    assert finished['duration_ms'] >= 0
    with client.db_factory() as db:
        recorder.finish('complete', db=db)
        saved = trace.snapshot_for_message(db, recorder.message_id)
        assert len(saved['steps']) == 2
        assert [item['status'] for item in saved['steps']] == ['complete', 'complete']
        assert len(saved['steps'][1]['detail']) <= 6000
        db.commit()
    with client.db_factory() as db:
        assert len(db.scalars(select(ToolTrace).where(ToolTrace.message_id == recorder.message_id)).all()) == 2


def test_reasoning_checkpoint_is_bounded_not_one_db_write_per_delta(recorder):
    writes = []
    def before(_conn, _cursor, statement, _parameters, _context, _many):
        if statement.lstrip().upper().startswith(('INSERT INTO CHAT_GENERATION_TRACES', 'UPDATE CHAT_GENERATION_TRACES')):
            writes.append(statement)
    event.listen(recorder.bind, 'before_cursor_execute', before)
    async def operation():
        for _ in range(1000):
            await recorder.publish_reasoning('공개 설명 ' * 20, call_id='call', connection_id='local', model='fixture')
        yield 'complete', {'ok': True}
    try:
        events = collect(recorder, operation)
    finally:
        event.remove(recorder.bind, 'before_cursor_execute', before)
    assert len(recorder.reasoning) == trace.MAX_REASONING and recorder.reasoning_truncated
    assert len(writes) < 10
    assert sum(len(item['content']) for kind, item in events if kind == 'reasoning_delta') == trace.MAX_REASONING


def test_cancelled_stream_closes_producer_without_queue_deadlock(recorder):
    closed = []
    async def source():
        try:
            yield 'started', {}
            await asyncio.Event().wait()
        finally:
            closed.append(True)
    async def run():
        async with aclosing(recorder.stream(source())) as events:
            assert await anext(events) == ('started', {})
    asyncio.run(asyncio.wait_for(run(), timeout=2))
    assert closed == [True]


def test_retry_replaces_trace_request_without_previous_attempt_steps(recorder, client):
    with recorder.scope():
        trace.record_activity('first_attempt', 'complete', '이전 시도')
    recorder.finish('complete')
    new = trace.TraceSession(recorder.bind, recorder.user_id, recorder.message_id, str(uuid4()))
    with new.scope():
        trace.record_activity('retry', 'complete', '새 시도')
    new.finish('complete')
    with client.db_factory() as db:
        saved = trace.snapshot_for_message(db, recorder.message_id)
        assert saved['request_id'] == new.request_id
        assert [item['name'] for item in saved['steps']] == ['retry']


def test_redactor_holds_whitespace_secrets_and_oversized_private_key_blocks():
    secret = 'private phrase with spaces'
    redactor = trace.IncrementalRedactor((secret,))
    text = ('공개 설명 ' * 160) + secret + (' 정상 설명 ' * 160)
    parts = [redactor.feed(text[index:index + 17]) for index in range(0, len(text), 17)]
    parts.append(redactor.feed('', final=True))
    exposed = ''.join(parts)
    assert secret not in exposed and 'private phrase' not in exposed
    assert '[비밀값 숨김]' in exposed

    private = '-----BEGIN PRIVATE KEY-----\n' + ('A' * 16000) + '\n' + ('B' * 16000) + '\n-----END PRIVATE KEY-----\n공개 설명'
    redactor = trace.IncrementalRedactor()
    parts = [redactor.feed(private[index:index + 37]) for index in range(0, len(private), 37)]
    parts.append(redactor.feed('', final=True))
    exposed = ''.join(parts)
    assert 'AAA' not in exposed and 'BBB' not in exposed
    assert '공개 설명' in exposed


def test_trace_millisecond_columns_use_postgresql_bigint():
    from sqlalchemy.dialects import postgresql
    for model, fields in ((trace.GenerationTrace, ('started_at_ms', 'updated_at_ms')),
                          (trace.TraceActivity, ('started_at_ms', 'finished_at_ms'))):
        for field in fields:
            assert str(model.__table__.c[field].type.compile(dialect=postgresql.dialect())) == 'BIGINT'


def framed_reply(kind, messages):
    if kind == 'vllm':
        frames = [dict(id='chatcmpl-' + 'a' * 50, object='chat.completion.chunk', created=1795420800,
            model='internal-model-with-a-long-served-name', choices=[{'index': 0, 'delta': value, 'finish_reason': None}])
            for value in messages]
        return httpx.Response(200, text=''.join('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n' for value in frames)
            + 'data: [DONE]\n\n', headers={'content-type': 'text/event-stream'})
    frames = [dict(model='internal-model-with-a-long-served-name', created_at='2026-11-23T01:00:00.000000Z',
        message={'role': 'assistant', **value}, done=False, internal_metadata='framing-' * 12) for value in messages]
    return httpx.Response(200, text=''.join(json.dumps(value, ensure_ascii=False) + '\n' for value in frames)
        + json.dumps({'message': {}, 'done': True}) + '\n', headers={'content-type': 'application/x-ndjson'})


@pytest.mark.parametrize('kind', ['vllm', 'ollama'])
def test_token_by_token_answer_is_not_limited_by_repeated_json_envelopes(recorder, monkeypatch, kind):
    content = '답변abc' * 1400
    response = framed_reply(kind, [{'content': '답변abc'} for _ in range(1400)])
    assert len(response.content) > 256000 and len(content.encode()) < 256000
    monkeypatch.setattr(providers, 'client', lambda *_args: httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: response)))
    async def operation():
        result = await providers.tool_reply(connection(kind), 'fixture', [{'role': 'user', 'content': '긴 답변'}], [], Settings(_env_file=None))
        assert result['content'] == content and result['tool_calls'] == []
        yield 'complete', {'ok': True}
    assert collect(recorder, operation)[-1][0] == 'complete'


def test_fragmented_tool_arguments_are_bounded_by_payload_not_framing(recorder, monkeypatch):
    arguments = json.dumps({'label': 'x' * 7000})
    parts = [arguments[index:index + 5] for index in range(0, len(arguments), 5)]
    messages = [{'tool_calls': [{'index': 0, 'id': 'tool-fragmented', 'function': {
        **({'name': 'read_status'} if index == 0 else {}), 'arguments': part}}]} for index, part in enumerate(parts)]
    response = framed_reply('vllm', messages)
    assert len(response.content) > 256000
    monkeypatch.setattr(providers, 'client', lambda *_args: httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: response)))
    async def operation():
        result = await providers.tool_reply(connection(), 'fixture', [{'role': 'user', 'content': '자료 조회'}], [], Settings(_env_file=None))
        assert result['tool_calls'][0]['function']['arguments'] == {'label': 'x' * 7000}
        yield 'complete', {'ok': True}
    assert collect(recorder, operation)[-1][0] == 'complete'


@pytest.mark.parametrize('kind', ['vllm', 'ollama'])
@pytest.mark.parametrize('field', ['content', 'reasoning', 'arguments'])
def test_actual_oversized_stream_payload_is_still_rejected(recorder, monkeypatch, kind, field):
    text = '가' * 86000
    if field == 'arguments':
        value = {'tool_calls': [{'index': 0, 'function': {'name': 'read_status',
            'arguments': json.dumps({'label': text}, ensure_ascii=False) if kind == 'vllm' else {'label': text}}}]}
    else:
        value = {('thinking' if kind == 'ollama' and field == 'reasoning' else field): text}
    mock_stream(monkeypatch, kind, [value])
    async def operation():
        await providers.tool_reply(connection(kind), 'fixture', [{'role': 'user', 'content': '질문'}], [], Settings(_env_file=None))
        yield 'unexpected', {}
    with pytest.raises(providers.ProviderError, match='크기 제한'):
        collect(recorder, operation)


def test_stream_with_unbounded_metadata_is_still_rejected():
    from unittest.mock import AsyncMock, Mock
    body = ('data: ' + json.dumps({'metadata': 'x' * 200, 'choices': []}) + '\n\n') * 100
    response = httpx.Response(200, text=body, headers={'content-type': 'text/event-stream'})
    with pytest.raises(providers.ProviderError, match='크기 제한'):
        asyncio.run(providers.response_message(response, True, Mock(), Mock(reasoning=AsyncMock()), limit=64, streaming=True))
