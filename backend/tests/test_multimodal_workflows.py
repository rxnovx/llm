"""Exercise automatic and legacy image handling through chat, storage and provider APIs."""
import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from email import policy
from email.parser import BytesParser
import io
import json
import threading
import time
from uuid import uuid4

import httpx
import pytest
from PIL import Image
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.workflows.models import ImageAdmission
from test_chat import auth, create, events, send
from test_image_generation import IMAGE_KEY, IMAGE_MODEL, IMAGE_URL, image_result, png_bytes
from test_knowledge import index, upload


EDIT_URL = 'http://image-server.test:9010/v1/images/edits'
EDIT_KEY = 'test-image-editor-key'
EDIT_MODEL = 'internal-editor'
TEXT_REPLY = '텍스트 답변이 도착했습니다.'


@pytest.fixture
def wire(client, monkeypatch):
    connections = [
        {'id': 'local', 'name': 'Local vision', 'provider': 'vllm',
         'base_url': 'http://chat-server.test/v1', 'model': 'local-chat',
         'supports_vision': True, 'supports_tools': True, 'max_concurrent': 1},
        {'id': 'ollama', 'name': 'Ollama vision', 'provider': 'ollama',
         'base_url': 'http://ollama.test', 'model': 'local-ollama', 'supports_vision': True},
        {'id': 'text', 'name': 'Text only', 'provider': 'vllm',
         'base_url': 'http://chat-server.test/v1', 'model': 'text-only'},
    ]
    settings = {
        'LLM_CONNECTIONS_JSON': json.dumps(connections),
        'LLM_TEST_MODE': 'true',
        'ASSISTANT_AUTOMATIC_ENABLED': 'true',
        'IMAGE_GENERATION_URL': IMAGE_URL,
        'IMAGE_GENERATION_API_KEY': IMAGE_KEY,
        'IMAGE_GENERATION_MODEL': IMAGE_MODEL,
        'IMAGE_EDIT_URL': EDIT_URL,
        'IMAGE_EDIT_API_KEY': EDIT_KEY,
        'IMAGE_EDIT_MODEL': EDIT_MODEL,
        'IMAGE_MAX_CONCURRENT': '1',
        'IMAGE_USER_CONCURRENCY': '1',
        'IMAGE_QUEUE_CAPACITY': '20',
        'IMAGE_QUEUE_WAIT_SECONDS': '15',
        'KNOWLEDGE_OCR_ENABLED': 'false',
    }
    for key, value in settings.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()
    original = httpx.AsyncClient
    requests = []

    def install(handler):
        async def serve(request):
            requests.append(request)
            return await handler(request)

        transport = httpx.MockTransport(serve)
        monkeypatch.setattr(httpx, 'AsyncClient', lambda **options: original(transport=transport, **options))
        return requests

    return install


def reply(*, provider='vllm'):
    if provider == 'ollama':
        return httpx.Response(200, content=json.dumps({
            'message': {'role': 'assistant', 'content': TEXT_REPLY}, 'done': True,
        }).encode() + b'\n')
    chunk = {'choices': [{'delta': {'content': TEXT_REPLY}, 'finish_reason': None}]}
    return httpx.Response(200, content=(
        f'data: {json.dumps(chunk)}\n\n'
        'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
        'data: [DONE]\n\n'
    ).encode())


def structured_reply(plan, *, provider='vllm'):
    message = {'content': json.dumps(plan) if isinstance(plan, dict) else plan}
    return httpx.Response(200, json={'message': message} if provider == 'ollama'
        else {'choices': [{'message': message}]})


def vision_message(payload, provider='vllm'):
    message = payload['messages'][-1]
    if provider == 'ollama':
        encoded, = message['images']
        return message['content'], base64.b64decode(encoded)
    text, image = message['content']
    assert text['type'] == 'text' and image['type'] == 'image_url'
    uri = image['image_url']['url']
    assert uri.startswith('data:image/png;base64,')
    return text['text'], base64.b64decode(uri.split(',', 1)[1])


