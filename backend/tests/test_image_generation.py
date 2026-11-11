"""Exercise chat routing and private image artifacts with local provider doubles."""
import asyncio
import base64
import io
import json

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.chat.models import Admission, Conversation
from app.modules.workflows.models import Artifact
from test_chat import auth, create, events, send


IMAGE_URL = 'http://image-server.test:9010/v1/images/generations'
IMAGE_KEY = 'test-image-provider-key'
IMAGE_MODEL = 'internal-illustrator'
QUESTION = '등대가 있는 저녁 바다를 한 장으로 보고 싶어'
PROMPT = '고요한 저녁 바다 위로 낮은 구름이 깔리고 해변에 흰 등대가 보인다.'
IMAGE_PLAN = {'route': 'image', 'image_prompt': PROMPT}


@pytest.fixture
def png_bytes():
    with Image.new('RGB', (16, 8), 'navy') as image, io.BytesIO() as output:
        image.save(output, format='PNG')
        return output.getvalue()


@pytest.fixture
def upstream(client, monkeypatch):
    monkeypatch.setenv('LLM_CONNECTIONS_JSON', json.dumps([{
        'id': 'local', 'name': 'Local', 'provider': 'vllm',
        'base_url': 'http://chat-server.test/v1', 'model': 'local-chat',
    }]))
    monkeypatch.setenv('LLM_TEST_MODE', 'true')
    monkeypatch.setenv('ASSISTANT_AUTOMATIC_ENABLED', 'true')
    monkeypatch.setenv('IMAGE_GENERATION_API_KEY', IMAGE_KEY)
    original_client = httpx.AsyncClient

    def install(image_response=None, *, plan=IMAGE_PLAN, url=IMAGE_URL, model=IMAGE_MODEL):
        monkeypatch.setenv('IMAGE_GENERATION_URL', url)
        monkeypatch.setenv('IMAGE_GENERATION_MODEL', model)
        get_settings.cache_clear()
        requests = []

        def serve(request):
            requests.append(request)
            if request.url.host == 'chat-server.test':
                assert request.url.path == '/v1/chat/completions'
                payload = json.loads(request.content)
                if payload['stream']:
                    return httpx.Response(200, content=(
                        'data: {"choices":[{"delta":{"content":"일반 답변"}}]}\n\n'
                        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                        'data: [DONE]\n\n'
                    ).encode())
                return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(plan)}}]})
            assert str(request.url) == IMAGE_URL
            assert image_response is not None, 'Image provider called without an image request'
            return image_response

        transport = httpx.MockTransport(serve)
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **options: original_client(transport=transport, **options))
        return requests

    return install


def image_result(raw):
    return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(raw).decode()}]})


def saved(client, headers, conversation):
    response = client.get(f'/chat/conversations/{conversation}', headers=headers)
    assert response.status_code == 200, response.text
    return response.json()['messages'][-1]


@pytest.mark.parametrize('model', [IMAGE_MODEL, ''])
def test_chat_routes_image_request_to_private_download(client, account, upstream, png_bytes, model):
    requests = upstream(image_result(png_bytes), model=model)
    owner, other = auth(account()), auth(account('bob'))
    conversation = create(client, owner)

    response = send(client, owner, conversation, QUESTION, connection_id='local')
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    assert [data['status'] for kind, data in stream if kind == 'activity' and data['name'] == 'generate_image'] == [
        'running', 'complete',
    ]
    file, = [data for kind, data in stream if kind == 'artifact']
    assert file['format'] == 'png' and file['size'] == len(png_bytes)
    assert saved(client, owner, conversation)['artifacts'] == [file]
    assert stream[-1][1]['message']['artifacts'] == [file]

    image_requests = [request for request in requests if request.url.host == 'image-server.test']
    image_request, = image_requests
    assert image_request.method == 'POST' and str(image_request.url) == IMAGE_URL
    assert image_request.headers['authorization'] == f'Bearer {IMAGE_KEY}'
    expected = {'prompt': PROMPT, 'n': 1, 'response_format': 'b64_json'}
    if model:
        expected['model'] = model
    assert json.loads(image_request.content) == expected
    decision_request = json.loads(requests[0].content)
    assert json.loads(decision_request['messages'][-1]['content'])['question'] == QUESTION
    assert 'image' in decision_request['response_format']['json_schema']['schema']['properties']['route']['enum']
    assert IMAGE_KEY.encode() not in requests[0].content and IMAGE_KEY not in response.text
    assert IMAGE_URL.encode() not in requests[0].content and IMAGE_URL not in response.text

    download_path = f"/workflows/artifacts/{file['id']}/download"
    download = client.get(download_path, headers=owner)
    assert download.status_code == 200 and download.content == png_bytes
    assert download.headers['content-type'] == 'image/png'
    assert download.headers['cache-control'] == 'no-store'
    assert client.get(download_path, headers=other).status_code == 404
    assert client.get(download_path).status_code == 401


