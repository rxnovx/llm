"""Verify that a single chat turn composes image, repository/app, and file tools."""

import asyncio
import base64
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler
import json
import threading
from urllib.parse import parse_qs
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.assistant import web_search
from app.modules.chat.models import Admission
from app.modules.chat.usage import TokenCall
from app.modules.workflows.models import Artifact
from app.modules.workspaces.models import Workspace
from test_assistant import admin, registration, remote_mcp
from test_chat import auth, create, events, send
from test_gitlab_apps import connect, gitlab
from test_image_generation import png_bytes
from test_knowledge import index, upload
from test_multimodal_workflows import upload_image
from test_parallel_and_gateway import model_response, use_model
from test_workspace_flow import http_server, workspace_stack
from test_workflows import project


@pytest.fixture
def composition_models(client, monkeypatch):
    monkeypatch.setenv('LLM_USER_CONCURRENCY', '2')
    monkeypatch.setenv('ASSISTANT_AUTOMATIC_ENABLED', 'true')
    monkeypatch.setenv('ASSISTANT_GROUNDING_REVIEW', 'true')
    models = [
        {'id': provider, 'name': provider, 'provider': provider, 'base_url': url,
         'model': 'chat-model', 'supports_vision': True, 'supports_tools': True,
         'context_window': 24576, 'max_output_tokens': 1024, 'max_concurrent': 2}
        for provider, url in [('vllm', 'http://model.test/v1'), ('ollama', 'http://model.test')]
    ]
    monkeypatch.setenv('LLM_CONNECTIONS_JSON', json.dumps(models))
    get_settings.cache_clear()


def model_json(provider, content='', *, call=None):
    message = {'role': 'assistant', 'content': content}
    if call:
        name, arguments = call
        message['tool_calls'] = [{'id': name, 'type': 'function', 'function': {
            'name': name,
            'arguments': json.dumps(arguments, ensure_ascii=False) if provider == 'vllm' else arguments,
        }}]
    body = {'choices': [{'message': message}]} if provider == 'vllm' else {'message': message}
    return httpx.Response(200, json=body)


def schema_of(payload):
    if 'response_format' in payload:
        return payload['response_format']['json_schema']['schema']
    return payload.get('format', {})


def current_image(payload, provider, expected):
    user_turns = [message for message in payload['messages'] if message['role'] == 'user']
    assert user_turns
    for older in user_turns[:-1]:
        assert isinstance(older['content'], str) and 'images' not in older
    current = user_turns[-1]
    if provider == 'ollama':
        encoded, = current['images']
        text = current['content']
    else:
        text_part, image_part = current['content']
        assert text_part['type'] == 'text' and image_part['type'] == 'image_url'
        text = text_part['text']
        media, encoded = image_part['image_url']['url'].split(',', 1)
        assert media == 'data:image/png;base64'
    assert base64.b64decode(encoded) == expected
    return text


def assert_pdf(client, headers, conversation, response, marker):
    assert response.status_code == 200, response.text
    stream = events(response)
    assert stream[-1][0] == 'done', response.text[-6500:]
    artifact, = [data for name, data in stream if name == 'artifact']
    assert artifact['format'] == 'pdf'
    assert stream[-1][1]['message']['artifacts'] == [artifact]
    saved = client.get(f'/chat/conversations/{conversation}', headers=headers).json()['messages'][-1]
    assert saved['artifacts'] == [artifact]
    preview = client.get(f"/workflows/artifacts/{artifact['id']}", headers=headers)
    assert marker in preview.json()['content']
    download = client.get(f"/workflows/artifacts/{artifact['id']}/download", headers=headers)
    assert download.status_code == 200 and download.content.startswith(b'%PDF-')
    return saved