def upload_image(client, headers, conversation, raw):
    result = client.post('/knowledge/documents', headers=headers,
        data={'conversation_id': conversation, 'image_input': 'true'},
        files={'file': ('사진.png', raw, 'image/png')})
    assert result.status_code == 201, result.text
    assert result.json()['status'] == 'image'
    return result.json()['id']


def multimodal(client, headers, conversation, mode, question, *, connection_id='local', **source):
    return send(client, headers, conversation, question, connection_id=connection_id,
        assistant={'image_mode': mode, **source})


def artifact_from(response):
    assert response.status_code == 200, response.text
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    artifact, = [data for name, data in stream if name == 'artifact']
    assert stream[-1][1]['message']['artifacts'] == [artifact]
    return artifact


def multipart_parts(request):
    content_type = request.headers['content-type']
    assert content_type.startswith('multipart/form-data;')
    message = BytesParser(policy=policy.default).parsebytes(
        f'Content-Type: {content_type}\r\nMIME-Version: 1.0\r\n\r\n'.encode() + request.content)
    return {part.get_param('name', header='content-disposition'): part for part in message.iter_parts()}


@pytest.mark.parametrize('failure', ['bad_json', 'timeout'])
def test_image_router_failure_preserves_text_chat(client, account, wire, failure):
    async def handle(request):
        assert request.url.host == 'chat-server.test'
        payload = json.loads(request.content)
        if payload['stream']:
            assert payload['messages'][-1]['content'] == '오늘의 업무를 정리해 줘'
            return reply()
        if failure == 'timeout':
            raise httpx.ReadTimeout('upstream diagnostic must stay private', request=request)
        return httpx.Response(200, json={'choices': [{'message': {'content': 'invalid JSON'}}]})

    requests = wire(handle)
    headers = auth(account())
    response = send(client, headers, create(client, headers), '오늘의 업무를 정리해 줘',
        connection_id='local')
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    assert stream[-1][1]['message']['content'] == TEXT_REPLY
    assert [json.loads(request.content)['stream'] for request in requests] == [False, True]
    assert not any(name == 'artifact' for name, _ in stream)
    assert 'upstream diagnostic must stay private' not in response.text


def test_explicit_generation_bypasses_classification_and_exposes_image_queue(client, account, wire, png_bytes):
    async def handle(request):
        assert str(request.url) == IMAGE_URL
        assert request.headers['authorization'] == f'Bearer {IMAGE_KEY}'
        assert json.loads(request.content) == {
            'prompt': '초록 산을 그려 줘', 'n': 1, 'response_format': 'b64_json', 'model': IMAGE_MODEL,
        }
        return image_result(png_bytes)

    requests = wire(handle)
    headers = auth(account())
    config = client.get('/chat/config', headers=headers).json()
    assert config['images'] == {'generate': True, 'edit': True}
    assert next(item for item in config['connections'] if item['id'] == 'local')['supports_vision']
    conversation = create(client, headers)
    response = multimodal(client, headers, conversation, 'generate', '초록 산을 그려 줘', automatic=False)
    file = artifact_from(response)
    assert any(name == 'queue' and data.get('kind') == 'image' and data['status'] == 'running'
        for name, data in events(response))
    assert len(requests) == 1
    assert IMAGE_KEY not in response.text and IMAGE_URL not in response.text
    assert client.get(f"/workflows/artifacts/{file['id']}/download", headers=headers).content == png_bytes


