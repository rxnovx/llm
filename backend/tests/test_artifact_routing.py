"""Actual downloads survive missing native calls and failed image classification."""
import base64
from io import BytesIO
import json
from zipfile import ZipFile

import httpx
from PIL import Image
import pytest

from app.core.config import get_settings
from app.modules.assistant import file_generation
from app.modules.chat import providers
from test_chat import auth, create, events, send


ARCHIVE = {'name': '프로젝트', 'entries': [
    {'path': 'README.txt', 'content': '안녕하세요'},
    {'path': 'config/settings.json', 'content': '{"enabled": true}'},
]}
ZIP_QUESTION = 'README.txt와 config/settings.json을 넣은 ZIP 파일을 생성해줘. README 내용은 안녕하세요, 설정은 enabled true야.'


@pytest.fixture
def upstream(client, monkeypatch):
    def install(handler, *, tools=False, image_url='', context_window=8192, max_output_tokens=2048):
        monkeypatch.setenv('LLM_CONNECTIONS_JSON', json.dumps([{
            'id': 'local', 'name': 'Local', 'provider': 'vllm',
            'base_url': 'http://chat.test/v1', 'model': 'local-chat', 'supports_tools': tools,
            'context_window': context_window, 'max_output_tokens': max_output_tokens,
        }]))
        monkeypatch.setenv('ASSISTANT_AUTOMATIC_ENABLED', 'true')
        monkeypatch.setenv('IMAGE_GENERATION_URL', image_url)
        monkeypatch.setenv('IMAGE_EDIT_URL', '')
        get_settings.cache_clear()
        requests = []
        original = httpx.AsyncClient

        def serve(request):
            requests.append(request)
            return handler(request)

        monkeypatch.setattr(httpx, 'AsyncClient', lambda **options: original(
            transport=httpx.MockTransport(serve), **options))
        return requests
    return install


def message(content='', calls=None):
    return httpx.Response(200, json={'choices': [{'message': {'content': content, 'tool_calls': calls or []}}]})


def schema_kind(payload):
    properties = payload.get('response_format', {}).get('json_schema', {}).get('schema', {}).get('properties', {})
    return 'file' if 'decision' in properties else 'route' if 'route' in properties else ''


def file_decision(value):
    return message(json.dumps({'decision': value}, ensure_ascii=False))


def assert_zip(client, headers, response):
    stream = events(response)
    assert stream[-1][0] == 'done', response.text[-3000:]
    file, = [payload for kind, payload in stream if kind == 'artifact']
    assert file['format'] == 'zip'
    download = client.get(f"/workflows/artifacts/{file['id']}/download", headers=headers)
    assert download.status_code == 200
    with ZipFile(BytesIO(download.content)) as archive:
        assert archive.namelist() == ['README.txt', 'config/settings.json']
        assert archive.read('README.txt').decode() == '안녕하세요'
        assert json.loads(archive.read('config/settings.json')) == {'enabled': True}
    assert stream[-1][1]['message']['artifacts'] == [file]
    return stream[-1][1]['message']


def test_zip_without_native_tool_support_is_built_from_validated_schema(client, account, upstream):
    generated = False

    def serve(request):
        nonlocal generated
        payload = json.loads(request.content)
        assert not payload['stream'] and 'tools' not in payload
        if schema_kind(payload) == 'route':
            return message('{"route":"direct"}')
        assert schema_kind(payload) == 'file'
        assert payload['max_tokens'] == 2048
        assert all(item['role'] != 'tool' and 'tool_calls' not in item for item in payload['messages'])
        if not generated:
            generated = True
            return file_decision({'tool': 'create_archive', 'arguments': ARCHIVE})
        assert 'Host file operation result' in payload['messages'][-1]['content']
        return file_decision({'tool': 'answer', 'content': 'ZIP 파일을 만들었습니다.'})

    requests = upstream(serve)
    headers = auth(account())
    saved = assert_zip(client, headers, send(client, headers, create(client, headers), ZIP_QUESTION, connection_id='local'))
    assert saved['content'] == 'ZIP 파일을 만들었습니다.'
    assert len(requests) == 3


