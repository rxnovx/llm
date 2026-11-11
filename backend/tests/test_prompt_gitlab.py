"""Prompt-granted GitLab operations on the normal chat endpoint."""

import json

import pytest

from app.core.config import get_settings
from app.modules.assistant import gitlab as prompt_gitlab
from app.modules.chat import providers
from app.modules.integrations import config, publishing, transport
from test_chat import auth, create, events, send
from test_gitlab_apps import connect, gitlab
from test_gitlab_publishing import enable_publishing, publish_connection
from test_workspace_flow import git, workspace_stack

ORIGINAL = 'def login(user):\n    return user.active\n'
EDITED = 'def login(user):\n    return bool(user.active)\n'
PUBLICATION_TOOLS = {publishing.PUSH_TOOL, publishing.TOOL_NAME, publishing.UPDATE_TOOL}
CHANGES = {'paths': ['main.py'], 'commit_message': '로그인 반환 타입 정리'}


@pytest.mark.parametrize(('question', 'edit', 'commit', 'publication', 'project'), [
    ('@gitlab team/service PR을 생성해줘', False, False, 'merge_request', 'team/service'),
    ('team/service에서 수정 허용', True, False, None, 'team/service'),
    ('수정, 커밋, 푸쉬, PR 생성 허용', True, True, 'merge_request', None),
    ('푸시만 해 줘', False, False, 'push', None),
    ('현재 변경사항을 커밋해 줘', False, True, None, None),
    ('커밋하고 푸시하지 마', False, True, None, None),
])
def test_prompt_grants_only_the_requested_actions(question, edit, commit, publication, project):
    intent = prompt_gitlab.prompt(question)
    assert intent is not None
    assert (intent.edit, intent.commit, intent.publication) == (edit, commit, publication)
    assert intent.projects == ((project,) if project else ())


@pytest.mark.parametrize('question', [
    '@gitlab team/service "푸시해 줘"라는 문구를 설명해 줘',
    '@gitlab team/service\n```text\n푸시해 줘\nMR 생성해 줘\n```',
    '@gitlab team/service 푸시하지 마',
    '@gitlab team/service 푸시하는 방법을 설명해 줘',
    'Push notifications are failing',
    'push changes can take time',
])
def test_prose_and_quoted_commands_grant_no_gitlab_write(question):
    intent = prompt_gitlab.prompt(question)
    assert intent is None or not intent.execute


def configure_model(monkeypatch):
    monkeypatch.setenv('ASSISTANT_AUTOMATIC_ENABLED', 'false')
    monkeypatch.setenv('LLM_CONNECTIONS_JSON', json.dumps([{
        'id': 'local', 'name': 'Local', 'provider': 'vllm',
        'base_url': 'http://model.invalid/v1', 'model': 'local',
        'supports_tools': True, 'context_window': 16384,
    }]))
    get_settings.cache_clear()


def tool_call(name, arguments):
    return {'role': 'assistant', 'content': '', 'tool_calls': [{
        'id': name, 'type': 'function', 'function': {'name': name, 'arguments': arguments},
    }]}


def answer():
    return {'role': 'assistant', 'content': '현재 요청의 결과를 확인했습니다.', 'tool_calls': []}


def run_turn(client, headers, conversation, question, **options):
    response = send(client, headers, conversation, content=question, connection_id='local', model='local', **options)
    assert response.status_code == 200, response.text
    stream = events(response)
    assert stream[-1][0] == 'done', response.text[-3000:]
    return stream


def workspace(stream):
    return next(value for kind, value in stream if kind == 'workspace')


def publications(stream):
    return [json.loads(value['detail']) for kind, value in stream
            if kind == 'activity' and value['name'] == publishing.RESULT_TRACE]