@pytest.mark.parametrize(('provider', 'explicit_app'), [('vllm', False), ('ollama', False), ('vllm', True)])
def test_image_read_app_and_pdf_share_one_turn(
    client, account, composition_models, monkeypatch, png_bytes, provider, explicit_app,
):
    owner, other = auth(account()), auth(account('bob'))
    admin(client)
    conversation = create(client, owner)
    assert events(send(client, owner, conversation, '이전 메시지'))[-1][0] == 'done'
    document_id = upload_image(client, owner, conversation, png_bytes)
    question = '첨부 화면을 사내 규정과 비교해서 PDF 보고서를 만들어 줘.'
    report = '첨부 화면과 앱의 자료를 비교했습니다. [MS1]'
    model_calls = []

    def handle(request):
        payload = json.loads(request.content)
        schema = schema_of(payload).get('properties', {})
        if 'route' in schema:
            assert document_id not in json.dumps(payload)
            plan = {'route': 'vision'} if explicit_app else {'route': 'mcp', 'mcp_ids': ['policy'], 'use_vision': True}
            return model_json(provider, json.dumps(plan))
        if 'verdict' in schema:
            review = json.loads(current_image(payload, provider, png_bytes))
            assert report in review['draft'] and any(item['id'] == 'MS1' for item in review['evidence'])
            return model_json(provider, '{"verdict":"supported"}')
        assert question in current_image(payload, provider, png_bytes)
        tools = {tool['function']['name']: tool['function'] for tool in payload['tools']}
        assert 'create_document' in tools
        assert not any(': change.' in tool['description'] for tool in tools.values())
        lookup = next(name for name, tool in tools.items() if ': lookup.' in tool['description'])
        previous = [message for message in payload['messages'] if message['role'] == 'tool']
        model_calls.append(previous)
        if not previous:
            return model_json(provider, call=(lookup, {'query': '화면 기준'}))
        if len(previous) == 1:
            result = json.loads(previous[0]['content'])
            assert result['source_id'] == 'MS1' and result['result']['text'] == 'Policy 화면 기준'
            return model_json(provider, call=('create_document', {'format': 'pdf', 'name': '화면 비교', 'content': report}))
        assert json.loads(previous[-1]['content'])['format'] == 'pdf'
        return model_json(provider, report)

    with remote_mcp('Policy') as (url, calls, _):
        registration_response = client.put('/assistant/mcp/servers/policy', headers=owner,
            json=registration('policy', '사내 규정', url) | {'secret_ref': ''})
        assert registration_response.status_code == 200, registration_response.text
        use_model(monkeypatch, handle)
        options = {'image_document_ids': [document_id]}
        if explicit_app:
            options['mcp_ids'] = ['policy']
        response = send(client, owner, conversation, question, connection_id=provider, assistant=options)
        saved = assert_pdf(client, owner, conversation, response, report)
        assert saved['sources'][0]['id'] == 'MS1' and saved['sources'][0]['app'] == 'mcp'
        assert report in saved['content'] and len(model_calls) == 3
        assert calls == [('read', '화면 기준')]
        with client.db_factory() as db:
            measured = db.scalars(select(TokenCall).where(TokenCall.request_id == saved['active_version'])).all()
            assert len(measured) >= 3 and all(0 < item.input_tokens < 24576 for item in measured)
        another_conversation = create(client, other)
        denied = send(client, other, another_conversation, question, connection_id=provider,
            assistant={'image_document_ids': [document_id]})
        assert denied.status_code in {403, 404}, denied.text
        assert calls == [('read', '화면 기준')]