def test_zip_with_unspecified_contents_asks_instead_of_denying_capability(client, account, upstream):
    def serve(request):
        payload = json.loads(request.content)
        if schema_kind(payload) == 'route':
            return message('{"route":"direct"}')
        assert schema_kind(payload) == 'file'
        return file_decision({'tool': 'clarify', 'content': 'ZIP에 넣을 파일 이름과 내용을 알려 주세요.'})

    upstream(serve)
    headers = auth(account())
    response = send(client, headers, create(client, headers), 'ZIP 파일 생성해줘', connection_id='local')
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    assert stream[-1][1]['message']['content'] == 'ZIP에 넣을 파일 이름과 내용을 알려 주세요.'
    assert not any(kind == 'artifact' for kind, _ in stream)


@pytest.mark.parametrize('file_format', ['txt', 'docx'])
def test_documents_without_native_tools_finish_with_default_context_budget(client, account, upstream, file_format):
    generated = False

    def serve(request):
        nonlocal generated
        payload = json.loads(request.content)
        if schema_kind(payload) == 'route':
            return message('{"route":"direct"}')
        assert schema_kind(payload) == 'file'
        if not generated:
            generated = True
            return file_decision({'tool': 'create_document', 'arguments': {
                'name': '안내', 'format': file_format, 'content': '안녕하세요. 파일 생성 확인입니다.',
            }})
        return file_decision({'tool': 'answer', 'content': '요청한 파일을 만들었습니다.'})

    upstream(serve)
    headers = auth(account())
    response = send(client, headers, create(client, headers), f'인사말을 {file_format} 파일로 만들어줘', connection_id='local')
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    file, = [payload for kind, payload in stream if kind == 'artifact']
    assert file['format'] == file_format
    raw = client.get(f"/workflows/artifacts/{file['id']}/download", headers=headers).content
    if file_format == 'txt':
        assert raw.decode() == '안녕하세요. 파일 생성 확인입니다.'
    else:
        with ZipFile(BytesIO(raw)) as document:
            assert '안녕하세요. 파일 생성 확인입니다.' in document.read('word/document.xml').decode()


@pytest.mark.parametrize('envelope', ['name', 'tool_calls', 'fenced'])
def test_native_tool_json_in_content_becomes_a_real_zip_without_leaking_json(client, account, upstream, envelope):
    call = {'name': 'create_archive', 'arguments': ARCHIVE}
    content = json.dumps({'tool_calls': [{'function': call}]} if envelope == 'tool_calls' else call, ensure_ascii=False)
    if envelope == 'fenced':
        content = '```json\n' + content + '\n```'

    def serve(request):
        payload = json.loads(request.content)
        if schema_kind(payload) == 'route':
            return message('{"route":"direct"}')
        assert 'tools' in payload and schema_kind(payload) != 'file'
        if any(item['role'] == 'tool' for item in payload['messages']):
            return message('ZIP 파일을 만들었습니다.')
        return message(content)

    upstream(serve, tools=True)
    headers = auth(account())
    saved = assert_zip(client, headers, send(client, headers, create(client, headers), ZIP_QUESTION, connection_id='local'))
    assert saved['content'] == 'ZIP 파일을 만들었습니다.'
    assert any(trace['name'] == 'file_generation' for trace in saved['tools'])


def test_native_file_refusal_is_repaired_once_before_publishing_answer(client, account, upstream):
    repairs = 0

    def serve(request):
        nonlocal repairs
        payload = json.loads(request.content)
        kind = schema_kind(payload)
        if kind == 'route':
            return message('{"route":"direct"}')
        if kind == 'file':
            repairs += 1
            return file_decision({'tool': 'create_archive', 'arguments': ARCHIVE})
        if any(item['role'] == 'tool' for item in payload['messages']):
            return message('ZIP 파일을 만들었습니다.')
        return message('저는 ZIP 파일을 직접 생성할 수 없습니다.')

    upstream(serve, tools=True)
    headers = auth(account())
    response = send(client, headers, create(client, headers), ZIP_QUESTION, connection_id='local')
    assert_zip(client, headers, response)
    assert repairs == 1
    assert '직접 생성할 수 없습니다' not in response.text


