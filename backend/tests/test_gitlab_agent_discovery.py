"""일반 채팅의 GitLab 자동 탐색·API 쓰기·여러 저장소 분석 계약."""

import json

import pytest
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.assistant import routing
from app.modules.chat import providers, scheduler
from app.modules.integrations import actions, config, transport
from app.modules.integrations.models import AppConnection, GitlabWriteReceipt
from app.modules.workspaces.models import Workspace
from test_chat import auth, create, events, send
from test_gitlab_apps import SHA, connect, gitlab
from test_gitlab_publishing import enable_publishing, publish_connection
from test_prompt_gitlab import configure_model, tool_call
from test_workspace_flow import git, workspace_stack


def normal_chat(client, headers, conversation, question):
    response = send(client, headers, conversation, question, connection_id='local', model='local')
    assert response.status_code == 200, response.text
    return response, events(response)


def last_message(client, headers, conversation):
    return client.get(f'/chat/conversations/{conversation}', headers=headers).json()['messages'][-1]


def tool_results(history):
    return [item for item in history if item['role'] == 'tool']


def final_answer(content):
    return {'role': 'assistant', 'content': content, 'tool_calls': []}


def test_korean_project_search_reads_real_lines_without_a_picker_or_terminal(client, account, gitlab, monkeypatch):
    configure_model(monkeypatch)
    monkeypatch.setenv('TERMINAL_MCP_URL', '')
    get_settings.cache_clear()
    headers = auth(account())
    connection = connect(client, headers)
    conversation = create(client, headers)
    original = transport.request
    queried = []

    async def remote(provider, method, path, **kwargs):
        queried.append((method, path, kwargs.get('params')))
        if path == '/api/v4/projects':
            assert kwargs['params']['search'] == '로그인'
        if path == '/api/v4/projects/7/search':
            assert kwargs['params']['scope'] == 'blobs' and kwargs['params']['search'] == 'login'
            return [{'path': 'app/main.py', 'startline': 3, 'ref': 'main',
                'data': 'def login(user): return user.is_active'}]
        return await original(provider, method, path, **kwargs)

    monkeypatch.setattr(transport, 'request', remote)

    async def model(_connection, _model, history, tools, _settings):
        names = {item['function']['name'] for item in tools}
        assert 'gitlab_query' in names
        assert not {'terminal_exec', 'workspace_write_file', 'gitlab_create_issue'} & names
        results = tool_results(history)
        if not results:
            return tool_call('gitlab_query', {'operation': 'projects', 'query': '로그인'})
        result = json.loads(results[-1]['content'])
        if len(results) == 1:
            assert result['items'][0]['id'] == 7
            assert result['items'][0]['connection_id'] == connection
            return tool_call('gitlab_query', {'operation': 'search_code', 'project_id': 7,
                'ref': 'main', 'query': 'login'})
        if len(results) == 2:
            assert result['items'][0]['path'] == 'app/main.py'
            return tool_call('gitlab_query', {'operation': 'file', 'project_id': 7,
                'ref': 'main', 'path': 'app/main.py', 'start_line': 3, 'end_line': 4})
        assert result['sources'][0]['commit'] == SHA
        assert result['sources'][0]['start_line'] == 3
        assert 'return user.is_active' in result['content']
        return final_answer('로그인 관련 프로젝트는 team/service이며 login 함수는 활성 계정을 확인합니다. [GL1]')

    monkeypatch.setattr(providers, 'tool_reply', model)
    monkeypatch.setattr(providers, 'structured_reply', lambda *args, **kwargs:
        pytest.fail('명확한 API 탐색은 추가 라우팅 모델 호출을 하지 않아야 합니다.'))
    response, stream = normal_chat(client, headers, conversation, '내 GitLab에서 로그인 관련 프로젝트 찾아줘.')
    assert stream[-1][0] == 'done', response.text[-4000:]
    message = last_message(client, headers, conversation)
    assert message['routing']['route'] == 'gitlab_api'
    source, = message['sources']
    assert source['commit'] == SHA and source['path'] == 'app/main.py'
    assert source['start_line'] == 3 and source['end_line'] == 4
    assert source['url'].endswith(f'/-/blob/{SHA}/app/main.py#L3-4')
    assert 'local-access-' not in response.text and 'local-test-client-secret' not in response.text
    with client.db_factory() as db:
        assert db.scalar(select(Workspace)) is None
    assert any(path.endswith('/search') for _, path, _ in queried)