@pytest.mark.parametrize('mode', ['direct', 'unconfigured', 'opt_out', 'demo'])
def test_image_generation_preserves_ordinary_chat_and_respects_opt_out(client, account, upstream, mode):
    requests = upstream(plan={'route': 'direct'}, url='' if mode == 'unconfigured' else IMAGE_URL)
    headers = auth(account())
    conversation = create(client, headers)
    options = {'connection_id': 'demo' if mode == 'demo' else 'local'}
    if mode == 'opt_out':
        options['assistant'] = {'automatic': False}

    response = send(client, headers, conversation, '저녁 바다를 그려줘라는 문장을 영어로 번역해 줘', **options)
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    assert not any(kind == 'artifact' for kind, _ in stream)
    assert all(request.url.host == 'chat-server.test' for request in requests)
    if mode == 'demo':
        assert requests == [] and saved(client, headers, conversation)['is_test']
    else:
        assert [json.loads(request.content)['stream'] for request in requests] == (
            [False, True] if mode == 'direct' else [True]
        )
        assert saved(client, headers, conversation)['content'] == '일반 답변'


def test_model_cannot_enable_unconfigured_image_generation(client, account, upstream):
    requests = upstream(url='')
    headers = auth(account())
    conversation = create(client, headers)

    response = send(client, headers, conversation, QUESTION, connection_id='local', assistant={'rag_mode': 'always'})
    stream = events(response)
    assert stream[-1][0] == 'error', response.text
    assert not any(kind == 'artifact' for kind, _ in stream)
    assert [request.url.host for request in requests] == ['chat-server.test']


@pytest.mark.parametrize('status,payload', [
    (503, {'error': 'private-upstream-error-details'}),
    (200, {'data': [{'b64_json': 'invalid base64'}]}),
    (200, {'data': [{'b64_json': base64.b64encode(b'<svg></svg>').decode()}]}),
    (200, {'data': [{'url': 'http://do-not-fetch.test/generated.png'}]}),
])
def test_image_provider_failure_does_not_publish_artifact(client, account, upstream, status, payload):
    requests = upstream(httpx.Response(status, json=payload))
    headers = auth(account())
    conversation = create(client, headers)

    response = send(client, headers, conversation, QUESTION, connection_id='local')
    stream = events(response)
    assert stream[-1][0] == 'error', response.text
    assert any(kind == 'activity' and data['name'] == 'generate_image' and data['status'] == 'error'
               for kind, data in stream)
    assert not any(kind == 'artifact' for kind, _ in stream)
    assert 'private-upstream-error-details' not in response.text
    assert [request.url.host for request in requests] == ['chat-server.test', 'image-server.test']
    assert saved(client, headers, conversation)['artifacts'] == []
    with client.db_factory() as db:
        assert db.scalar(select(Artifact)) is None


def test_failed_image_regeneration_keeps_previous_image(client, account, upstream, png_bytes):
    upstream(image_result(png_bytes))
    headers = auth(account())
    conversation = create(client, headers)
    first = send(client, headers, conversation, QUESTION, connection_id='local')
    assert events(first)[-1][0] == 'done', first.text
    previous = saved(client, headers, conversation)

    upstream(httpx.Response(503))
    retry = send(client, headers, conversation, QUESTION, connection_id='local', retry=True)
    assert events(retry)[-1][0] == 'error', retry.text
    current = saved(client, headers, conversation)
    assert current['status'] == 'complete' and current['recovery']['preserved_previous']
    assert current['artifacts'] == previous['artifacts']
    assert current['versions'] == previous['versions']
    with client.db_factory() as db:
        assert list(db.scalars(select(Artifact.id))) == [previous['artifacts'][0]['id']]


def test_last_branch_removal_deletes_the_image(client, account, upstream, png_bytes):
    requests = upstream(image_result(png_bytes), plan={'route': 'image'})
    headers = auth(account())
    conversation = create(client, headers)
    response = send(client, headers, conversation, QUESTION, connection_id='local')
    assert events(response)[-1][0] == 'done', response.text
    assert json.loads(requests[-1].content)['prompt'] == QUESTION
    answer = saved(client, headers, conversation)
    file, = answer['artifacts']
    fork = client.post(f'/workflows/conversations/{conversation}/fork', headers=headers,
        json={'message_id': answer['id']})
    assert fork.status_code == 201, fork.text
    branch = fork.json()['conversation']['id']
    assert client.delete(f'/chat/conversations/{conversation}', headers=headers).status_code == 204
    download = f"/workflows/artifacts/{file['id']}/download"
    assert saved(client, headers, branch)['artifacts'] == [file]
    assert client.get(download, headers=headers).content == png_bytes
    assert client.delete(f'/chat/conversations/{branch}', headers=headers).status_code == 204
    assert client.get(download, headers=headers).status_code == 404


def test_cancel_while_image_provider_is_running_discards_result(client, account, upstream, png_bytes):
    headers = auth(account())
    conversation = create(client, headers)
    stopped = False

    class InterruptedImage(httpx.AsyncByteStream):
        async def __aiter__(self):
            nonlocal stopped
            with client.db_factory() as db:
                assert db.scalar(select(Admission)).state == 'tool_waiting'
                db.get(Conversation, conversation).cancel_requested = True
                db.commit()
            try:
                await asyncio.sleep(2)
                yield image_result(png_bytes).content
            except asyncio.CancelledError:
                stopped = True
                raise

    upstream(httpx.Response(200, stream=InterruptedImage()))
    response = send(client, headers, conversation, QUESTION, connection_id='local')
    stream = events(response)
    assert stream[-1][0] == 'cancelled', response.text
    assert stopped and not any(kind == 'artifact' for kind, _ in stream)
    with client.db_factory() as db:
        assert db.scalar(select(Admission)).state == 'cancelled'
        assert db.scalar(select(Artifact)) is None