def test_native_file_json_is_recovered_after_an_earlier_file_operation(client, account, upstream):
    def serve(request):
        payload = json.loads(request.content)
        if schema_kind(payload) == 'route':
            return message('{"route":"direct"}')
        assert 'tools' in payload
        results = {item['tool_name']: json.loads(item['content']) for item in payload['messages'] if item['role'] == 'tool'}
        if not results:
            return message(json.dumps({'name': 'create_document', 'arguments': {
                'name': '인사', 'format': 'txt', 'content': '안녕하세요',
            }}, ensure_ascii=False))
        if 'create_archive' not in results:
            return message(json.dumps({'name': 'create_archive', 'arguments': {
                'name': '인사 묶음', 'file_ids': [results['create_document']['id']],
            }}, ensure_ascii=False))
        return message('TXT와 ZIP 파일을 만들었습니다.')

    upstream(serve, tools=True)
    headers = auth(account())
    response = send(client, headers, create(client, headers), '인사말 TXT 파일을 만들고 ZIP으로 묶어줘', connection_id='local')
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    files = [payload for kind, payload in stream if kind == 'artifact']
    assert [file['format'] for file in files] == ['txt', 'zip']
    assert stream[-1][1]['message']['content'] == 'TXT와 ZIP 파일을 만들었습니다.'
    raw = client.get(f"/workflows/artifacts/{files[1]['id']}/download", headers=headers).content
    with ZipFile(BytesIO(raw)) as archive:
        assert archive.read('인사.txt').decode() == '안녕하세요'


def test_failed_structured_file_output_is_a_clear_error_without_json_or_fabricated_artifact(client, account, upstream):
    def serve(request):
        payload = json.loads(request.content)
        if schema_kind(payload) == 'route':
            return message('{"route":"direct"}')
        return message('{"private_debug":"not a file decision"}')

    upstream(serve)
    headers = auth(account())
    response = send(client, headers, create(client, headers), ZIP_QUESTION, connection_id='local')
    stream = events(response)
    assert stream[-1][0] == 'error', response.text
    assert '파일 생성 내용을 확인하지 못했습니다' in response.text
    assert 'private_debug' not in response.text
    assert not any(kind in {'artifact', 'delta'} for kind, _ in stream)


def test_json_example_request_is_not_converted_into_an_archive(client, account, upstream):
    content = json.dumps({'name': 'create_archive', 'arguments': ARCHIVE}, ensure_ascii=False)

    def serve(request):
        payload = json.loads(request.content)
        if schema_kind(payload) == 'route':
            return message('{"route":"direct"}')
        assert 'tools' in payload and not schema_kind(payload)
        return message(content)

    requests = upstream(serve, tools=True)
    headers = auth(account())
    response = send(client, headers, create(client, headers), 'ZIP 파일 생성 API의 JSON 예시를 보여줘', connection_id='local')
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    assert not any(kind == 'artifact' for kind, _ in stream)
    assert stream[-1][1]['message']['content'] == content
    assert len(requests) == 2


@pytest.mark.parametrize('content', [
    '{"name":"terminal_exec","arguments":{"argv":["sh"]}}',
    'Example: {"name":"create_archive","arguments":{}}',
    '{"name":"create_archive","arguments":{"name":"x","entries":[]}}',
])
def test_file_json_recovery_never_executes_other_tools_or_fragments(content):
    assert file_generation.recover_calls(content, {'create_archive', 'terminal_exec'}) == []