@pytest.mark.parametrize('review_supported', [True, False])
def test_image_and_gitlab_source_can_produce_pdf(
    client, account, workspace_stack, composition_models, monkeypatch, png_bytes, review_supported,
):
    _, commit = workspace_stack
    owner = auth(account())
    gitlab_connection = connect(client, owner)
    conversation = create(client, owner)
    document_id = upload_image(client, owner, conversation, png_bytes)
    question = '첨부 화면을 team/service 로그인 코드와 비교 분석해서 PDF 보고서를 만들어 줘.'

    def handle(request):
        payload = json.loads(request.content)
        schema = schema_of(payload).get('properties', {})
        if 'route' in schema:
            request_data = json.loads(payload['messages'][-1]['content'])
            assert request_data['available']['image_vision']
            assert request_data['available']['gitlab_connections'][0]['connection_id'] == gitlab_connection
            return model_json('vllm', json.dumps({'route': 'gitlab', 'project': 'team/service', 'use_vision': True}))
        if 'verdict' in schema:
            review = json.loads(current_image(payload, 'vllm', png_bytes))
            assert '화면과 구현을 비교했습니다.' in review['draft']
            assert any('return user.active' in item['content'] for item in review['evidence'])
            verdict = 'supported' if review_supported else 'insufficient'
            return model_json('vllm', json.dumps({'verdict': verdict}))
        assert question in current_image(payload, 'vllm', png_bytes)
        names = {tool['function']['name'] for tool in payload['tools']}
        assert {'workspace_read_file', 'create_document'} <= names
        assert not {'terminal_exec', 'workspace_write_file'} & names
        previous = [message for message in payload['messages'] if message['role'] == 'tool']
        if not previous:
            return model_json('vllm', call=('workspace_read_file', {'path': 'main.py', 'start_line': 1, 'end_line': 2}))
        result = json.loads(previous[0]['content'])
        assert 'return user.active' in result['content']
        report = f"화면과 구현을 비교했습니다. [{result['source_id']}]"
        if len(previous) == 1:
            return model_json('vllm', call=('create_document', {'format': 'pdf', 'name': '화면과 구현', 'content': report}))
        return model_json('vllm', report)

    use_model(monkeypatch, handle)
    response = send(client, owner, conversation, question, connection_id='vllm',
        assistant={'image_document_ids': [document_id]})
    if review_supported:
        saved = assert_pdf(client, owner, conversation, response, '화면과 구현을 비교했습니다.')
        assert any(source['commit'] == commit for source in saved['sources'] if source['app'] == 'gitlab')
    else:
        stream = events(response)
        assert stream[-1][0] == 'done', response.text[-6500:]
        assert not any(name == 'artifact' for name, _ in stream)
        saved = stream[-1][1]['message']
        assert not saved['artifacts'] and '화면과 구현을 비교했습니다.' not in saved['content']
    assert saved['apps'][0]['workspace_id'] and saved['apps'][0]['mode'] == 'workspace'
    with client.db_factory() as db:
        workspace = db.scalar(select(Workspace))
        assert workspace is not None and not workspace.allow_execution
        assert bool(db.scalar(select(Artifact))) == review_supported


def test_automatic_parallel_tool_is_available_only_after_planning(client, account, composition_models, monkeypatch):
    owner = auth(account())
    conversation = create(client, owner)
    entered = set()
    concurrent = asyncio.Event()
    tasks = [{'name': '성능', 'question': '성능을 검토하세요.'}, {'name': '유지보수', 'question': '유지보수를 검토하세요.'}]

    async def handle(request):
        payload = json.loads(request.content)
        schema = schema_of(payload).get('properties', {})
        if 'route' in schema:
            return model_json('vllm', '{"route":"direct","parallel_agents":true}')
        if 'verdict' in schema:
            return model_json('vllm', '{"verdict":"supported"}')
        if payload['stream']:
            assert 'tools' not in payload
            name = json.loads(payload['messages'][-1]['content'])['assignment']
            entered.add(name)
            if len(entered) == 2:
                concurrent.set()
            await asyncio.wait_for(concurrent.wait(), timeout=4)
            return model_response('성능을 검토했습니다.') if name.startswith('성능') else httpx.Response(502)
        assert 'parallel_analyze' in {tool['function']['name'] for tool in payload['tools']}
        latest = payload['messages'][-1]
        if latest['role'] != 'tool':
            return model_json('vllm', call=('parallel_analyze', {'tasks': tasks}))
        results = json.loads(latest['content'])['tasks']
        assert [(item['name'], item['status']) for item in results] == [('성능', 'complete'), ('유지보수', 'error')]
        return model_json('vllm', '성능 검토는 끝났고 유지보수 검토는 실패했습니다.')

    use_model(monkeypatch, handle)
    response = send(client, owner, conversation, '성능과 유지보수 관점에서 각각 검토한 뒤 결과를 합쳐 줘.',
        connection_id='vllm')
    assert response.status_code == 200, response.text
    stream = events(response)
    assert stream[-1][0] == 'done', response.text[-4500:]
    assert entered == {task['question'] for task in tasks}
    worker_events = [data for name, data in stream if name == 'activity' and data['name'] == 'parallel_worker']
    assert {data['status'] for data in worker_events} == {'running', 'complete', 'error'}
    assert '유지보수 검토는 실패' in stream[-1][1]['message']['content']