def test_unconnected_gitlab_search_requests_connection_without_calling_a_model(client, account, gitlab, monkeypatch):
    configure_model(monkeypatch)
    headers = auth(account())
    conversation = create(client, headers)

    async def forbidden(*args, **kwargs):
        pytest.fail('미연결 GitLab 검색을 모델의 가짜 결과로 대체하면 안 됩니다.')

    monkeypatch.setattr(providers, 'tool_reply', forbidden)
    monkeypatch.setattr(providers, 'structured_reply', forbidden)
    monkeypatch.setattr(providers, 'stream_reply', forbidden)
    response, stream = normal_chat(client, headers, conversation, '내 GitLab 프로젝트 목록을 찾아줘.')
    assert stream[-1][0] == 'error', response.text[-2500:]
    message = last_message(client, headers, conversation)
    assert '연결' in message['error']
    assert message['recovery']['code'] == 'app_connection'
    assert not message['sources']


def test_natural_compare_and_commit_history_use_fixed_gitlab_read_apis(client, account, gitlab, monkeypatch):
    configure_model(monkeypatch)
    headers = auth(account())
    connect(client, headers)
    conversation = create(client, headers)
    original = transport.request
    queried = []
    head = 'b' * 40

    async def remote(provider, method, path, **kwargs):
        queried.append((method, path, kwargs.get('params')))
        assert method == 'GET'
        if path == '/api/v4/projects/7/repository/compare':
            assert kwargs['params'] == {'from': 'main', 'to': 'feature/login', 'straight': 'true'}
            return {'commits': [{'id': head, 'title': '로그인 검사 수정'}],
                'diffs': [{'old_path': 'app/main.py', 'new_path': 'app/main.py',
                    'diff': '@@ -3,2 +3,2 @@\n def login(user):\n-    return True\n+    return user.is_active\n'}]}
        if path == '/api/v4/projects/7/repository/commits':
            assert kwargs['params']['ref_name'] == 'feature/login'
            return [{'id': head, 'title': '로그인 검사 수정', 'committed_date': '2026-11-20T01:00:00Z'}]
        return await original(provider, method, path, **kwargs)

    monkeypatch.setattr(transport, 'request', remote)

    async def model(_connection, _model, history, tools, _settings):
        assert 'gitlab_query' in {item['function']['name'] for item in tools}
        results = tool_results(history)
        if not results:
            return tool_call('gitlab_query', {'operation': 'compare', 'project_id': 7,
                'from_ref': 'main', 'to_ref': 'feature/login'})
        result = json.loads(results[-1]['content'])
        if len(results) == 1:
            assert 'return user.is_active' in result['files'][0]['patch']
            assert result['commits'][0]['sha'] == head
            return tool_call('gitlab_query', {'operation': 'commits', 'project_id': 7, 'ref': 'feature/login'})
        assert result['items'][0]['sha'] == head
        return final_answer(f'feature/login 브랜치는 활성 사용자 검사를 추가했습니다. 확인한 커밋은 {head}입니다.')

    monkeypatch.setattr(providers, 'tool_reply', model)
    response, stream = normal_chat(client, headers, conversation,
        '@gitlab team/service 저장소의 main과 feature/login 차이와 최근 커밋 이력을 알려줘.')
    assert stream[-1][0] == 'done', response.text[-3000:]
    assert any(path.endswith('/repository/compare') for _, path, _ in queried)
    assert any(path.endswith('/repository/commits') for _, path, _ in queried)
    assert not any(kind == 'workspace' for kind, _ in stream)


