"""MR follow-up over the real checkout/Terminal MCP with GitLab protocol doubles."""

import asyncio
import base64
import json
import sys
from urllib.parse import quote

import pytest
from sqlalchemy import update

from app.core.config import get_settings
from app.modules.chat import providers
from app.modules.chat.models import Conversation
from app.modules.integrations import config, merge_requests, publishing, transport
from test_chat import auth, create, events, send
from test_gitlab_apps import gitlab, connect, INSTANCE, SHA
from test_gitlab_publishing import enable_publishing, publish_connection
from test_workspace_flow import workspace_stack

IID = 27
TARGET_PATH = '/api/v4/projects/8/merge_requests/27'
NAMESPACE_PATH = '/api/v4/projects/team%2Fupstream/merge_requests/27'
SOURCE_BRANCH = 'feature/login'
BRANCH_PATH = '/api/v4/projects/7/repository/branches/' + quote(SOURCE_BRANCH, safe='')
ORIGINAL = 'def login(user):\n    return user.active\n'
EDITED = 'def login(user):\n    return bool(user.active)\n'
REVIEW = 'Return a bool from login.'
TRACE = 'AssertionError: login returned an integer'


def use_model(monkeypatch, *, supports_tools):
    monkeypatch.setenv('LLM_CONNECTIONS_JSON', json.dumps([{
        'id': 'local', 'name': 'Local', 'provider': 'vllm', 'base_url': 'http://model.invalid/v1',
        'model': 'local', 'supports_tools': supports_tools,
    }]))
    get_settings.cache_clear()


def mr_link(base):
    return f'{base}/team/upstream/-/merge_requests/{IID}'


def fixture_mr(provider, sha=SHA, *, state='opened'):
    return {
        'iid': IID, 'title': 'Normalize login result', 'description': 'Login follow-up',
        'state': state, 'sha': sha, 'target_project_id': 8, 'source_project_id': 7,
        'target_branch': 'main', 'source_branch': SOURCE_BRANCH, 'web_url': mr_link(provider.base_url),
        'head_pipeline': {'id': 58, 'status': 'failed', 'sha': sha, 'project_id': 7},
    }


def evidence_api(path, params):
    if path == TARGET_PATH + '/discussions':
        return [{'notes': [{'system': True, 'body': 'System event'},
            {'body': REVIEW, 'author': {'username': 'reviewer'}, 'position': {'new_path': 'main.py', 'new_line': 2}}]}]
    if path == TARGET_PATH + '/diffs':
        return [{'new_path': 'main.py', 'diff': '+    return user.active\n'},
            {'new_path': '.env', 'diff': '+PRIVATE_FIXTURE=not-model-context'}]
    if path == '/api/v4/projects/7/pipelines/58/jobs':
        assert params == {'scope[]': 'failed', 'per_page': 4}
        return [{'id': 66, 'name': 'unit tests', 'status': 'failed', 'failure_reason': 'script_failure'},
            {'id': 67, 'name': 'passing job', 'status': 'success'}]
    if path == '/api/v4/projects/7/jobs/66/trace':
        return ('diagnostic output\n' * 200) + '\x1b[31m' + TRACE + '\x1b[0m\n'
    raise AssertionError(path)


