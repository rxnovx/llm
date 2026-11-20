import asyncio
import json

import httpx
from httpx import AsyncClient as HttpClient
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.chat import diagnostics, providers, usage
from test_admin import headers, operator
from test_chat import events


def configure(monkeypatch, kind='vllm', **extra):
    monkeypatch.setenv('LLM_CONNECTIONS_JSON', json.dumps([{
        'id': 'office', 'name': '진단 모델', 'provider': kind,
        'base_url': 'https://private-model.example.test/v1' if kind == 'vllm' else 'https://private-model.example.test',
        'api_key': 'secret-never-expose', 'model': 'fixture', 'supports_tools': False, 'supports_vision': False,
        'context_window': 8192, 'max_output_tokens': 256, 'max_concurrent': 2, **extra,
    }]))
    get_settings.cache_clear()


def upstream(monkeypatch, kind, *, reasoning=True, reports=True, fail=False):
    calls = []
    async def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        assert request.headers['authorization'] == 'Bearer secret-never-expose'
        assert request.url.path == ('/v1/chat/completions' if kind == 'vllm' else '/api/chat')
        await asyncio.sleep(.005)
        if fail:
            return httpx.Response(503, text='secret-never-expose private-upstream-stacktrace')
        tools = payload.get('tools')
        counts = {'usage': {'prompt_tokens': 17, 'completion_tokens': 5},
                  'metrics': {'time_to_first_token_ms': 12.5, 'generation_time_ms': 100, 'queue_time_ms': 3, 'tokens_per_second': 45.0}} if kind == 'vllm' else {
                      'prompt_eval_count': 17, 'eval_count': 5, 'eval_duration': 100_000_000}
        if not reports:
            counts = {}
        if tools:
            message = {'content': '', 'tool_calls': [{'id': 'diagnostic-1', 'function': {
                'name': 'diagnostic_echo', 'arguments': json.dumps({'value': 'probe'}) if kind == 'vllm' else {'value': 'probe'}}}]}
            return httpx.Response(200, json=({'choices': [{'message': message}]} if kind == 'vllm' else {'message': message}) | counts)
        if payload.get('response_format') or payload.get('format'):
            message = {'content': '{"status":"ok"}'}
            return httpx.Response(200, json=({'choices': [{'message': message}]} if kind == 'vllm' else {'message': message}) | counts)
        prompt = payload['messages'][-1]['content']
        if isinstance(prompt, list) or payload['messages'][-1].get('images'):
            reply = '빨간색'
        elif 'CONTEXT-' in prompt:
            reply = prompt.splitlines()[-1]
        else:
            reply = '연결 확인했습니다.'
        if kind == 'vllm':
            parts = []
            if reasoning:
                parts.append({'choices': [{'delta': {'reasoning': '모델이 제공한 진단 추론입니다.'}, 'finish_reason': None}]})
            parts.extend([{'choices': [{'delta': {'content': reply[:2]}, 'finish_reason': None}]},
                          {'choices': [{'delta': {'content': reply[2:]}, 'finish_reason': 'stop'}]},
                          {'choices': [], **counts}])
            return httpx.Response(200, text=''.join('data: ' + json.dumps(part, ensure_ascii=False) + '\n\n' for part in parts) + 'data: [DONE]\n\n')
        parts = [{'message': {'content': reply[:2], **({'thinking': '모델이 제공한 추론'} if reasoning else {})}, 'done': False},
                 {'message': {'content': reply[2:]}, 'done': False}, {'message': {'content': ''}, 'done': True, **counts}]
        return httpx.Response(200, text='\n'.join(json.dumps(part, ensure_ascii=False) for part in parts))
    original = HttpClient
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(handle), **kw))
    return calls


@pytest.mark.parametrize('kind', ['vllm', 'ollama'])
def test_actual_capabilities_are_explicit_bounded_persisted_and_timed(client, account, monkeypatch, kind):
    configure(monkeypatch, kind)
    calls = upstream(monkeypatch, kind)
    admin = headers(operator(client, account))
    path = '/admin/connections/office/capabilities'
    assert client.get(path, headers=admin).json()['items'] == []
    checks = ['chat', 'streaming', 'tools', 'structured_output', 'reasoning', 'vision', 'context', 'concurrency']
    result = client.post(path + '/probe', headers=admin, json={'checks': checks, 'context_tokens': 512})
    assert result.status_code == 200, result.text
    items = {item['capability']: item for item in result.json()['items']}
    assert len(calls) == 9
    assert all(item['status'] == ('limited' if capability == 'context' else 'verified') for capability, item in items.items())
    assert items['concurrency']['metrics']['overlap_observed']
    assert items['streaming']['metrics']['input_tokens'] == 17
    assert items['streaming']['metrics']['token_source'] == 'server_reported'
    assert items['streaming']['metrics']['tokens_per_second'] == (45 if kind == 'vllm' else 50)
    assert all(item['checked_at'] and item['elapsed_ms'] >= 0 for item in items.values())
    assert len(client.get(path, headers=admin).json()['items']) == 8
    # 검사는 도구 사용/이미지 공개 설정을 자동으로 켜지 않는다.
    config = client.get('/admin/connections', headers=admin).json()['items'][0]
    assert not config['supports_tools'] and not config['supports_vision'] and config['reasoning_visibility'] == 'off'
    stats = client.get('/admin/telemetry', headers=admin)
    assert stats.status_code == 200, stats.text
    model = stats.json()['models'][0]
    assert model['calls'] == 9 and model['reported_calls'] == 9
    assert model['reported_input_tokens'] == 17 * 9 and model['reported_output_tokens'] == 5 * 9
    assert model['errors'] == 0 and model['error_rate'] == 0
    assert model['average_tokens_per_second'] == (45 if kind == 'vllm' else 50)
    assert all(secret not in result.text + stats.text for secret in ['secret-never-expose', 'private-model.example.test', '모델이 제공한 진단 추론'])
    audit = client.get('/admin/audit', headers=admin)
    assert 'admin.model.capabilities' in audit.text