@pytest.mark.parametrize('revoke_at', ['after_read', 'after_admission'])
def test_revoked_source_is_not_sent_to_a_subsequent_model_call(client, account, gitlab, monkeypatch, revoke_at):
    configure_model(monkeypatch)
    headers = auth(account())
    connection = connect(client, headers)
    conversation = create(client, headers)
    original = actions.GitlabTools.call
    original_poll = scheduler.poll
    model_calls = []
    source_ready = False

    def revoke(db):
        db.get(AppConnection, connection).status = 'needs_reconnect'

    async def revoke_after_read(tools, name, args):
        nonlocal source_ready
        result = await original(tools, name, args)
        if name == actions.READ_TOOL and args.get('operation') == 'file':
            source_ready = True
            # 첫 조회는 허용되었지만 다음 추론 호출 전에 연결이 해제된 경합이다.
            if revoke_at == 'after_read':
                with client.db_factory() as db:
                    revoke(db)
                    db.commit()
        return result

    def admission(db, *args, **kwargs):
        result = original_poll(db, *args, **kwargs)
        if revoke_at == 'after_admission' and source_ready and result[0] == 'running':
            # 대기 전에 유효했던 자료도 추론 슬롯을 얻은 뒤 다시 검사해야 한다.
            revoke(db)
        return result

    async def model(_connection, _model, history, tools, _settings):
        model_calls.append(history)
        if not tool_results(history):
            return tool_call(actions.READ_TOOL, {'operation': 'file', 'project_id': 7,
                'path': 'app/main.py', 'ref': 'main'})
        return final_answer('권한 회수 뒤의 소스는 새 추론 입력으로 전달하면 안 됩니다.')

    monkeypatch.setattr(actions.GitlabTools, 'call', revoke_after_read)
    monkeypatch.setattr(scheduler, 'poll', admission)
    monkeypatch.setattr(providers, 'tool_reply', model)
    response, stream = normal_chat(client, headers, conversation,
        '@gitlab team/service 저장소의 login 파일을 검색해줘.')
    assert stream[-1][0] == 'error', response.text[-2500:]
    assert len(model_calls) == 1
    assert not any(kind == 'delta' for kind, _ in stream)


def test_explicit_issue_creation_in_chat_uses_scope_and_one_confirmed_write_receipt(client, account, gitlab, monkeypatch):
    configure_model(monkeypatch)
    enable_publishing(monkeypatch)
    original = transport.request
    writes = []

    async def remote(provider, method, path, **kwargs):
        if path == '/oauth/token':
            return (await original(provider, method, path, **kwargs)) | {'scope': config.PUBLISH_SCOPES}
        if path == '/api/v4/projects/7/issues' and method == 'POST':
            assert kwargs['payload']['title'] == '로그인 오류 조사'
            writes.append(path)
            return {'iid': 9, 'title': '로그인 오류 조사'}
        if path == '/api/v4/projects/7/issues/9':
            return {'iid': 9, 'title': '로그인 오류 조사', 'state': 'opened',
                'description': '재현 방법 정리', 'updated_at': '2026-11-20T01:00:00Z'}
        return await original(provider, method, path, **kwargs)

    monkeypatch.setattr(transport, 'request', remote)
    headers = auth(account())
    publish_connection(client, headers)
    conversation = create(client, headers)

    async def model(_connection, _model, history, tools, _settings):
        assert actions.CREATE_ISSUE in {item['function']['name'] for item in tools}
        results = tool_results(history)
        args = {'project_id': 7, 'title': '로그인 오류 조사', 'description': '재현 방법 정리'}
        if len(results) < 2:
            if results:
                assert json.loads(results[-1]['content'])['iid'] == 9
            return tool_call(actions.CREATE_ISSUE, args)
        result = json.loads(results[-1]['content'])
        if len(results) == 2:
            assert result['duplicate_prevented'] is True
            return tool_call(actions.READ_TOOL, {'operation': 'issue', 'project_id': 7, 'iid': 9})
        assert result['issue']['title'] == '로그인 오류 조사'
        return final_answer('로그인 오류 조사 이슈 #9를 생성하고 다시 조회했습니다. '
            'https://gitlab.example.internal/team/service/-/issues/9')

    monkeypatch.setattr(providers, 'tool_reply', model)
    response, stream = normal_chat(client, headers, conversation,
        '@gitlab team/service 프로젝트에 로그인 오류 조사 이슈를 생성해줘.')
    assert stream[-1][0] == 'done', response.text[-4000:]
    assert writes == ['/api/v4/projects/7/issues']
    with client.db_factory() as db:
        receipt, = db.scalars(select(GitlabWriteReceipt)).all()
        assert receipt.status == 'confirmed' and receipt.action == actions.CREATE_ISSUE
        assert json.loads(receipt.result_json)['iid'] == 9


