"""원격 변경의 새 읽기 스냅샷과 편집 중인 작업공간 보존을 실제 Git으로 검증한다."""

import asyncio
import json
from uuid import UUID

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from app.modules.assistant import routing
from app.modules.integrations import publishing, transport
from app.modules.integrations.models import AppConnection
from app.modules.integrations.service import GitlabClient
from app.modules.integrations.tools import GitlabSelection
from app.modules.workspaces import service
from app.modules.workspaces.models import Workspace
from test_chat import auth, create
from test_gitlab_apps import connect, gitlab
from test_workspace_flow import git, workspace_stack


ORIGINAL = 'def login(user):\n    return user.active\n'
UPDATED = ('def login(user):\n    return user.active and user.verified\n\n'
    'def authenticate_session(user):\n    return login(user)\n')


@pytest.fixture
def mutable_remote(workspace_stack, monkeypatch, tmp_path):
    terminal, sha = workspace_stack
    upstream = transport.request
    head = {'sha': sha}

    async def remote(provider, method, path, **kwargs):
        if path.startswith('/api/v4/projects/7/repository/commits/'):
            # 불변 SHA와 움직이는 브랜치 이름을 구분한다.
            return {'id': sha if path.rsplit('/', 1)[-1] == sha else head['sha']}
        return await upstream(provider, method, path, **kwargs)

    monkeypatch.setattr(transport, 'request', remote)

    def advance():
        source = tmp_path / 'source'
        (source / 'main.py').write_text(UPDATED)
        git('add', '.', cwd=source)
        git('-c', 'user.name=Local Test', '-c', 'user.email=test@example.invalid',
            'commit', '-m', 'remote authentication update', cwd=source)
        head['sha'] = git('rev-parse', 'HEAD', cwd=source)
        git('push', str(tmp_path / 'repositories' / 'team' / 'service.git'), 'main', cwd=source)
        assert head['sha'] != sha
        return head['sha']

    return terminal, sha, advance


def environment(client, account, *, username='alice', mode='workspace'):
    identity = account(username)
    headers = auth(identity)
    connection = connect(client, headers)
    conversation = create(client, headers)
    with client.db_factory() as db:
        bind = db.get_bind()
    selection = GitlabSelection(app='gitlab', connection_id=connection, project_id=7, ref='main', mode=mode)
    return bind, identity['user']['subject'], conversation, selection


def ensure(scope, selection=None):
    bind, user, conversation, current = scope
    return asyncio.run(service.ensure(bind, user, conversation, selection or current))


def invoke(scope, row, name, args):
    return asyncio.run(service.invoke(scope[0], row, name, args))


def test_remote_branch_change_creates_fresh_readonly_code_index_and_preserves_old_snapshot(
        client, account, mutable_remote):
    terminal, old_sha, advance = mutable_remote
    scope = environment(client, account)
    first = ensure(scope)
    before = invoke(scope, first, 'repository_index', {})
    assert not invoke(scope, first, 'code_definition', {'name': 'authenticate_session'})['definitions']
    new_sha = advance()
    # 명시적으로 이전 작업공간을 보냈어도 원격 브랜치의 최신 SHA를 확인한다.
    selection = scope[3].model_copy(update={'workspace_id': UUID(first.id)})
    second = ensure(scope, selection)
    after = invoke(scope, second, 'repository_index', {})
    definitions = invoke(scope, second, 'code_definition', {'name': 'authenticate_session'})['definitions']
    assert second.id != first.id
    assert json.loads(first.source_json)['commit'] == old_sha
    assert json.loads(second.source_json)['commit'] == new_sha
    assert after['snapshot_hash'] != before['snapshot_hash']
    assert definitions and definitions[0]['path'] == 'main.py'
    assert (terminal.store.repo(first.id) / 'main.py').read_text() == ORIGINAL
    assert (terminal.store.repo(second.id) / 'main.py').read_text() == UPDATED
    assert git('rev-parse', 'HEAD', cwd=terminal.store.repo(first.id)) == old_sha
    assert git('rev-parse', 'HEAD', cwd=terminal.store.repo(second.id)) == new_sha
    with client.db_factory() as db:
        # created_at은 초 단위다. 같은 초에 생성된 복제본도 SHA로 올바르게 재사용해야 한다.
        for workspace in db.scalars(select(Workspace)):
            workspace.created_at = 1795156000
        db.commit()
    # 이어지는 새 요청에서 같은 최신 스냅샷을 재사용하며 복제본을 계속 만들지 않는다.
    assert ensure(scope).id == second.id
    with client.db_factory() as db:
        assert len(list(db.scalars(select(Workspace)))) == 2


