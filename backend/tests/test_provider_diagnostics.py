import asyncio
import json
import ssl

import httpx
import pytest

from app.core.config import Settings
from app.modules.chat import providers


HISTORY = [{'role': 'user', 'content': '상태를 확인해 주세요.'}]
TOOLS = [{'type': 'function', 'function': {
    'name': 'read_status', 'description': 'Read a synthetic status.',
    'parameters': {'type': 'object', 'properties': {'label': {'type': 'string'}}},
}}]
PRIVATE = 'private-upstream-detail'


def setup(monkeypatch, kind, handler):
    base = 'https://model.example.test'
    connection = providers.Connection(id='probe', name='probe', provider=kind,
        base_url=base + ('/v1' if kind == 'vllm' else ''), api_key=PRIVATE, model='fixture', supports_tools=True)
    constructor = httpx.AsyncClient
    monkeypatch.setattr(providers.httpx, 'AsyncClient',
        lambda **options: constructor(transport=httpx.MockTransport(handler), **options))
    return connection


@pytest.mark.parametrize('kind,phase,status,hint', [
    ('vllm', 'list', 401, 'API 키'),
    ('ollama', 'list', 404, 'API 경로'),
    ('ollama', 'list', 302, '리디렉션'),
    ('vllm', 'text', 429, '동시 요청 한도'),
    ('ollama', 'text', 503, '추론 로그'),
    ('vllm', 'schema', 400, 'JSON 스키마'),
    ('ollama', 'tools', 422, '서버 파서'),
])
def test_provider_rejection_has_a_specific_hint_without_upstream_data(monkeypatch, kind, phase, status, hint):
    connection = setup(monkeypatch, kind, lambda _: httpx.Response(status, text=PRIVATE))
    settings = Settings(_env_file=None)

    async def run():
        if phase == 'list':
            await providers.list_models(connection)
        elif phase == 'schema':
            await providers.structured_reply(connection, 'fixture', HISTORY, {'type': 'object'}, settings)
        elif phase == 'tools':
            await providers.tool_reply(connection, 'fixture', HISTORY, TOOLS, settings)
        else:
            async for _ in providers.stream_reply(connection, 'fixture', HISTORY, settings):
                pytest.fail('A rejected response cannot send content')

    with pytest.raises(providers.ProviderError) as error:
        asyncio.run(run())
    message = str(error.value)
    assert f'HTTP {status}' in message and hint in message
    assert PRIVATE not in message and 'model.example.test' not in message


@pytest.mark.parametrize('failure,hint', [
    ('refused', 'FastAPI 실행 환경'),
    ('connect_timeout', '접속 시간이 초과'),
    ('read_timeout', '모델 로딩'),
    ('tls', 'TLS 인증서'),
    ('invalid_ca', 'CA 번들을 읽지'),
    ('interrupted', 'HTTP 통신이 중단'),
])
def test_provider_transport_failure_is_actionable_and_sanitized(monkeypatch, failure, hint):
    def upstream(request):
        if failure == 'tls':
            raise httpx.ConnectError(PRIVATE, request=request) from ssl.SSLCertVerificationError(PRIVATE)
        errors = {'refused': httpx.ConnectError, 'connect_timeout': httpx.ConnectTimeout,
            'read_timeout': httpx.ReadTimeout, 'interrupted': httpx.RemoteProtocolError}
        raise errors[failure](PRIVATE, request=request)

    connection = setup(monkeypatch, 'vllm', upstream)
    if failure == 'invalid_ca':
        def invalid_ca():
            raise OSError(PRIVATE)
        monkeypatch.setattr(providers, 'assistant_tls', invalid_ca)
    with pytest.raises(providers.ProviderError, match=hint) as error:
        asyncio.run(providers.list_models(connection))
    assert PRIVATE not in str(error.value)


@pytest.mark.parametrize('kind', ['vllm', 'ollama'])
def test_tool_call_contract_preserves_arguments_for_followup(monkeypatch, kind):
    calls = []
    args = {'label': 'sample'}

    def upstream(request):
        calls.append(json.loads(request.content))
        assert request.url.path == ('/v1/chat/completions' if kind == 'vllm' else '/api/chat')
        assert calls[-1]['stream'] is False
        if len(calls) == 1:
            message = {'content': '', 'tool_calls': [{'id': 'tool-1', 'function': {'name': 'read_status',
                'arguments': json.dumps(args) if kind == 'vllm' else args}}]}
        else:
            message = {'content': '확인했습니다.'}
        return httpx.Response(200, json={'choices': [{'message': message}]} if kind == 'vllm' else {'message': message})

    connection = setup(monkeypatch, kind, upstream)
    settings = Settings(_env_file=None)

    async def run():
        decision = await providers.tool_reply(connection, 'fixture', HISTORY, TOOLS, settings)
        assert decision['tool_calls'][0]['function']['arguments'] == args
        return await providers.tool_reply(connection, 'fixture',
            [*HISTORY, decision, {'role': 'tool', 'tool_call_id': 'tool-1', 'tool_name': 'read_status', 'content': 'ok'}],
            TOOLS, settings)

    assert asyncio.run(run())['content'] == '확인했습니다.'
    assert (calls[0].get('tool_choice') == 'auto') is (kind == 'vllm')
    assert calls[1]['messages'][1]['tool_calls'][0]['function']['arguments'] == (json.dumps(args) if kind == 'vllm' else args)