def test_indirect_presentation_request_creates_pptx_without_a_format_hint(client, account, composition_models, monkeypatch):
    owner = auth(account())
    conversation = create(client, owner)
    question = '이걸 발표용으로 정리해서 보내 줘.'

    def handle(request):
        payload = json.loads(request.content)
        if 'route' in schema_of(payload).get('properties', {}):
            assert json.loads(payload['messages'][-1]['content'])['available']['file_request']
            return model_json('vllm', '{"route":"direct","create_files":["document"]}')
        names = {tool['function']['name'] for tool in payload['tools']}
        assert names == {'create_document'}
        if payload['messages'][-1]['role'] != 'tool':
            return model_json('vllm', call=('create_document', {
                'name': '제품 발표', 'format': 'pptx', 'slides': [{'title': '개요', 'bullets': ['핵심 내용']}],
            }))
        return model_json('vllm', '발표자료를 만들었습니다.')

    use_model(monkeypatch, handle)
    response = send(client, owner, conversation, question, connection_id='vllm')
    stream = events(response)
    assert stream[-1][0] == 'done', response.text[-4200:]
    result, = stream[-1][1]['message']['artifacts']
    assert result['format'] == 'pptx'
    download = client.get(f'/workflows/artifacts/{result["id"]}/download', headers=owner)
    assert download.status_code == 200 and download.content.startswith(b'PK')


def test_cancelling_image_turn_removes_draft_file_and_releases_model_slot(client, account, composition_models, monkeypatch, png_bytes):
    owner = auth(account())
    conversation = create(client, owner)
    document_id = upload_image(client, owner, conversation, png_bytes)
    request_id = str(uuid4())
    draft_written, resume = threading.Event(), threading.Event()

    async def handle(request):
        payload = json.loads(request.content)
        if 'route' in schema_of(payload).get('properties', {}):
            return model_json('vllm', '{"route":"vision"}')
        current_image(payload, 'vllm', png_bytes)
        if not any(message['role'] == 'tool' for message in payload['messages']):
            return model_json('vllm', call=('create_document', {
                'format': 'pdf', 'name': '초안', 'content': '검토 전 초안입니다.',
            }))
        draft_written.set()
        await asyncio.to_thread(resume.wait, 5)
        return model_json('vllm', '초안 검토를 끝냈습니다.')

    use_model(monkeypatch, handle)
    path = f'/chat/conversations/{conversation}'
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, f'{path}/messages', headers=owner, json={
            'content': '첨부 화면을 설명하는 PDF를 만들어 줘.', 'connection_id': 'vllm',
            'request_id': request_id, 'assistant': {'image_document_ids': [document_id]},
        })
        try:
            assert draft_written.wait(5)
            with client.db_factory() as db:
                artifact = db.scalar(select(Artifact).where(Artifact.run_id == request_id))
                assert artifact is not None and artifact.status == 'draft'
                artifact_id = artifact.id
            assert client.get(f'/workflows/artifacts/{artifact_id}/download', headers=owner).status_code == 404
            assert client.post(f'{path}/cancel', headers=owner, json={'request_id': request_id}).status_code == 204
            response = pending.result(timeout=5)
        finally:
            resume.set()

    stream = events(response)
    assert stream[-1][0] == 'cancelled', response.text[-3800:]
    assert not any(name == 'artifact' for name, _ in stream)
    with client.db_factory() as db:
        assert db.get(Artifact, artifact_id) is None
        assert db.scalar(select(Admission.state).where(Admission.request_id == request_id)) == 'cancelled'