def test_unchanged_remote_reuses_the_workspace_and_its_index_cache(client, account, mutable_remote):
    _, _, _ = mutable_remote
    scope = environment(client, account)
    first = ensure(scope)
    before = invoke(scope, first, 'repository_index', {})
    second = ensure(scope)
    after = invoke(scope, second, 'repository_index', {})
    assert second.id == first.id
    assert after['snapshot_hash'] == before['snapshot_hash']
    assert after['cache']['parsed'] == 0
    assert after['cache']['hits'] == after['indexed_files']
    with client.db_factory() as db:
        assert len(list(db.scalars(select(Workspace)))) == 1


def test_execution_workspace_keeps_dirty_files_and_original_publication_base_when_remote_advances(
        client, account, mutable_remote, monkeypatch):
    terminal, old_sha, advance = mutable_remote
    scope = environment(client, account, mode='execute')
    first = ensure(scope)
    local_change = 'def login(user):\n    return bool(user.active)\n'
    (terminal.store.repo(first.id) / 'main.py').write_text(local_change)
    new_sha = advance()
    second = ensure(scope)
    assert second.id == first.id
    assert json.loads(second.source_json)['commit'] == old_sha != new_sha
    assert (terminal.store.repo(second.id) / 'main.py').read_text() == local_change
    assert git('rev-parse', 'HEAD', cwd=terminal.store.repo(second.id)) == old_sha
    assert git('status', '--porcelain', cwd=terminal.store.repo(second.id)) == 'M main.py'
    original = transport.request
    reads = []

    async def remote(provider, method, path, **kwargs):
        if path == '/api/v4/projects/7/repository/files/main.py/raw':
            assert method == 'GET' and kwargs['params'] == {'ref': old_sha}
            reads.append(path)
            return ORIGINAL
        assert method == 'GET'
        return await original(provider, method, path, **kwargs)

    monkeypatch.setattr(transport, 'request', remote)
    upstream = GitlabClient(scope[0], scope[1], str(scope[3].connection_id))
    changes = asyncio.run(publishing.file_actions(upstream, '/projects/7', old_sha, second, ['main.py']))
    assert changes == [{'action': 'update', 'file_path': 'main.py', 'content': local_change}]
    assert reads == ['/api/v4/projects/7/repository/files/main.py/raw']


@pytest.mark.parametrize('source_kind', ['workspace', 'api'])
def test_other_accounts_sources_cannot_be_accepted_as_additional_evidence(client, account, mutable_remote, source_kind):
    _, _, _ = mutable_remote
    alice = environment(client, account)
    own = ensure(alice)
    bob = environment(client, account, username='bob')
    other = ensure(bob)
    selection = alice[3].model_copy(update={'workspace_id': UUID(own.id)})
    sources = [{'app': 'gitlab', 'project_id': 7,
        'connection_id': str(bob[3].connection_id), 'commit': json.loads(other.source_json)['commit'],
        'connection_generation': other.connection_generation,
        **({'workspace_id': other.id} if source_kind == 'workspace' else {})}]
    with pytest.raises(HTTPException) as caught:
        asyncio.run(routing.verify(alice[0], alice[1], alice[2], selection, sources))
    assert caught.value.status_code == 404


@pytest.mark.parametrize('revocation', ['account', 'generation', 'project'])
@pytest.mark.parametrize('source_kind', ['workspace', 'api'])
def test_all_sources_are_reauthorized_even_without_an_active_selection(
        client, account, mutable_remote, monkeypatch, revocation, source_kind):
    _, _, _ = mutable_remote
    scope = environment(client, account)
    row = ensure(scope)
    sources = [{'app': 'gitlab',
        'connection_id': str(scope[3].connection_id), 'project_id': 7,
        'commit': json.loads(row.source_json)['commit'], 'connection_generation': row.connection_generation,
        **({'workspace_id': row.id} if source_kind == 'workspace' else {})}]
    asyncio.run(routing.verify(scope[0], scope[1], scope[2], None, sources))
    if revocation in {'account', 'generation'}:
        with client.db_factory() as db:
            connection = db.get(AppConnection, str(scope[3].connection_id))
            if revocation == 'account':
                connection.status = 'needs_reconnect'
            else:
                connection.generation += 1
            db.commit()
    else:
        original = transport.request

        async def remote(provider, method, path, **kwargs):
            if path == '/api/v4/projects/7':
                raise transport.GitlabError('프로젝트 권한이 회수되었습니다.', 403)
            return await original(provider, method, path, **kwargs)

        monkeypatch.setattr(transport, 'request', remote)
    with pytest.raises((HTTPException, transport.GitlabError)) as caught:
        asyncio.run(routing.verify(scope[0], scope[1], scope[2], None, sources))
    status = caught.value.status_code if isinstance(caught.value, HTTPException) else caught.value.status
    assert status == (403 if revocation == 'project' else 409)
