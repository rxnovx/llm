import asyncio
from concurrent.futures import ThreadPoolExecutor
import json
import threading

import httpx

from app.modules.chat import providers
from test_admin import operator
from test_chat import auth, events, send
from test_model_controls import configure


def model_response(text='연결 확인', *, report=True):
    values = [{'choices': [{'delta': {'content': text}, 'finish_reason': None}]},
              {'choices': [{'delta': {}, 'finish_reason': 'stop'}]}]
    if report:
        values.append({'choices': [], 'usage': {'prompt_tokens': 17, 'completion_tokens': 5}})
    return httpx.Response(200, text=''.join('data: ' + json.dumps(value) + '\n\n' for value in values) + 'data: [DONE]\n\n')


def use_model(monkeypatch, handler):
    monkeypatch.setattr(providers, 'client', lambda *_: httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def test_probe_uses_real_inference_after_listing_models(client, account, monkeypatch):
    configure(monkeypatch, model='')
    admin = auth(operator(client, account))
    requested = []

    def handle(request):
        requested.append(request.url.path)
        if request.method == 'GET':
            return httpx.Response(200, json={'data': [{'id': 'model-a'}]})
        assert json.loads(request.content)['model'] == 'model-a'
        return model_response()

    use_model(monkeypatch, handle)
    result = client.post('/admin/connections/office/probe', headers=admin, json={})
    assert result.status_code == 200, result.text
    assert result.json()['model'] == 'model-a' and result.json()['sample'] == '연결 확인'
    assert requested == ['/v1/models', '/v1/chat/completions']
    use_model(monkeypatch, lambda request: handle(request) if request.method == 'GET' else httpx.Response(503))
    failed = client.post('/admin/connections/office/probe', headers=admin, json={})
    assert failed.status_code == 502 and '목록 조회 성공' in failed.json()['detail']


def test_gateway_key_permissions_quotas_and_model_reported_usage(client, account, monkeypatch):
    configure(monkeypatch, allowed_users=['alice'])
    admin, identity = auth(operator(client, account)), account()
    user = identity['user']['subject']
    issued = client.post('/admin/service-keys', headers=admin, json={'user_id': user, 'label': '사내 SDK', 'daily_tokens': 0})
    assert issued.status_code == 201, issued.text
    key = issued.json()
    service = {'Authorization': 'Bearer ' + key['secret']}
    assert key['secret'] not in client.get('/admin/service-keys', headers=admin, params={'user_id': user}).text
    assert key['secret'] not in client.get('/admin/audit', headers=admin).text
    assert client.get('/v1/models', headers=auth(identity)).status_code == 401
    assert [item['id'] for item in client.get('/v1/models', headers=service).json()['data']] == ['office:model-a']
    body = {'model': 'office:model-a', 'messages': [{'role': 'user', 'content': '요약해 줘'}], 'max_tokens': 16}
    requests = []
    def handle(request):
        payload = json.loads(request.content)
        assert payload['max_tokens'] == 16 and payload['stream_options']['include_usage'] is True
        requests.append(payload)
        return model_response(report=len(requests) < 3)

    use_model(monkeypatch, handle)
    response = client.post('/v1/chat/completions', headers=service, json=body)
    assert response.status_code == 200, response.text
    value = response.json()
    assert value['choices'][0]['message']['content'] == '연결 확인'
    assert value['usage'] == {'prompt_tokens': 17, 'completion_tokens': 5, 'total_tokens': 22}
    response = client.post('/v1/chat/completions', headers=service,
        json=body | {'stream': True, 'stream_options': {'include_usage': True}})
    assert 'data: [DONE]' in response.text and '"total_tokens": 22' in response.text
    assert client.post('/v1/chat/completions', headers=service, json=body).json()['usage'] is None
    counts = client.get('/admin/usage', headers=admin, params={'q': 'alice'}).json()['items'][0]
    assert (counts['reported_input'], counts['reported_output'], counts['reserved']) == (34, 10, 0)
    assert counts['estimated_input'] > 0 and counts['estimated_output'] > 0

    key_path = '/admin/service-keys/' + key['id']
    budget_path = '/admin/usage/' + user
    assert client.patch(key_path, headers=admin, json={'daily_tokens': 20}).status_code == 200
    assert client.post('/v1/chat/completions', headers=service, json=body).status_code == 429
    assert client.patch(key_path, headers=admin, json={'daily_tokens': 0}).status_code == 200
    assert client.patch(budget_path, headers=admin, json={'daily_tokens': 40}).status_code == 200
    assert client.post('/v1/chat/completions', headers=service, json=body).status_code == 429
    assert len(requests) == 3
    assert client.patch(key_path, headers=admin, json={'revoke': True}).status_code == 200
    assert client.get('/v1/models', headers=service).status_code == 401


def test_parallel_gateway_calls_share_reserved_user_budget(client, account, monkeypatch):
    configure(monkeypatch, max_concurrent=2)
    admin, identity = auth(operator(client, account)), account()
    user = identity['user']['subject']
    assert client.patch('/admin/usage/' + user, headers=admin, json={'daily_tokens': 350}).status_code == 200
    key = client.post('/admin/service-keys', headers=admin, json={
        'user_id': user, 'label': '동시 호출 검증', 'daily_tokens': 0,
    }).json()
    service = {'Authorization': 'Bearer ' + key['secret']}
    body = {'model': 'office:model-a', 'messages': [{'role': 'user', 'content': '간단히 답해 줘'}], 'max_tokens': 64}
    entered, release = threading.Event(), threading.Event()

    async def handle(_request):
        entered.set()
        await asyncio.wait_for(asyncio.to_thread(release.wait, 5), 6)
        return model_response()

    use_model(monkeypatch, handle)
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, '/v1/chat/completions', headers=service, json=body)
        try:
            assert entered.wait(4)
            pending = client.get('/admin/usage', headers=admin, params={'q': 'alice'}).json()['items'][0]
            assert 175 < pending['reserved'] <= 350
            second = client.post('/v1/chat/completions', headers=service, json=body)
            assert second.status_code == 429 and not first.done()
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 200
    assert client.post('/v1/chat/completions', headers=service, json=body).status_code == 200