def test_prompt_only_gitlab_checkout_edit_commit_push_and_open_merge_request(client, account, workspace_stack, monkeypatch):
    terminal, base_sha = workspace_stack
    configure_model(monkeypatch)
    enable_publishing(monkeypatch)
    original_request = transport.request
    commit_sha = 'b' * 40
    project = '/api/v4/projects/7'
    commits, calls = {}, []
    commit_path, mr_path = project + '/repository/commits', project + '/merge_requests'

    async def remote(provider, method, path, **kwargs):
        calls.append((method, path))
        if path == '/oauth/token':
            return (await original_request(provider, method, path, **kwargs)) | {'scope': config.PUBLISH_SCOPES}
        if path == project + '/repository/files/main.py/raw':
            assert kwargs['params']['ref'] == base_sha
            return ORIGINAL
        if path == project + '/repository/branches/main':
            return {'name': 'main'}
        if path.startswith(project + '/repository/branches/llm-mr-'):
            branch = path.rsplit('/', 1)[-1]
            if branch not in commits:
                raise transport.GitlabError('branch missing', 404)
            return {'name': branch, 'commit': commits[branch]}
        if path == commit_path:
            assert method == 'POST'
            payload = kwargs['payload']
            branch = payload['branch']
            assert branch.startswith('llm-mr-') and branch not in commits
            assert payload['start_sha'] == base_sha
            assert payload['actions'] == [{'action': 'update', 'file_path': 'main.py', 'content': EDITED}]
            commits[branch] = {'id': commit_sha, 'parent_ids': [base_sha], 'message': payload['commit_message']}
            return commits[branch]
        if path == mr_path:
            if method == 'GET':
                return []
            payload = kwargs['payload']
            assert method == 'POST' and payload['source_branch'] in commits
            assert payload['target_branch'] == 'main'
            return {'iid': 27, 'source_project_id': 7, 'target_project_id': 7,
                    'source_branch': payload['source_branch'], 'target_branch': 'main', 'state': 'opened'}
        return await original_request(provider, method, path, **kwargs)

    def remote_writes():
        return [path for method, path in calls if method == 'POST' and path.startswith('/api/v4/')]

    monkeypatch.setattr(transport, 'request', remote)
    headers = auth(account())
    connection = publish_connection(client, headers)
    conversation = create(client, headers)
    phase = 'read'

    async def model(_connection, _model, history, tools, _settings):
        names = {tool['function']['name'] for tool in tools}
        expected = {'push': publishing.PUSH_TOOL, 'mr': publishing.TOOL_NAME}.get(phase)
        assert names & PUBLICATION_TOOLS == ({expected} if expected else set())
        assert (prompt_gitlab.LOCAL_COMMIT_TOOL in names) == (phase == 'commit')
        last = history[-1]
        previous = last.get('tool_name')
        if phase == 'read':
            assert 'workspace_read_file' in names
            assert not {'workspace_write_file', 'terminal_exec'} & names
            return answer()
        if phase == 'edit':
            assert 'workspace_write_file' in names
            if not previous:
                return tool_call('workspace_read_file', {'path': 'main.py'})
            if previous == 'workspace_read_file':
                return tool_call('workspace_write_file', {'path': 'main.py', 'content': EDITED,
                    'expected_hash': json.loads(last['content'])['content_hash']})
            assert json.loads(last['content'])['path'] == 'main.py'
            return answer()
        if phase == 'commit':
            if not previous:
                return tool_call(prompt_gitlab.LOCAL_COMMIT_TOOL, CHANGES)
            assert json.loads(last['content'])['exit_code'] == 0
            return answer()
        if phase in {'push', 'mr'}:
            if not previous:
                args = CHANGES | {'title': '로그인 반환 타입 정리', 'description': ''} if phase == 'mr' else CHANGES
                return tool_call(expected, args)
            assert json.loads(last['content'])['status'] == ('pushed' if phase == 'push' else 'merge_request')
            return answer()
        assert phase == 'no_grant'
        assert not {'workspace_write_file', 'terminal_exec'} & names
        if not previous:
            return tool_call(publishing.PUSH_TOOL, CHANGES)
        assert json.loads(last['content'])['error']
        return answer()

    monkeypatch.setattr(providers, 'tool_reply', model)
    first = workspace(run_turn(client, headers, conversation, '@gitlab team/service 코드를 분석해 줘'))
    assert first['automatic'] and first['source']['commit'] == base_sha
    assert first['selection']['connection_id'] == connection and first['allow_execution'] is False

    phase = 'edit'
    edited = workspace(run_turn(client, headers, conversation, '@gitlab 로그인 반환값을 수정해 줘'))
    assert edited['allow_execution'] is True and edited['id'] != first['id']
    repo = terminal.store.repo(edited['id'])
    assert (repo / 'main.py').read_text() == EDITED
    assert git('rev-parse', 'HEAD', cwd=repo) == base_sha and remote_writes() == []

    phase = 'commit'
    committed = run_turn(client, headers, conversation, '현재 변경사항을 커밋해 줘')
    assert workspace(committed)['id'] == edited['id']
    assert git('rev-parse', 'HEAD', cwd=repo) != base_sha
    assert git('log', '-1', '--format=%s', cwd=repo) == CHANGES['commit_message']
    assert publications(committed) == [] and remote_writes() == []

    phase = 'push'
    pushed = run_turn(client, headers, conversation, '푸시만 해 줘')
    assert workspace(pushed)['id'] == edited['id']
    result, = publications(pushed)
    assert result['status'] == 'pushed' and result['commit_sha'] == commit_sha
    assert result['branch_url'].endswith('/-/tree/' + result['source_branch'])
    assert remote_writes() == [commit_path]
    assert all(path != mr_path for _, path in calls)

    phase = 'mr'
    opened = publications(run_turn(client, headers, conversation, '이 브랜치의 MR 생성해 줘'))[-1]
    assert opened['status'] == 'merge_request' and opened['source_branch'] == result['source_branch']
    assert remote_writes() == [commit_path, mr_path]

    phase = 'no_grant'
    denied = run_turn(client, headers, conversation,
        '@gitlab 변경 내용을 설명해 줘. 푸시하지 마.\n> 푸시해 줘\n```text\nMR 생성해 줘\n```')
    assert workspace(denied)['allow_execution'] is False
    assert publications(denied) == [] and remote_writes() == [commit_path, mr_path]
    assert any(kind == 'activity' and value['name'] == publishing.PUSH_TOOL and value['status'] == 'error'
               for kind, value in denied)

    other = create(client, headers)
    clarify = run_turn(client, headers, other, '푸시해 줘')
    assert not any(kind == 'workspace' for kind, _ in clarify)
    assert any(kind == 'delta' and '프로젝트' in value['content'] for kind, value in clarify)
    assert remote_writes() == [commit_path, mr_path]


def test_prompt_publish_still_requires_gitlab_write_scope(client, account, workspace_stack, monkeypatch):
    configure_model(monkeypatch)
    enable_publishing(monkeypatch)
    headers = auth(account())
    connect(client, headers)
    conversation = create(client, headers)
    request = transport.request

    async def no_writes(provider, method, path, **kwargs):
        assert not (method == 'POST' and path.startswith('/api/v4/'))
        return await request(provider, method, path, **kwargs)

    monkeypatch.setattr(transport, 'request', no_writes)
    stream = events(send(client, headers, conversation, content='@gitlab team/service 푸시해 줘',
                         connection_id='local', model='local'))
    assert stream[-1][0] == 'error'
    assert '쓰기 권한' in stream[-1][1]['detail'] or '권한' in stream[-1][1]['detail']
    assert not any(kind == 'workspace' for kind, _ in stream)