@pytest.mark.parametrize('source_kind', ['latest', 'explicit', 'attachment', 'fork'])
def test_image_edit_sends_a_scoped_reference_and_preserves_original(client, account, wire, png_bytes, source_kind):
    with Image.new('RGB', (16, 8), 'green') as image, io.BytesIO() as output:
        image.save(output, format='PNG')
        edited_bytes = output.getvalue()

    async def handle(request):
        if str(request.url) == IMAGE_URL:
            return image_result(png_bytes)
        assert str(request.url) == EDIT_URL
        assert request.headers['authorization'] == f'Bearer {EDIT_KEY}'
        parts = multipart_parts(request)
        assert parts['image'].get_content_type() == 'image/png'
        assert parts['image'].get_payload(decode=True) == png_bytes
        assert parts['prompt'].get_payload(decode=True).decode() == '하늘만 연두색으로 바꿔 줘'
        assert parts['model'].get_payload(decode=True) == EDIT_MODEL.encode()
        assert parts['n'].get_payload(decode=True) == b'1'
        assert parts['response_format'].get_payload(decode=True) == b'b64_json'
        return image_result(edited_bytes)

    requests = wire(handle)
    headers = auth(account())
    conversation = create(client, headers)
    original = None
    if source_kind == 'attachment':
        source = {'document_ids': [upload_image(client, headers, conversation, png_bytes)]}
    else:
        original = artifact_from(multimodal(client, headers, conversation, 'generate', '산을 그려 줘'))
        source = {'image_artifact_id': original['id']} if source_kind in {'explicit', 'fork'} else {}
        if source_kind == 'fork':
            original_path = f'/chat/conversations/{conversation}'
            original_message = client.get(original_path, headers=headers).json()['messages'][-1]['id']
            fork = client.post(f'/workflows/conversations/{conversation}/fork', headers=headers,
                json={'message_id': original_message})
            assert fork.status_code == 201, fork.text
            conversation = fork.json()['conversation']['id']
            assert client.delete(original_path, headers=headers).status_code == 204
    response = multimodal(client, headers, conversation, 'edit', '하늘만 연두색으로 바꿔 줘', **source)
    edited = artifact_from(response)
    assert client.get(f"/workflows/artifacts/{edited['id']}/download", headers=headers).content == edited_bytes
    assert [str(request.url) for request in requests] == (
        [EDIT_URL] if source_kind == 'attachment' else [IMAGE_URL, EDIT_URL])
    assert EDIT_KEY not in response.text
    if original:
        assert edited['id'] != original['id']
        assert client.get(f"/workflows/artifacts/{original['id']}/download", headers=headers).content == png_bytes


def test_automatic_edit_uses_latest_image_in_the_conversation(client, account, wire, png_bytes):
    async def handle(request):
        if str(request.url) == IMAGE_URL:
            return image_result(png_bytes)
        if request.url.host == 'chat-server.test':
            payload = json.loads(request.content)
            decision = json.loads(payload['messages'][-1]['content'])
            assert not payload['stream'] and decision['available']['image_edit']
            plan = {'route': 'image_edit', 'image_prompt': '해 질 무렵의 따뜻한 색감으로 바꾼다.'}
            return httpx.Response(200, json={'choices': [{'message': {'content': json.dumps(plan)}}]})
        assert str(request.url) == EDIT_URL
        parts = multipart_parts(request)
        assert parts['image'].get_payload(decode=True) == png_bytes
        assert parts['prompt'].get_payload(decode=True).decode() == '해 질 무렵의 따뜻한 색감으로 바꾼다.'
        return image_result(png_bytes)

    requests = wire(handle)
    headers = auth(account())
    conversation = create(client, headers)
    original = artifact_from(multimodal(client, headers, conversation, 'generate', '처음 그림을 만들어 줘'))
    response = send(client, headers, conversation, '방금 그림을 따뜻한 노을 느낌으로 바꿔 줘',
        connection_id='local')
    assert artifact_from(response)['id'] != original['id']
    assert [str(request.url) for request in requests] == [
        IMAGE_URL, 'http://chat-server.test/v1/chat/completions', EDIT_URL,
    ]