def test_multiple_repositories_are_cloned_readonly_and_cited_separately(client, account, workspace_stack, monkeypatch, tmp_path):
    _, first_sha = workspace_stack
    configure_model(monkeypatch)
    original = transport.request
    repositories = tmp_path / 'repositories' / 'team'
    other_source = tmp_path / 'other-source'
    git('clone', str(repositories / 'service.git'), str(other_source), cwd=tmp_path)
    (other_source / 'main.py').write_text('def login(user):\n    return user.enabled and user.verified\n')
    git('add', '.', cwd=other_source)
    git('-c', 'user.name=Local Test', '-c', 'user.email=test@example.invalid',
        'commit', '-m', 'other authentication', cwd=other_source)
    second_sha = git('rev-parse', 'HEAD', cwd=other_source)
    git('clone', '--bare', str(other_source), str(repositories / 'other.git'), cwd=tmp_path)

    async def remote(provider, method, path, **kwargs):
        if path == '/api/v4/projects':
            return [{'id': 7, 'path_with_namespace': 'team/service', 'default_branch': 'main'},
                {'id': 8, 'path_with_namespace': 'team/other', 'default_branch': 'main'}]
        if path == '/api/v4/projects/8':
            return {'id': 8, 'path_with_namespace': 'team/other', 'default_branch': 'main'}
        if path.startswith('/api/v4/projects/8/repository/commits/'):
            return {'id': second_sha}
        return await original(provider, method, path, **kwargs)

    monkeypatch.setattr(transport, 'request', remote)
    headers = auth(account())
    connection = connect(client, headers)
    conversation = create(client, headers)

    async def model(_connection, _model, history, tools, _settings):
        names = {item['function']['name'] for item in tools}
        assert 'gitlab_analyze_repository' in names
        assert not {'terminal_exec', 'workspace_write_file'} & names
        results = tool_results(history)
        if len(results) < 2:
            return tool_call('gitlab_analyze_repository', {'connection_id': connection,
                'project_id': 7 if not results else 8, 'ref': 'main', 'query': 'login'})
        values = [json.loads(item['content']) for item in results]
        assert {value['project'] for value in values} == {'team/service', 'team/other'}
        assert all(value['sections'] for value in values), values
        sources = [source for value in values for source in value['sources']]
        citations = ' '.join(f'[{source["id"]}]' for source in sources)
        return final_answer('조회한 두 저장소에서 로그인 구현의 조건이 다릅니다. 정적 소스 발췌에 따른 비교입니다. ' + citations)

    monkeypatch.setattr(providers, 'tool_reply', model)
    response, stream = normal_chat(client, headers, conversation,
        '@gitlab team/service와 team/other 소스코드의 login 함수를 분석해줘.')
    assert stream[-1][0] == 'done', response.text[-4000:]
    message = last_message(client, headers, conversation)
    assert {source['project'] for source in message['sources']} == {'team/service', 'team/other'}
    assert {source['commit'] for source in message['sources']} == {first_sha, second_sha}
    assert len({source['id'] for source in message['sources']}) == len(message['sources'])
    with client.db_factory() as db:
        workspaces = list(db.scalars(select(Workspace)))
        assert len(workspaces) == 2 and all(not row.allow_execution and row.status == 'ready' for row in workspaces)