@pytest.mark.parametrize('routing_result', ['bad_json', 'unsupported', 'direct', 'document'])
def test_explicit_image_creation_survives_failed_or_incorrect_route(client, account, upstream, routing_result):
    with Image.new('RGB', (4, 4), 'blue') as image, BytesIO() as output:
        image.save(output, format='PNG')
        raw = output.getvalue()

    def serve(request):
        payload = json.loads(request.content)
        if request.url.host == 'images.test':
            assert payload['prompt'] == '파란 하늘의 이미지 파일 생성해줘'
            return httpx.Response(200, json={'data': [{'b64_json': base64.b64encode(raw).decode()}]})
        assert schema_kind(payload) == 'route'
        if routing_result == 'unsupported':
            return httpx.Response(400)
        if routing_result == 'bad_json':
            return message('```json\nnot json\n```')
        return message(json.dumps({'route': 'direct', 'create_files': ['document'] if routing_result == 'document' else []}))

    requests = upstream(serve, image_url='http://images.test/v1/images/generations')
    headers = auth(account())
    response = send(client, headers, create(client, headers), '파란 하늘의 이미지 파일 생성해줘', connection_id='local')
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    file, = [payload for kind, payload in stream if kind == 'artifact']
    assert file['format'] == 'png'
    assert client.get(f"/workflows/artifacts/{file['id']}/download", headers=headers).content == raw
    assert stream[-1][1]['message']['routing']['route'] == 'image'
    assert [request.url.host for request in requests] == ['chat.test', 'images.test']
    assert not any(kind == 'activity' and payload['name'] == 'create_document' for kind, payload in stream)


def test_unconfigured_explicit_image_request_reports_the_missing_server(client, account, upstream):
    def serve(request):
        assert schema_kind(json.loads(request.content)) == 'route'
        return httpx.Response(400)

    requests = upstream(serve)
    headers = auth(account())
    response = send(client, headers, create(client, headers), '고양이를 그려줘', connection_id='local')
    stream = events(response)
    assert stream[-1][0] == 'error', response.text
    assert 'IMAGE_GENERATION_URL' in response.text
    assert not any(kind in {'artifact', 'delta'} for kind, _ in stream)
    assert len(requests) == 1


def test_image_route_preserves_a_real_question_about_missing_subject(client, account, upstream):
    def serve(request):
        assert schema_kind(json.loads(request.content)) == 'route'
        return message('{"route":"clarify","clarification":"어떤 대상을 그릴까요?"}')

    requests = upstream(serve, image_url='http://images.test/v1/images/generations')
    headers = auth(account())
    response = send(client, headers, create(client, headers), '이미지 파일 생성해줘', connection_id='local')
    stream = events(response)
    assert stream[-1][0] == 'done', response.text
    assert stream[-1][1]['message']['content'] == '어떤 대상을 그릴까요?'
    assert not any(kind == 'artifact' for kind, _ in stream)
    assert len(requests) == 1


@pytest.mark.parametrize('question', [
    '"이미지 생성해줘"라는 문장을 영어로 번역해줘',
    '저녁 바다를 그려줘라는 문장을 영어로 번역해 줘',
    '이미지 생성 프롬프트를 작성해줘',
    '이미지 파일 생성 방법을 알려줘',
    '이미지 생성 API의 JSON 예시를 보여줘',
    '이미지가 포함된 HTML 발표자료를 만들어줘',
    'HTML로 이미지를 그려줘',
    '이미지 생성하지 말고 ZIP 파일만 만들어줘',
    '이미지를 생성하지 말고 ZIP 파일만 만들어줘',
    'Do not create an image; make a ZIP file',
    '고양이 이미지 만들어서 ZIP 파일로 묶어줘',
    '고양이를 그려줘. 결과를 ZIP으로 묶어줘',
    '이 사진을 PNG로 저장해줘',
    'Explain how to generate an image',
])
def test_image_fallback_does_not_match_explanations_examples_or_documents(question):
    assert not file_generation.image_request(question)


@pytest.mark.parametrize('question', [
    'ZIP 만들지 마',
    'ZIP 생성하지 말고 설명해줘',
    'Do not create a ZIP file',
    'ZIP 파일 생성이 가능한지 알려줘',
])
def test_file_fallback_requires_an_actual_creation_request(question):
    assert not file_generation.is_creation_request(question)