@pytest.mark.parametrize('provider,connection_id', [('vllm', 'local'), ('ollama', 'ollama')])
def test_llm_routes_the_same_attachment_by_request(client, account, wire, png_bytes, provider, connection_id):
    describe = '이 사진에 어떤 색이 많이 나와?'
    edit = '이 사진의 하늘만 밝은 파란색으로 바꿔 줘'
    translate = '“파란 하늘을 그려 줘”를 영어로 번역해 줘'
    headers = auth(account())
    conversation = create(client, headers)
    attachment = upload_image(client, headers, conversation, png_bytes)
    encoded = base64.b64encode(png_bytes)

    async def handle(request):
        if str(request.url) == EDIT_URL:
            parts = multipart_parts(request)
            assert parts['image'].get_payload(decode=True) == png_bytes
            assert parts['prompt'].get_payload(decode=True).decode() == edit
            return image_result(png_bytes)
        assert request.url.path == ('/api/chat' if provider == 'ollama' else '/v1/chat/completions')
        payload = json.loads(request.content)
        if payload['stream']:
            if payload['messages'][-1]['content'] == translate:
                assert encoded not in request.content
            else:
                assert vision_message(payload, provider) == (describe, png_bytes)
            return reply(provider=provider)
        assert encoded not in request.content and attachment.encode() not in request.content
        decision = json.loads(payload['messages'][-1]['content'])
        available = decision['available']
        assert available['image_vision'] and available['image_edit']
        if decision['question'] == describe or (decision['question'] == edit and provider == 'ollama'):
            assert available['image_source'] == 'current'
        else:
            assert available['image_source'] == 'recent'
        plans = {describe: {'route': 'vision'}, edit: {'route': 'image_edit', 'image_prompt': edit},
            translate: {'route': 'direct'}}
        return structured_reply(plans[decision['question']], provider=provider)

    requests = wire(handle)
    options = {'connection_id': connection_id, 'assistant': {'image_document_ids': [attachment]}}
    analyzed = send(client, headers, conversation, describe, **options)
    stream = events(analyzed)
    assert stream[-1][0] == 'done', analyzed.text
    assert stream[-1][1]['message']['content'] == TEXT_REPLY
    assert stream[-1][1]['message']['assistant']['image_document_ids'] == [attachment]
    assert stream[-1][1]['message']['routing']['route'] == 'vision'
    assert not any(kind == 'artifact' for kind, _ in stream)
    edit_options = options if provider == 'ollama' else {'connection_id': connection_id}
    edited = send(client, headers, conversation, edit, **edit_options)
    assert artifact_from(edited)['format'] == 'png'
    assert len(requests) == 4 and str(requests[-1].url) == EDIT_URL
    assert encoded.decode() not in analyzed.text and encoded.decode() not in edited.text
    ordinary = send(client, headers, conversation, translate, connection_id=connection_id)
    stream = events(ordinary)
    assert stream[-1][0] == 'done', ordinary.text
    assert stream[-1][1]['message']['content'] == TEXT_REPLY
    assert len(requests) == 6 and not any(kind == 'artifact' for kind, _ in stream)


def test_automatic_vision_uses_explicit_document_evidence(client, account, wire, png_bytes, monkeypatch):
    monkeypatch.setenv('ASSISTANT_GROUNDING_REVIEW', 'false')
    get_settings.cache_clear()
    question = '사진과 첨부 문서를 보고 점검 주기를 알려 줘'
    evidence = '점검 주기는 매주 금요일입니다.'

    async def handle(request):
        assert request.url.host == 'chat-server.test'
        payload = json.loads(request.content)
        if not payload['stream']:
            return structured_reply({'route': 'vision', 'query': '점검 주기'})
        text, pixels = vision_message(payload)
        assert pixels == png_bytes and question in text and evidence in text
        return reply()

    requests = wire(handle)
    headers = auth(account())
    conversation = create(client, headers)
    attachment = upload_image(client, headers, conversation, png_bytes)
    document = upload(client, headers, conversation=conversation, text=evidence)['id']
    index(client)
    response = send(client, headers, conversation, question, connection_id='local',
        assistant={'image_document_ids': [attachment], 'document_ids': [document]})
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    source, = stream[-1][1]['message']['sources']
    assert source['app'] == 'knowledge' and source['document_id'] == document
    assert len(requests) == 2