def test_mr_link_or_number_reads_review_and_ci_without_a_worker(client, account, gitlab, monkeypatch):
    use_model(monkeypatch, supports_tools=False)
    original = transport.request
    reads = []

    async def remote(provider, method, path, **kwargs):
        if '/merge_requests/' in path or '/pipelines/' in path or '/jobs/' in path:
            assert method == 'GET' and provider.base_url == INSTANCE['base_url']
            reads.append(path)
            if path in {TARGET_PATH, NAMESPACE_PATH}:
                return fixture_mr(provider, state='merged')
            return evidence_api(path, kwargs.get('params'))
        return await original(provider, method, path, **kwargs)

    async def model(_connection, _model, messages, _settings):
        prompt = messages[-1]['content']
        assert REVIEW in prompt and TRACE in prompt and 'main.py' in prompt
        assert '\\u001b' not in prompt and 'PRIVATE_FIXTURE' not in prompt
        yield '리뷰와 CI의 반환 타입 문제를 확인했습니다.'

    monkeypatch.setattr(transport, 'request', remote)
    monkeypatch.setattr(providers, 'stream_reply', model)
    alice = auth(account())
    connection = connect(client, alice)
    endpoint = '/apps/gitlab/merge-request/resolve'
    url = mr_link(INSTANCE['base_url'])

    for invalid in [url.replace('gitlab.example.internal', 'gitlab.example.internal.evil.test'),
                    url.replace('://', '://gitlab.example.internal@'),
                    url.replace('/team/upstream/', '/team/%2e%2e/upstream/')]:
        assert client.post(endpoint, headers=alice, json={'reference': invalid}).status_code == 422
    assert not reads
    assert client.post(endpoint, headers=auth(account('bob')),
        json={'reference': url, 'connection_id': connection}).status_code == 404
    assert not reads
    resolved = client.post(endpoint, headers=alice, json={'reference': '!27', 'project_id': 8, 'connection_id': connection})
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()['merge_request']['url'] == url
    assert not resolved.json()['merge_request']['editable']

    conversation = create(client, alice)
    reply = send(client, alice, conversation, content=f'{url} 리뷰와 실패한 CI를 확인해 줘',
                 connection_id='local', model='local')
    stream = events(reply)
    assert stream[-1][0] == 'done', reply.text[-2200:]
    assert not any(kind == 'workspace' for kind, _ in stream)
    assert stream[0][1]['assistant_message']['apps'][0]['mode'] == 'merge_request'
    assert reads[-6:] == [NAMESPACE_PATH, TARGET_PATH, TARGET_PATH + '/discussions', '/api/v4/projects/7/pipelines/58/jobs',
                         '/api/v4/projects/7/jobs/66/trace', TARGET_PATH + '/diffs']
    trace = stream[-1][1]['message']['tools']
    for stage in ['gitlab_mr_overview', 'gitlab_mr_reviews', 'gitlab_mr_ci', 'gitlab_mr_diffs']:
        assert {item['status'] for item in trace if item['name'] == stage} == {'running', 'complete'}
    assert TRACE not in reply.text  # Remote logs go to the model, not the activity payload.