def test_reasoning_absence_and_missing_token_counts_are_not_fabricated(client, account, monkeypatch):
    configure(monkeypatch)
    upstream(monkeypatch, 'vllm', reasoning=False, reports=False)
    admin = headers(operator(client, account))
    result = client.post('/admin/connections/office/capabilities/probe', headers=admin,
        json={'checks': ['reasoning', 'streaming', 'streaming']})
    assert result.status_code == 200, result.text
    assert [item['status'] for item in result.json()['items']] == ['unverified', 'verified']
    metrics = result.json()['items'][1]['metrics']
    assert metrics['input_tokens'] is None and metrics['tokens_per_second'] is None
    assert metrics['is_estimate'] and metrics['token_source'] == 'unavailable'
    model = client.get('/admin/telemetry', headers=admin).json()['models'][0]
    assert model['calls'] == 2 and model['reported_calls'] == 0
    assert model['average_tokens_per_second'] is None


def test_capability_changes_invalidate_previous_success(client, account, monkeypatch):
    configure(monkeypatch)
    calls = upstream(monkeypatch, 'vllm')
    admin = headers(operator(client, account))
    path = '/admin/connections/office/capabilities'
    assert client.post(path + '/probe', headers=admin, json={'checks': ['streaming']}).status_code == 200
    assert client.get(path, headers=admin).json()['items'][0]['status'] == 'verified'
    configure(monkeypatch, context_window=4096)
    item = client.get(path, headers=admin).json()['items'][0]
    assert item['status'] == 'unverified' and item['stale'] and item['metrics'] == {}
    assert len(calls) == 1  # 상태 조회나 설정 변경만으로 검사하지 않는다.


def test_probe_failure_is_persisted_without_sensitive_upstream_body(client, account, monkeypatch):
    configure(monkeypatch)
    upstream(monkeypatch, 'vllm', fail=True)
    admin = headers(operator(client, account))
    result = client.post('/admin/connections/office/capabilities/probe', headers=admin, json={'checks': ['streaming']})
    assert result.status_code == 200
    assert result.json()['items'][0]['status'] == 'failed' and '503' in result.json()['items'][0]['detail']
    telemetry = client.get('/admin/telemetry', headers=admin)
    assert telemetry.json()['models'][0]['error_rate'] == 100
    assert 'secret-never-expose' not in result.text + telemetry.text
    assert 'private-upstream-stacktrace' not in result.text + telemetry.text
    with client.db_factory() as db:
        row = db.scalar(select(usage.ModelCallTelemetry))
        assert row.status == 'error' and row.error_code == 'upstream_rejected'


def test_diagnostics_require_current_admin_and_test_mode_is_never_verified(client, account, monkeypatch):
    member = headers(account())
    for path, method, body in [('/admin/telemetry', 'GET', None),
                               ('/admin/connections/demo/capabilities', 'GET', None),
                               ('/admin/connections/demo/capabilities/probe', 'POST', {})]:
        assert client.request(method, path, json=body).status_code == 401
        assert client.request(method, path, headers=member, json=body).status_code == 403
    admin = headers(operator(client, account))
    result = client.post('/admin/connections/demo/capabilities/probe', headers=admin, json={'checks': ['streaming']})
    assert result.status_code == 200 and result.json()['items'][0]['status'] == 'unavailable'
    assert client.post('/admin/connections/demo/capabilities/probe', headers=admin, json={'checks': ['unknown']}).status_code == 422
    assert client.post('/admin/connections/demo/capabilities/probe', headers=admin, json={'context_tokens': 100000}).status_code == 422