def test_automatic_router_failure_keeps_the_current_image(client, account, wire, png_bytes):
    question = '사진에 무엇이 보여?'

    async def handle(request):
        assert request.url.host == 'chat-server.test'
        payload = json.loads(request.content)
        if not payload['stream']:
            assert base64.b64encode(png_bytes) not in request.content
            return structured_reply('invalid JSON')
        assert vision_message(payload) == (question, png_bytes)
        return reply()

    requests = wire(handle)
    headers = auth(account())
    conversation = create(client, headers)
    attachment = upload_image(client, headers, conversation, png_bytes)
    response = send(client, headers, conversation, question, connection_id='local',
        assistant={'image_document_ids': [attachment], 'automatic': False})
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    assert stream[-1][1]['message']['content'] == TEXT_REPLY
    assert stream[-1][1]['message']['routing']['fallback'] == 'route_decision'
    assert not any(kind == 'artifact' for kind, _ in stream)
    assert len(requests) == 2


def test_automatic_route_cannot_send_images_to_text_only_model(client, account, wire, png_bytes):
    async def handle(request):
        assert request.url.host == 'chat-server.test'
        payload = json.loads(request.content)
        assert not payload['stream'] and base64.b64encode(png_bytes) not in request.content
        available = json.loads(payload['messages'][-1]['content'])['available']
        assert not available['image_vision'] and available['image_edit']
        return structured_reply({'route': 'vision'})

    requests = wire(handle)
    headers = auth(account())
    conversation = create(client, headers)
    attachment = upload_image(client, headers, conversation, png_bytes)
    response = send(client, headers, conversation, '사진을 설명해 줘', connection_id='text',
        assistant={'image_document_ids': [attachment]})
    assert response.status_code == 200, response.text
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    assert '이미지 입력을 지원하도록 설정된 모델' in stream[-1][1]['message']['content']
    assert len(requests) == 1 and not any(kind == 'artifact' for kind, _ in stream)


def test_edited_question_inherits_raw_image_in_fork(client, account, wire, png_bytes):
    describe = '이 사진을 설명해 줘'
    revised = '이 사진에 보이는 색을 설명해 줘'

    async def handle(request):
        payload = json.loads(request.content)
        if not payload['stream']:
            return structured_reply({'route': 'vision'})
        text, pixels = vision_message(payload)
        assert pixels == png_bytes and text in {describe, revised}
        return reply()

    requests = wire(handle)
    headers = auth(account())
    conversation = create(client, headers)
    document_id = upload_image(client, headers, conversation, png_bytes)
    response = send(client, headers, conversation, describe, connection_id='local',
        assistant={'image_document_ids': [document_id]})
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    fork = client.post(f'/workflows/conversations/{conversation}/fork', headers=headers,
        json={'message_id': stream[0][1]['user_message']['id'], 'content': revised})
    assert fork.status_code == 201, fork.text
    branch = fork.json()
    assert branch['assistant']['image_document_ids'] == [document_id]
    assert branch['excluded_attachments'] == 0
    assert client.delete(f'/chat/conversations/{conversation}', headers=headers).status_code == 204
    response = send(client, headers, branch['conversation']['id'], branch['draft'], connection_id='local',
        assistant=branch['assistant'])
    assert events(response)[-1][0] == 'done', response.text
    assert len(requests) == 4


