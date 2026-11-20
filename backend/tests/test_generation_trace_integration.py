"""실제 채팅 API에서 SSE·새로고침·취소·재생성·버전의 작업 기록 일관성."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading
from uuid import uuid4

import httpx
import pytest

from app.modules.chat import providers
from test_chat import auth, events, send
from test_model_controls import configure


def setup(client, account, monkeypatch, *, reasoning=True):
    configure(monkeypatch, supports_reasoning=reasoning, reasoning_visibility='public' if reasoning else 'off')
    owner = auth(account())
    response = client.post('/chat/conversations', headers=owner, json={'connection_id': 'office', 'model': 'model-a'})
    assert response.status_code == 201, response.text
    return owner, response.json()['id']


def wire(reasoning, answer, *, finish=True):
    values = [{'choices': [{'delta': {'reasoning_content': reasoning}}]},
              {'choices': [{'delta': {'content': answer}}]}]
    if finish:
        values += [{'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
                   {'choices': [], 'usage': {'prompt_tokens': 19, 'completion_tokens': 8}}]
    body = ''.join('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n' for value in values)
    return body + ('data: [DONE]\n\n' if finish else '')


def use_model(monkeypatch, remote):
    monkeypatch.setattr(providers, 'client', lambda *_args: httpx.AsyncClient(transport=httpx.MockTransport(remote)))


def request(client, owner, conversation, **extra):
    return send(client, owner, conversation, '한글로 인사해 줘', connection_id='office', model='model-a',
        assistant={'automatic': False}, **extra)


def last(client, owner, conversation):
    response = client.get('/chat/conversations/' + conversation, headers=owner)
    assert response.status_code == 200, response.text
    return response.json()['messages'][-1]


def test_korean_answer_reasoning_and_metrics_are_separate_and_survive_refresh(client, account, monkeypatch):
    owner, conversation = setup(client, account, monkeypatch)
    use_model(monkeypatch, lambda _request: httpx.Response(200, text=wire('서버가 제공한 공개 설명입니다.', '**안녕하세요.**'),
        headers={'content-type': 'text/event-stream'}))
    response = request(client, owner, conversation)
    assert response.status_code == 200, response.text
    stream = events(response)
    assert stream[-1][0] == 'done', response.text[-3000:]
    answer = ''.join(data['content'] for kind, data in stream if kind == 'delta')
    reasoning = ''.join(data['content'] for kind, data in stream if kind == 'reasoning_delta')
    assert answer == '**안녕하세요.**' and reasoning == '서버가 제공한 공개 설명입니다.'
    assert [kind for kind, _ in stream].index('reasoning_delta') < [kind for kind, _ in stream].index('delta')
    saved = last(client, owner, conversation)
    trace = saved['generation_trace']
    assert trace['reasoning'] == reasoning and saved['content'] == answer
    assert trace == stream[-1][1]['message']['generation_trace']
    assert trace['metrics']['input_tokens'] == 19 and trace['metrics']['output_tokens'] == 8
    assert trace['metrics']['token_source'] == 'server_reported'
    assert trace['metrics']['first_token_ms'] is not None
    assert all(item['status'] == 'complete' and item['duration_ms'] >= 0 for item in trace['steps'])
    stranger = auth(account('bob'))
    assert client.get('/chat/conversations/' + conversation, headers=stranger).status_code == 404


def test_unsupported_reasoning_model_keeps_real_activity_without_fabricated_thoughts(client, account, monkeypatch):
    owner, conversation = setup(client, account, monkeypatch, reasoning=False)
    # 서버가 예상과 달리 별도 채널을 보내더라도 운영자의 공개 설정이 우선한다.
    use_model(monkeypatch, lambda _request: httpx.Response(200, text=wire('표시하면 안 되는 내용', '한국어 답변'),
        headers={'content-type': 'text/event-stream'}))
    stream = events(request(client, owner, conversation))
    assert stream[-1][0] == 'done'
    assert not any(kind == 'reasoning_delta' for kind, _ in stream)
    activity = [data for kind, data in stream if kind == 'activity']
    assert activity and any(item['status'] == 'running' for item in activity)
    assert any(item['status'] == 'complete' for item in activity)
    snapshot = last(client, owner, conversation)['generation_trace']
    assert snapshot['reasoning'] == '' and snapshot['steps']
    assert '표시하면 안' not in json.dumps(snapshot, ensure_ascii=False)


def test_retry_resets_trace_and_versions_and_fork_keep_their_own_snapshot(client, account, monkeypatch):
    owner, conversation = setup(client, account, monkeypatch)
    results = iter([('첫 시도 공개 설명', '첫 번째 답변'), ('새 시도 공개 설명', '두 번째 답변')])
    def remote(_request):
        return httpx.Response(200, text=wire(*next(results)), headers={'content-type': 'text/event-stream'})
    use_model(monkeypatch, remote)
    first_stream = events(request(client, owner, conversation))
    assert first_stream[-1][0] == 'done'
    first = first_stream[-1][1]['message']
    second_stream = events(request(client, owner, conversation, retry=True))
    assert second_stream[-1][0] == 'done', json.dumps(second_stream[-1], ensure_ascii=False)
    second = second_stream[-1][1]['message']
    assert second['id'] == first['id']
    assert second_stream[0][1]['assistant_message']['generation_trace'] is None
    assert second['generation_trace']['reasoning'] == '새 시도 공개 설명'
    assert second['generation_trace']['request_id'] != first['generation_trace']['request_id']
    versions = client.get(f"/workflows/messages/{first['id']}/versions", headers=owner).json()['items']
    assert [item['message']['generation_trace']['reasoning'] for item in versions] == ['첫 시도 공개 설명', '새 시도 공개 설명']
    selected = client.post(f"/workflows/messages/{first['id']}/versions/{first['active_version']}/select", headers=owner)
    assert selected.status_code == 200, selected.text
    selected_message = selected.json()['messages'][-1]
    assert selected_message['content'] == '첫 번째 답변'
    assert selected_message['generation_trace']['reasoning'] == '첫 시도 공개 설명'
    assert last(client, owner, conversation)['generation_trace']['reasoning'] == '첫 시도 공개 설명'
    forked = client.post(f'/workflows/conversations/{conversation}/fork', headers=owner, json={'message_id': first['id']})
    assert forked.status_code == 201, forked.text
    child = forked.json()
    assert child['messages'][-1]['generation_trace']['reasoning'] == '첫 시도 공개 설명'
    assert last(client, owner, child['conversation']['id'])['generation_trace']['reasoning'] == '첫 시도 공개 설명'


def test_failed_regeneration_keeps_successful_answer_and_its_trace(client, account, monkeypatch):
    owner, conversation = setup(client, account, monkeypatch)
    calls = []
    def remote(_request):
        calls.append(True)
        return httpx.Response(200, text=wire('성공한 원본 설명' if len(calls) == 1 else '실패한 새 설명',
            '보존할 답변' if len(calls) == 1 else '불완전한 답변', finish=len(calls) == 1),
            headers={'content-type': 'text/event-stream'})
    use_model(monkeypatch, remote)
    first = events(request(client, owner, conversation))[-1][1]['message']
    failed = events(request(client, owner, conversation, retry=True))
    assert failed[-1][0] == 'error'
    saved = last(client, owner, conversation)
    assert saved['content'] == first['content'] and saved['status'] == 'complete'
    assert saved['generation_trace']['reasoning'] == first['generation_trace']['reasoning']
    assert saved['generation_trace']['request_id'] == first['generation_trace']['request_id']
    assert saved['recovery']['preserved_previous'] is True


def test_refresh_and_cancel_preserve_checkpointed_reasoning_then_retry_replaces_it(client, account, monkeypatch):
    owner, conversation = setup(client, account, monkeypatch)
    blocked, closed = threading.Event(), threading.Event()
    class SlowStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            try:
                for index in range(2):
                    body = {'choices': [{'delta': {'reasoning': ('실제 공개 설명 ' * 100) + str(index)}}]}
                    yield ('data: ' + json.dumps(body, ensure_ascii=False) + '\n\n').encode()
                    if index == 0:
                        await asyncio.sleep(.85)
                blocked.set()
                await asyncio.wait_for(asyncio.Event().wait(), timeout=12)
            finally:
                closed.set()
        async def aclose(self):
            closed.set()
    use_model(monkeypatch, lambda _request: httpx.Response(200, stream=SlowStream(), headers={'content-type': 'text/event-stream'}))
    request_id = str(uuid4())
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(request, client, owner, conversation, request_id=request_id)
        try:
            assert blocked.wait(4)
            in_progress = last(client, owner, conversation)
            assert in_progress['status'] == 'pending' and in_progress['generation_trace']['reasoning']
            assert in_progress['generation_trace']['request_id'] == request_id
        finally:
            stopped = client.post(f'/chat/conversations/{conversation}/cancel', headers=owner, json={'request_id': request_id})
            assert stopped.status_code == 204, stopped.text
        stream = events(pending.result(timeout=4))
    assert stream[-1][0] == 'cancelled'
    assert closed.is_set()
    saved = last(client, owner, conversation)
    assert saved['generation_trace']['status'] == 'cancelled' and saved['generation_trace']['reasoning']
    assert any(item['status'] == 'cancelled' for item in saved['generation_trace']['steps'])
    use_model(monkeypatch, lambda _request: httpx.Response(200, text=wire('재시도 설명', '정상 완료'),
        headers={'content-type': 'text/event-stream'}))
    retried = events(request(client, owner, conversation, retry=True))
    assert retried[-1][0] == 'done', json.dumps(retried[-1], ensure_ascii=False)
    assert last(client, owner, conversation)['generation_trace']['reasoning'] == '재시도 설명'


def test_upstream_failure_never_silently_uses_demo_or_invented_reasoning(client, account, monkeypatch):
    owner, conversation = setup(client, account, monkeypatch)
    use_model(monkeypatch, lambda _request: httpx.Response(503, text='private-provider-detail'))
    stream = events(request(client, owner, conversation))
    assert stream[-1][0] == 'error'
    assert not any(kind in {'delta', 'reasoning_delta'} for kind, _ in stream)
    saved = last(client, owner, conversation)
    assert saved['content'] == '' and not saved['is_test']
    assert saved['generation_trace']['reasoning'] == ''
    assert 'private-provider-detail' not in json.dumps(saved)