def test_parallel_workers_overlap_and_parent_synthesizes_partial_results(client, account, monkeypatch):
    monkeypatch.setenv('LLM_USER_CONCURRENCY', '2')
    configure(monkeypatch, context_window=16384, supports_tools=True, max_concurrent=2)
    user = auth(account())
    conversation = client.post('/chat/conversations', headers=user,
        json={'connection_id': 'office', 'model': 'model-a'}).json()['id']
    entered = asyncio.Event()
    completed = set()
    calls = []
    task_names = ['정리', '검토']
    assignments = [{'name': name, 'question': name + '를 수행하세요.'} for name in task_names]

    async def handle(request):
        payload = json.loads(request.content)
        calls.append(payload)
        if payload['stream']:
            assert 'tools' not in payload and payload['max_tokens'] <= 512
            task = json.loads(payload['messages'][-1]['content'])['assignment']
            completed.add(task)
            if len(completed) == 2:
                entered.set()
            await asyncio.wait_for(entered.wait(), 4)
            return model_response('핵심 결과') if task.startswith('정리') else httpx.Response(502)
        assert 'parallel_analyze' in [tool['function']['name'] for tool in payload['tools']]
        if payload['messages'][-1]['role'] == 'tool':
            results = json.loads(payload['messages'][-1]['content'])['tasks']
            assert [(item['name'], item['status']) for item in results] == [('정리', 'complete'), ('검토', 'error')]
            answer = {'content': '핵심 결과를 요약했습니다. 검토 작업은 실패했습니다.'}
        else:
            answer = {'content': '', 'tool_calls': [{'id': 'parallel-1', 'type': 'function', 'function': {
                'name': 'parallel_analyze', 'arguments': json.dumps({'tasks': assignments}, ensure_ascii=False),
            }}]}
        return httpx.Response(200, json={'choices': [{'message': answer}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 25}})

    use_model(monkeypatch, handle)
    response = send(client, user, conversation, '독립된 두 관점에서 분석해 줘', connection_id='office',
        model='model-a', assistant={'automatic': False, 'parallel_agents': True})
    stream = events(response)
    assert stream[-1][0] == 'done', response.text[-2500:]
    assert len(calls) == 4
    assert {value['status'] for kind, value in stream if kind == 'activity' and value['name'] == 'parallel_worker'} == {
        'running', 'complete', 'error',
    }
    assert '검토 작업은 실패' in stream[-1][1]['message']['content']