@pytest.mark.parametrize('provider,connection_id', [('vllm', 'local'), ('ollama', 'ollama')])
def test_vision_sends_original_pixels_without_ocr_and_keeps_history_text(client, account, wire, png_bytes, provider, connection_id):
    async def handle(request):
        assert request.url.path == ('/api/chat' if provider == 'ollama' else '/v1/chat/completions')
        assert json.loads(request.content)['stream']
        return reply(provider=provider)

    requests = wire(handle)
    headers = auth(account())
    conversation = create(client, headers)
    document_id = upload_image(client, headers, conversation, png_bytes)
    question = '사진에서 보이는 것을 설명해 줘'
    response = multimodal(client, headers, conversation, 'vision', question,
        connection_id=connection_id, document_ids=[document_id])
    assert events(response)[-1][0] == 'done', response.text
    assert vision_message(json.loads(requests[0].content), provider) == (question, png_bytes)
    encoded = base64.b64encode(png_bytes).decode()
    assert encoded not in response.text
    followup = send(client, headers, conversation, '텍스트 후속 질문', connection_id=connection_id,
        assistant={'automatic': False})
    assert events(followup)[-1][0] == 'done', followup.text
    history = json.loads(requests[1].content)['messages']
    assert len(requests) == 2 and all(isinstance(item['content'], str) and 'images' not in item for item in history)
    assert history[-3]['content'] == question
    assert encoded not in client.get(f'/chat/conversations/{conversation}', headers=headers).text


def assert_rejected(response):
    if response.status_code == 200:
        stream = events(response)
        assert stream[-1][0] == 'error', response.text
        assert not any(name == 'artifact' for name, _ in stream)
    else:
        assert response.status_code in {404, 409, 422}, response.text


@pytest.mark.parametrize('mode', ['vision', 'auto'])
def test_vision_upload_cancellation_respects_turn_acceptance(client, account, wire, png_bytes, mode):
    async def handle(request):
        if not json.loads(request.content)['stream']:
            assert mode == 'auto'
            return structured_reply({'route': 'vision'})
        return reply()

    requests = wire(handle)
    headers = auth(account())
    conversation = create(client, headers)

    def attach():
        attempt = client.post('/workflows/uploads', headers=headers,
            json={'filename': '사진.png', 'conversation_id': conversation})
        assert attempt.status_code == 201, attempt.text
        attempt_id = attempt.json()['id']
        uploaded = client.post('/knowledge/documents', headers=headers,
            data={'conversation_id': conversation, 'upload_id': attempt_id, 'image_input': 'true'},
            files={'file': ('사진.png', png_bytes, 'image/png')})
        assert uploaded.status_code == 201, uploaded.text
        return attempt_id, uploaded.json()['id']

    def describe(document_id):
        options = {'image_document_ids': [document_id]} if mode == 'auto' else {
            'image_mode': 'vision', 'document_ids': [document_id],
        }
        return send(client, headers, conversation, '사진을 설명해 줘', connection_id='local', assistant=options)

    cancelled, lost_id = attach()
    assert client.post(f'/workflows/uploads/{cancelled}/cancel', headers=headers).status_code == 200
    lost = describe(lost_id)
    assert lost.status_code == 404, lost.text
    assert client.get(f'/chat/conversations/{conversation}', headers=headers).json()['messages'] == []
    assert requests == []

    attached, kept_id = attach()
    accepted = describe(kept_id)
    assert events(accepted)[-1][0] == 'done', accepted.text
    assert len(requests) == (2 if mode == 'auto' else 1)
    assert client.post(f'/workflows/uploads/{attached}/cancel', headers=headers).status_code == 409
    assert kept_id in [doc['id'] for doc in client.get('/knowledge/documents', headers=headers,
        params={'conversation_id': conversation}).json()['items']]


def test_raw_image_upload_checks_pixels_and_is_not_rag_input(client, account, wire, png_bytes):
    headers = auth(account())
    conversation = create(client, headers)
    invalid = client.post('/knowledge/documents', headers=headers,
        data={'conversation_id': conversation, 'image_input': 'true'},
        files={'file': ('사진.png', b'<svg></svg>', 'image/png')})
    assert invalid.status_code == 422, invalid.text
    document_id = upload_image(client, headers, conversation, png_bytes)
    assert client.post(f'/knowledge/documents/{document_id}/reindex', headers=headers).status_code == 422
    search = client.post('/knowledge/search', headers=headers,
        json={'conversation_id': conversation, 'query': '사진에 적힌 내용', 'document_ids': [document_id]})
    assert search.status_code == 422, search.text