def test_invalid_server_timings_are_ignored_and_estimates_do_not_become_speed():
    meter = usage.Meter()
    meter.output('한국어 답변')
    meter.observe({'usage': {'prompt_tokens': 3, 'completion_tokens': 7}, 'metrics': {
        'time_to_first_token_ms': -1, 'generation_time_ms': float('nan'),
        'queue_time_ms': True, 'tokens_per_second': float('inf')}}, True)
    result = meter.metrics()
    assert result['server_first_token_ms'] is None and result['server_generation_ms'] is None
    assert result['server_queue_ms'] is None and result['timing_source'] == 'client_observed'
    assert not result['is_estimate']
    unknown = usage.Meter()
    unknown.output('토큰 수 없는 응답')
    assert unknown.metrics()['tokens_per_second'] is None
    nonstream = usage.Meter(streaming=False)
    nonstream.output('한 번에 수신한 답변')
    assert nonstream.metrics()['first_token_ms'] is None
    server_only = usage.Meter()
    server_only.observe({'metrics': {'time_to_first_token_ms': 8.5}}, True)
    assert server_only.metrics()['server_first_token_ms'] == 8.5


def test_connection_failures_have_separate_sanitized_rate(client, account, monkeypatch):
    configure(monkeypatch)
    original = HttpClient
    def unavailable(request):
        raise httpx.ConnectError('secret-never-expose', request=request)
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(unavailable), **kw))
    admin = headers(operator(client, account))
    result = client.post('/admin/connections/office/capabilities/probe', headers=admin, json={'checks': ['streaming']})
    assert result.status_code == 200 and result.json()['items'][0]['status'] == 'failed'
    stats = client.get('/admin/telemetry', headers=admin)
    assert stats.json()['models'][0]['connection_error_rate'] == 100
    assert 'secret-never-expose' not in result.text + stats.text


def test_newest_probe_replaces_previous_result_within_same_second(client, account, monkeypatch):
    configure(monkeypatch)
    upstream(monkeypatch, 'vllm')
    admin = headers(operator(client, account))
    path = '/admin/connections/office/capabilities'
    assert client.post(path + '/probe', headers=admin, json={'checks': ['streaming']}).json()['items'][0]['status'] == 'verified'
    upstream(monkeypatch, 'vllm', fail=True)
    assert client.post(path + '/probe', headers=admin, json={'checks': ['streaming']}).json()['items'][0]['status'] == 'failed'
    assert client.get(path, headers=admin).json()['items'][0]['status'] == 'failed'


def test_failed_concurrent_probe_cancels_sibling_and_releases_admission(client, account, monkeypatch):
    configure(monkeypatch)
    from app.modules.chat.models import Admission
    entered = asyncio.Event()
    calls, cancelled = [], []
    async def handle(request):
        calls.append(request)
        if len(calls) == 1:
            await asyncio.wait_for(entered.wait(), 2)
            return httpx.Response(503)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.append(True)
    monkeypatch.setattr(providers.httpx, 'AsyncClient', lambda **kw: HttpClient(transport=httpx.MockTransport(handle), **kw))
    admin = headers(operator(client, account))
    result = client.post('/admin/connections/office/capabilities/probe', headers=admin, json={'checks': ['concurrency']})
    assert result.status_code == 200 and result.json()['items'][0]['status'] == 'failed'
    assert len(calls) == 2 and cancelled == [True]
    with client.db_factory() as db:
        assert not db.scalars(select(Admission).where(Admission.state.in_(['running', 'queued']))).all()
        assert sorted(db.scalars(select(usage.ModelCallTelemetry.status))) == ['cancelled', 'error']


@pytest.mark.parametrize('visibility', ['off', 'public'])
def test_chat_stream_records_model_metrics_without_merging_reasoning(client, account, monkeypatch, visibility):
    configure(monkeypatch, supports_reasoning=True, reasoning_visibility=visibility)
    calls = upstream(monkeypatch, 'vllm')
    access = headers(account())
    created = client.post('/chat/conversations', headers=access, json={'connection_id': 'office', 'model': 'fixture'})
    assert created.status_code == 201
    from uuid import uuid4
    response = client.post(f'/chat/conversations/{created.json()["id"]}/messages', headers=access,
        json={'content': '안녕하세요', 'connection_id': 'office', 'model': 'fixture', 'request_id': str(uuid4())})
    assert response.status_code == 200, response.text
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    assert len(calls) == 1
    assert ''.join(data['content'] for name, data in stream if name == 'delta') == '연결 확인했습니다.'
    assert bool([name for name, _ in stream if name == 'reasoning_delta']) == (visibility == 'public')
    metric_events = [data['metrics'] for name, data in stream if name == 'generation_metrics']
    assert metric_events and metric_events[-1]['input_tokens'] == 17
    assert metric_events[-1]['output_tokens'] == 5 and metric_events[-1]['tokens_per_second'] == 45
    assert metric_events[-1]['model_calls'] == 1 and not metric_events[-1]['is_estimate']
    saved = client.get(f'/chat/conversations/{created.json()["id"]}', headers=access).json()['messages'][-1]
    assert saved['content'] == '연결 확인했습니다.'
    assert saved['generation_trace']['metrics']['input_tokens'] == 17
    assert bool(saved['generation_trace']['reasoning']) == (visibility == 'public')