@pytest.mark.parametrize('cancel_in_flight', [False, True])
def test_updates_selected_mr_fork_source_with_guarded_files_and_recovers_commit(
        client, account, workspace_stack, monkeypatch, cancel_in_flight):
    terminal, base_sha = workspace_stack
    enable_publishing(monkeypatch)
    use_model(monkeypatch, supports_tools=True)
    original = transport.request
    commit, posts = None, []
    moved = False
    commit_sha, last_file_sha = 'b' * 40, 'c' * 40

    async def remote(provider, method, path, **kwargs):
        nonlocal commit
        if path == '/oauth/token':
            data = await original(provider, method, path, **kwargs)
            return data | {'scope': config.PUBLISH_SCOPES}
        if path in {TARGET_PATH, NAMESPACE_PATH}:
            return fixture_mr(provider, 'd' * 40 if moved else commit_sha if commit else base_sha)
        if path == BRANCH_PATH:
            return {'name': SOURCE_BRANCH, 'commit': commit or {'id': 'd' * 40 if moved else base_sha}}
        if path == '/api/v4/projects/7/repository/files/main.py':
            assert kwargs['params'] == {'ref': base_sha}
            return {'last_commit_id': last_file_sha, 'encoding': 'base64', 'size': len(ORIGINAL.encode()),
                'content': base64.b64encode(ORIGINAL.encode()).decode()}
        if method == 'POST' and path.startswith('/api/v4/'):
            posts.append(path)
            assert path == '/api/v4/projects/7/repository/commits'
            body = kwargs['payload']
            assert set(body) == {'branch', 'commit_message', 'actions'}
            assert body['branch'] == SOURCE_BRANCH
            assert body['actions'] == [{'action': 'update', 'file_path': 'main.py', 'content': EDITED, 'last_commit_id': last_file_sha}]
            assert 'LLM-MR-Update:' in body['commit_message']
            commit = {'id': commit_sha, 'message': body['commit_message'], 'parent_ids': [base_sha]}
            if cancel_in_flight:
                with client.db_factory() as db:
                    db.execute(update(Conversation).where(Conversation.id == conversation).values(cancel_requested=True))
                    db.commit()
                await asyncio.sleep(.5)
            raise transport.GitlabError('response lost', 502)
        if '/merge_requests/' in path or '/pipelines/' in path or '/jobs/' in path:
            return evidence_api(path, kwargs.get('params'))
        return await original(provider, method, path, **kwargs)

    mode = 'without_grant'

    async def model(_connection, _model, history, tools, _settings):
        nonlocal moved
        names = {tool['function']['name'] for tool in tools}
        assert publishing.TOOL_NAME not in names and 'workspace_export_files' not in names
        assert (publishing.UPDATE_TOOL in names) == (mode != 'without_grant')
        last = history[-1]
        previous = last.get('tool_name')
        if previous is None:
            assert REVIEW in last['content'] and TRACE in last['content']
            name, args = merge_requests.READ_TOOL, {'section': 'ci'}
        elif previous == merge_requests.READ_TOOL:
            assert TRACE in json.loads(last['content'])['failed_jobs'][0]['trace_tail']
            name, args = 'workspace_read_file', {'path': 'main.py'}
        elif previous == 'workspace_read_file':
            name, args = 'workspace_write_file', {'path': 'main.py', 'content': EDITED,
                'expected_hash': json.loads(last['content'])['content_hash']}
        elif previous == 'workspace_write_file':
            name, args = 'terminal_exec', {'argv': [sys.executable, '-c',
                "from types import SimpleNamespace; from main import login; assert login(SimpleNamespace(active=1)) is True"]}
        elif previous == 'terminal_exec':
            assert json.loads(last['content'])['exit_code'] == 0
            name, args = 'workspace_diff', {}
        elif previous == 'workspace_diff':
            assert 'main.py' in last['content']
            moved = mode == 'stale'
            name, args = publishing.UPDATE_TOOL, {'paths': ['main.py'], 'commit_message': '로그인 반환값 수정'}
        else:
            result = json.loads(last['content'])
            if mode == 'without_grant':
                assert 'error' in result
            else:
                assert result['commit_sha'] == commit_sha
            return {'role': 'assistant', 'content': '기존 MR 작업 결과를 확인했습니다.', 'tool_calls': []}
        return {'role': 'assistant', 'content': '', 'tool_calls': [
            {'id': name, 'type': 'function', 'function': {'name': name, 'arguments': args}},
        ]}

    monkeypatch.setattr(transport, 'request', remote)
    monkeypatch.setattr(providers, 'tool_reply', model)
    headers = auth(account())
    connection = publish_connection(client, headers)
    conversation = create(client, headers)
    base_url = config.instance('office').base_url
    resolved = client.post('/apps/gitlab/merge-request/resolve', headers=headers,
        json={'reference': mr_link(base_url), 'connection_id': connection})
    assert resolved.status_code == 200, resolved.text
    selection = resolved.json()['selection'] | {'mode': 'execute'}
    assert selection['project_id'] == 7 and selection['merge_request']['project_id'] == 8

    def run(publish=False):
        response = send(client, headers, conversation, content='리뷰와 CI 오류를 수정하고 테스트해서 같은 MR에 커밋해 줘',
            connection_id='local', model='local', apps=[selection], publish_gitlab=publish)
        return events(response), response

    stream, response = run()
    assert stream[-1][0] == 'done', response.text[-2400:]
    assert not posts
    mode = 'stale'
    stream, response = run(True)
    assert stream[-1][0] == 'error' and not posts, response.text[-2400:]
    assert '기준 커밋이 변경' in stream[-1][1]['detail']
    moved = False

    mode = 'update'
    stream, response = run(True)
    assert stream[-1][0] == ('cancelled' if cancel_in_flight else 'done'), response.text[-2400:]
    assert posts == ['/api/v4/projects/7/repository/commits']
    workspace_id = next(data['id'] for kind, data in stream if kind == 'workspace')
    assert (terminal.store.repo(workspace_id) / 'main.py').read_text() == EDITED
    if not cancel_in_flight:
        stages = [data['name'] for kind, data in stream if kind == 'activity' and data['status'] == 'complete']
        assert stages.index('gitlab_mr_reviews') < stages.index('terminal_exec') < stages.index(publishing.RESULT_TRACE)
    saved = client.get(f'/chat/conversations/{conversation}', headers=headers).json()['messages'][-1]
    result = json.loads(next(trace['detail'] for trace in saved['tools'] if trace['name'] == publishing.RESULT_TRACE))
    assert result['url'] == mr_link(base_url)
    assert result['commit_url'] == f'{base_url}/team/service/-/commit/{commit_sha}'
    assert result['status'] == 'updated'
    assert 'local-access-' not in response.text and TRACE not in response.text


def test_unavailable_pipeline_is_distinguished_from_no_pipeline():
    provider = config.GitlabInstance.model_validate(INSTANCE)

    class GitLab:
        async def get(self, path, **kwargs):
            if path.endswith('/pipelines'):
                if unavailable:
                    raise transport.GitlabError('unavailable', 403)
                return []
            assert path.endswith('/merge_requests/27')
            return fixture_mr(provider) | {'head_pipeline': None}

    upstream = GitLab()
    upstream.provider = provider
    mr = asyncio.run(merge_requests.load(upstream, 8, IID))
    unavailable = True
    assert asyncio.run(merge_requests.ci(upstream, mr)) == {'status': 'unavailable'}
    unavailable = False
    assert asyncio.run(merge_requests.ci(upstream, mr)) == {'status': 'no_pipeline'}