def test_image_sources_cannot_escape_conversation_or_model_capabilities(client, account, wire, png_bytes):
    async def handle(request):
        assert str(request.url) == IMAGE_URL
        return image_result(png_bytes)

    requests = wire(handle)
    owner, outsider = auth(account()), auth(account('bob'))
    source, other, foreign = create(client, owner), create(client, owner), create(client, outsider)
    attachment = upload_image(client, owner, source, png_bytes)
    original = artifact_from(multimodal(client, owner, source, 'generate', '원본을 그려 줘'))
    for headers, conversation in [(owner, other), (outsider, foreign)]:
        assert_rejected(multimodal(client, headers, conversation, 'vision', '이 사진을 분석해 줘',
            document_ids=[attachment]))
        assert_rejected(multimodal(client, headers, conversation, 'edit', '이 사진을 편집해 줘',
            image_artifact_id=original['id']))
        for source in ({'image_document_ids': [attachment]}, {'image_artifact_id': original['id']}):
            response = send(client, headers, conversation, '이 사진을 설명해 줘',
                connection_id='local', assistant=source)
            assert response.status_code == 404, response.text
    assert_rejected(multimodal(client, owner, source, 'vision', '이 사진을 분석해 줘',
        connection_id='text', document_ids=[attachment]))
    assert len(requests) == 1


def test_queued_edit_restores_position_and_cancels_without_holding_chat_slot(client, account, wire, png_bytes):
    started, release = threading.Event(), threading.Event()

    async def handle(request):
        if request.url.host == 'chat-server.test':
            assert json.loads(request.content)['stream']
            return reply()
        assert str(request.url) == IMAGE_URL, 'A cancelled queue entry must never reach the editor'
        started.set()
        assert await asyncio.to_thread(release.wait, 12), 'Test did not release the active image request'
        return image_result(png_bytes)

    requests = wire(handle)
    owner, other = auth(account()), auth(account('bob'))
    first, second, text = create(client, owner), create(client, other), create(client, owner)
    attachment = upload_image(client, other, second, png_bytes)
    request_id = str(uuid4())
    second_path = f'/chat/conversations/{second}'
    with ThreadPoolExecutor(max_workers=2) as executor:
        active = executor.submit(multimodal, client, owner, first, 'generate', '첫 번째 이미지')
        try:
            assert started.wait(4), 'Image provider did not receive the first request'
            waiting = executor.submit(client.post, second_path + '/messages', headers=other, json={
                'content': '두 번째 요청은 편집', 'request_id': request_id, 'connection_id': 'local',
                'assistant': {'image_mode': 'edit', 'document_ids': [attachment]},
            })
            deadline = time.monotonic() + 4
            while time.monotonic() < deadline:
                snapshot = client.get(second_path, headers=other).json()['generation'] or {}
                if snapshot.get('kind') == 'image' and snapshot['status'] == 'queued':
                    break
                time.sleep(.02)
            else:
                pytest.fail('The waiting edit was not exposed as an image queue entry')
            assert snapshot == {'kind': 'image', 'status': 'queued', 'request_id': request_id, 'position': 1}
            normal = send(client, owner, text, '텍스트 질문', connection_id='local',
                assistant={'automatic': False})
            assert events(normal)[-1][0] == 'done', normal.text
            assert not active.done()
            cancelled = client.post(second_path + '/cancel', headers=other, json={'request_id': request_id})
            assert cancelled.status_code == 204
            stream = events(waiting.result(timeout=4))
            assert stream[-1][0] == 'cancelled'
            assert any(name == 'queue' and data.get('kind') == 'image' and data['status'] == 'queued'
                for name, data in stream)
            assert client.get(second_path, headers=other).json()['generation'] is None
        finally:
            release.set()
        assert artifact_from(active.result(timeout=4))['format'] == 'png'
    assert [str(request.url) for request in requests] == [IMAGE_URL, 'http://chat-server.test/v1/chat/completions']
    with client.db_factory() as db:
        assert db.scalar(select(ImageAdmission.id)) is None