def test_web_search_uses_current_prompt_and_keeps_attachment_text_internal(client, account, composition_models, monkeypatch):
    owner = auth(account())
    conversation = create(client, owner)
    private_token = 'internal-attachment-token-5284'
    document = upload(client, owner, conversation=conversation, text=f'첨부된 비공개 메모 {private_token}')
    index(client)
    query = 'FastAPI 최신 공식 문서'
    question = 'FastAPI 최신 공식 문서 웹에서 찾아 줘.'
    calls = []
    source_url = 'https://fastapi.tiangolo.com/'

    class SearchDouble(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_POST(self):
            data = self.rfile.read(int(self.headers['Content-Length'])).decode()
            calls.append((self.path, parse_qs(data), self.headers.get('Cookie')))
            body = json.dumps({'results': [
                {'url': 'javascript:alert(1)', 'title': '무효 주소', 'content': '인용하면 안 되는 주소'},
                {'url': source_url, 'title': 'FastAPI 공식 문서', 'content': 'FastAPI 공식 문서입니다.'},
            ]}, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    tool_result = []

    def handle(request):
        payload = json.loads(request.content)
        schema = schema_of(payload).get('properties', {})
        if 'route' in schema:
            assert private_token not in json.dumps(payload)
            return model_json('vllm', json.dumps({'route': 'direct', 'search_web': True, 'query': query}))
        if 'verdict' in schema:
            return model_json('vllm', '{"verdict":"supported"}')
        tools = {tool['function']['name'] for tool in payload.get('tools', [])}
        assert web_search.TOOL_NAME in tools
        latest_question = next(message['content'] for message in reversed(payload['messages']) if message['role'] == 'user')
        assert private_token in latest_question and 'WS1' in latest_question
        assert 'javascript:' not in latest_question
        last = payload['messages'][-1]
        if last['role'] != 'tool':
            return model_json('vllm', call=(web_search.TOOL_NAME, {'query': private_token}))
        tool_result.append(json.loads(last['content']))
        return model_json('vllm', '공식 문서와 첨부를 비교했습니다. [WS1]')

    with http_server(SearchDouble) as search:
        monkeypatch.setenv('ASSISTANT_WEB_SEARCH_URL', f'http://127.0.0.1:{search.server_port}/search')
        get_settings.cache_clear()
        use_model(monkeypatch, handle)
        response = send(client, owner, conversation, question, connection_id='vllm',
            assistant={'document_ids': [document['id']]})

    assert response.status_code == 200, response.text
    stream = events(response)
    assert stream[-1][0] == 'done', response.text[-5200:]
    assert len(tool_result) == 1 and tool_result[0].get('error')
    assert len(calls) == 1 and calls[0][0] == '/search' and calls[0][2] is None
    assert calls[0][1]['format'] == ['json'] and 'FastAPI' in calls[0][1]['q'][0]
    assert private_token not in str(calls)
    message = stream[-1][1]['message']
    assert '[WS1]' in message['content']
    source, = [item for item in message['sources'] if item['app'] == 'web']
    assert source['id'] == 'WS1' and source['url'] == source_url


def test_project_history_cites_the_matching_shared_conversation_without_leaking_private_chat(
    client, account, composition_models, monkeypatch,
):
    owner, teammate = auth(account()), auth(account('bob'))
    team = project(client, owner)
    membership = client.post(f"/workflows/projects/{team['id']}/members", headers=owner,
        json={'username': 'bob', 'role': 'editor'})
    assert membership.status_code == 200, membership.text

    def team_chat(headers):
        response = client.post('/chat/conversations', headers=headers, json={'project_id': team['id']})
        assert response.status_code == 201, response.text
        return response.json()['id']

    keyword = '건축회의'
    visible, private, current = team_chat(teammate), create(client, owner), team_chat(owner)
    assert events(send(client, teammate, visible, keyword + ' 결과는 출입구를 동쪽으로 정했습니다.'))[-1][0] == 'done'
    assert events(send(client, owner, private, keyword + ' private-history-token-5285'))[-1][0] == 'done'
    observed = []

    def handle(request):
        payload = json.loads(request.content)
        schema = schema_of(payload).get('properties', {})
        if 'route' in schema:
            return model_json('vllm', json.dumps({'route': 'direct', 'search_history': True, 'query': keyword}))
        if 'verdict' in schema:
            return model_json('vllm', '{"verdict":"supported"}')
        outgoing = json.dumps(payload, ensure_ascii=False)
        assert 'private-history-token-5285' not in outgoing
        assert 'CH1' in outgoing and '출입구를 동쪽' in outgoing
        observed.append(outgoing)
        reply = '지난 프로젝트 회의에서는 출입구를 동쪽으로 정했습니다. [CH1]'
        return model_response(reply) if payload['stream'] else model_json('vllm', reply)

    use_model(monkeypatch, handle)
    response = send(client, owner, current, f'이전 대화에서 {keyword} 결정을 찾아서 알려 줘.', connection_id='vllm')
    assert response.status_code == 200, response.text
    stream = events(response)
    assert stream[-1][0] == 'done', response.text[-5200:]
    assert observed
    message = stream[-1][1]['message']
    source, = [item for item in message['sources'] if item['app'] == 'chat_history']
    assert source['id'] == 'CH1' and source['conversation_id'] == visible
    detail = client.get(f'/chat/conversations/{visible}', headers=owner,
        params={'around_message_id': source['message_id']}).json()
    assert any(item['id'] == source['message_id'] for item in detail['messages'])
    assert '[CH1]' in message['content']
