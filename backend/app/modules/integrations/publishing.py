"""Publish selected workspace files through the connected GitLab account."""

import asyncio
import base64
from hashlib import sha256
import json
import re
import time
from urllib.parse import quote

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.chat.models import Conversation
from app.modules.workspaces import client as workspace_client, service as workspaces
from app.modules.workspaces.models import Workspace
from terminal_mcp.analysis import PUBLISH_MAX_BYTES, PUBLISH_MAX_FILES, allowed
from . import config, merge_requests
from .service import GitlabClient, require_publish
from .transport import GitlabError

TOOL_NAME = 'gitlab_open_merge_request'
UPDATE_TOOL = 'gitlab_update_merge_request'
RESULT_TRACE = 'gitlab_publication'


class Changes(BaseModel):
    model_config = ConfigDict(extra='forbid')
    paths: list[str] = Field(min_length=1, max_length=PUBLISH_MAX_FILES)
    commit_message: str = Field(min_length=1, max_length=500)

    @field_validator('paths')
    @classmethod
    def paths_are_eligible(cls, values):
        if len(set(values)) != len(values):
            raise ValueError('같은 파일을 두 번 지정할 수 없습니다.')
        for path in values:
            config.validate_path(path)
            if not allowed(path):
                raise ValueError('작업공간에서 지원하는 텍스트 파일만 게시할 수 있습니다.')
        return sorted(values)

    @field_validator('commit_message')
    @classmethod
    def nonempty(cls, value):
        if not value.strip():
            raise ValueError('커밋 메시지를 입력하세요.')
        return value.strip()


class Draft(Changes):
    title: str = Field(min_length=1, max_length=250)
    description: str = Field(max_length=8000)

    @field_validator('title')
    @classmethod
    def title_nonempty(cls, value):
        if not value.strip():
            raise ValueError('MR 제목을 입력하세요.')
        return value.strip()


def tool(*, existing=False):
    if existing:
        return {'type': 'function', 'function': {
            'name': UPDATE_TOOL,
            'description': ('Commit selected changed files to the selected existing MR source branch. '
                'Call only if the user asks to publish, after checking reviews, CI, diff and local test results.'),
            'parameters': workspace_client.lean_schema(Changes.model_json_schema()),
        }}
    return {'type': 'function', 'function': {
        'name': TOOL_NAME,
        'description': (
            'Create a commit and GitLab merge request from the requested changed workspace files. '
            'Call only if the user asks to publish, after reviewing the changes and test results. '
            'Provide only the paths for this request. The app chooses the source branch and uses the selected target branch.'
        ),
        'parameters': workspace_client.lean_schema(Draft.model_json_schema()),
    }}


def parse(arguments, *, existing=False):
    try:
        schema = Changes if existing else Draft
        return schema.model_validate(arguments)
    except ValidationError:
        detail = '변경 파일 목록과 커밋 메시지를 확인해 주세요.' if existing else 'MR의 변경 파일 목록, 커밋 메시지, 제목과 설명을 확인해 주세요.'
        raise HTTPException(422, detail) from None


def check_active(bind, workspace, request_id):
    with Session(bind) as db:
        current = db.get(Workspace, workspace.id)
        conversation = db.get(Conversation, workspace.conversation_id)
        if (not current or current.status != 'ready' or not current.allow_execution
                or not get_settings().terminal_execution_enabled or not conversation
                or conversation.generation_id != request_id or conversation.cancel_requested
                or conversation.generation_until <= time.time()):
            raise HTTPException(409, '작업이 중단되었거나 작업공간이 만료되었습니다.')
        account = workspaces.authorize(db, current)
        require_publish(account, config.instance(account.provider_id))


async def get_optional(upstream, path, **options):
    try:
        return await upstream.get(path, **options)
    except GitlabError as error:
        if error.status != 404:
            raise
        return None


async def file_actions(upstream, project, base, workspace, paths, *, guard_updates=False):
    exported = await workspace_client.call(workspace, 'workspace_export_files',
        {'workspace_id': workspace.id, 'paths': paths}, provision=True)
    entries = exported.get('files') if isinstance(exported, dict) else None
    if not isinstance(entries, list) or len(entries) != len(paths):
        raise HTTPException(502, 'Terminal MCP의 게시 파일 응답을 확인하세요. API와 Terminal MCP를 함께 업데이트하세요.')

    actions, size = [], 0
    for path, entry in zip(paths, entries, strict=True):
        if not isinstance(entry, dict) or entry.get('path') != path or 'content' not in entry:
            raise HTTPException(502, 'Terminal MCP의 게시 파일 경로가 일치하지 않습니다.')
        content = entry['content']
        if content is not None:
            if not isinstance(content, str) or '\x00' in content:
                raise HTTPException(422, 'UTF-8 텍스트 파일만 게시할 수 있습니다.')
            size += len(content.encode())
            if size > PUBLISH_MAX_BYTES:
                raise HTTPException(422, '게시 파일의 전체 크기가 1MiB를 초과합니다.')
        file_path = project + f'/repository/files/{quote(path, safe="")}'
        params = {'ref': base}
        last_commit = None
        if guard_updates:
            snapshot = await get_optional(upstream, file_path, params=params, limit=2 * PUBLISH_MAX_BYTES)
            previous = None
            if snapshot is not None:
                try:
                    last_commit = snapshot['last_commit_id']
                    if snapshot['encoding'] != 'base64' or not valid_sha(last_commit):
                        raise ValueError()
                    data = base64.b64decode(''.join(snapshot['content'].split()), validate=True)
                    if b'\x00' in data or len(data) != snapshot['size']:
                        raise ValueError()
                    previous = data.decode()
                except (KeyError, TypeError, AttributeError, ValueError):
                    raise HTTPException(502, 'GitLab에서 파일의 원본 내용과 마지막 커밋을 확인할 수 없습니다.') from None
        else:
            previous = await get_optional(upstream, file_path + '/raw',
                params=params, raw=True, limit=PUBLISH_MAX_BYTES)
        if previous == content:
            continue
        if content is None:
            actions.append({'action': 'delete', 'file_path': path})
        else:
            actions.append({'action': 'create' if previous is None else 'update', 'file_path': path, 'content': content})
        if last_commit:
            actions[-1]['last_commit_id'] = last_commit
    if not actions:
        raise HTTPException(422, '선택한 파일에 원본 커밋과 다른 내용이 없습니다.')
    return actions


def valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r'[a-f0-9]{40}', value) is not None


def publication_commit(commit, base, marker):
    if (not isinstance(commit, dict) or not valid_sha(commit.get('id')) or commit.get('parent_ids') != [base]
            or not isinstance(commit.get('message'), str) or not commit['message'].rstrip().endswith('\n' + marker)):
        raise HTTPException(409, '게시 브랜치의 커밋을 확인할 수 없습니다. GitLab에서 브랜치 상태를 확인하세요.')
    return commit['id']


def matches_request(merge_request, project_id, branch, target):
    return (isinstance(merge_request, dict) and type(merge_request.get('iid')) is int and merge_request['iid'] > 0
        and merge_request.get('source_project_id') == project_id and merge_request.get('target_project_id') == project_id
        and merge_request.get('source_branch') == branch and merge_request.get('target_branch') == target)


async def existing_request(upstream, project, project_id, branch, target):
    values = await upstream.get(project + '/merge_requests', params={
        'scope': 'all', 'source_branch': branch, 'target_branch': target, 'per_page': 20,
    }, limit=512 * 1024)
    if not isinstance(values, list):
        raise HTTPException(502, 'GitLab MR 목록 응답 형식을 확인하세요.')
    return next((value for value in values if matches_request(value, project_id, branch, target)), None)


def request_result(merge_request, link, branch, target):
    return {
        'status': 'merge_request',
        'url': link + '/-/merge_requests/' + str(merge_request['iid']),
        'iid': merge_request['iid'],
        'state': merge_request.get('state', 'opened'),
        'source_branch': branch,
        'target_branch': target,
    }


async def publish(bind, workspace, request_id, draft):
    """Yield the commit once it is confirmed, then report the merge request."""
    def before_write():
        check_active(bind, workspace, request_id)

    before_write()
    base = json.loads(workspace.source_json).get('commit')
    if not valid_sha(base):
        raise HTTPException(422, 'GitLab 게시에는 40자리 원본 커밋 SHA가 필요합니다.')
    upstream = GitlabClient(bind, workspace.user_id, workspace.connection_id)
    upstream.generation = workspace.connection_generation
    project = f'/projects/{workspace.project_id}'
    try:
        repository = await upstream.get(project)
        try:
            namespace = config.validate_path(repository['path_with_namespace'])
            target = repository.get('default_branch') if workspace.ref == 'HEAD' else workspace.ref
            if repository.get('id') != workspace.project_id or not isinstance(target, str) or not 1 <= len(target) <= 200:
                raise ValueError()
        except (KeyError, TypeError, AttributeError, ValueError):
            raise HTTPException(502, 'GitLab 프로젝트와 대상 브랜치를 확인할 수 없습니다.') from None
        target_info = await get_optional(upstream, project + f'/repository/branches/{quote(target, safe="")}')
        if not isinstance(target_info, dict) or target_info.get('name') != target:
            raise HTTPException(422, 'MR을 만들 대상 브랜치를 GitLab 선택 화면에서 다시 지정하세요.')

        actions = await file_actions(upstream, project, base, workspace, draft.paths)
        digest = sha256(json.dumps([workspace.id, base, target, actions], ensure_ascii=False,
            separators=(',', ':'), sort_keys=True).encode()).hexdigest()
        branch = f'llm-mr-{digest[:24]}'
        marker = f'LLM-Publication: {digest}'
        link = upstream.provider.base_url + '/' + quote(namespace, safe='/')
        existing = await existing_request(upstream, project, workspace.project_id, branch, target)
        if existing:
            yield request_result(existing, link, branch, target)
            return

        branch_path = project + '/repository/branches/' + branch
        branch_info = await get_optional(upstream, branch_path)
        if branch_info is not None:
            commit_sha = publication_commit(branch_info.get('commit') if isinstance(branch_info, dict) else None, base, marker)
        else:
            before_write()
            try:
                commit = await upstream.post(project + '/repository/commits', {
                    'branch': branch,
                    'start_sha': base,
                    'commit_message': draft.commit_message + '\n\n' + marker,
                    'actions': actions,
                }, before_write=before_write)
            except GitlabError:
                # A timeout can occur after GitLab accepted the commit. Reconcile by reading the branch.
                branch_info = await get_optional(upstream, branch_path)
                if not isinstance(branch_info, dict):
                    raise
                commit = branch_info.get('commit')
            commit_sha = publication_commit(commit, base, marker)

        committed = {'status': 'committed', 'url': link + '/-/commit/' + commit_sha,
            'commit_sha': commit_sha, 'source_branch': branch, 'target_branch': target}
        yield committed
        before_write()
        try:
            merge_request = await upstream.post(project + '/merge_requests', {
                'source_branch': branch, 'target_branch': target, 'title': draft.title,
                'description': draft.description,
            }, before_write=before_write)
            if not matches_request(merge_request, workspace.project_id, branch, target):
                raise GitlabError('GitLab MR 생성 응답을 확인할 수 없습니다.')
        except GitlabError as error:
            # If the response was lost, recover the MR by its exact source/target before reporting failure.
            try:
                merge_request = await existing_request(upstream, project, workspace.project_id, branch, target)
            except GitlabError:
                yield committed | {'error': 'GitLab에서 MR 생성 상태를 확인할 수 없습니다.'}
                return
            if merge_request is None:
                yield committed | {'error': str(error)}
                return
        yield request_result(merge_request, link, branch, target)
    except GitlabError as error:
        status = error.status if error.status in {403, 404, 413, 422, 429} else 502
        raise HTTPException(status, str(error)) from None


async def update_existing(bind, workspace, request_id, mr, draft, *, confirmed):
    """Append to the verified MR source branch, including a fork's source project."""
    def before_write():
        check_active(bind, workspace, request_id)

    before_write()
    mr.require_source(workspace.project_id, workspace.ref)
    source = json.loads(workspace.source_json)
    base = source.get('commit')
    if base != mr.sha:
        raise HTTPException(409, '작업공간이 현재 MR 커밋과 일치하지 않습니다. 다시 연결하세요.')
    upstream = GitlabClient(bind, workspace.user_id, workspace.connection_id)
    upstream.generation = workspace.connection_generation
    project = f'/projects/{workspace.project_id}'
    branch_path = project + '/repository/branches/' + quote(mr.source_branch, safe='')
    try:
        actions = await file_actions(upstream, project, base, workspace, draft.paths, guard_updates=True)
        current = await merge_requests.load(upstream, mr.reference.project_id, mr.reference.iid)
        current.require_source(workspace.project_id, workspace.ref)
        branch = await get_optional(upstream, branch_path)
        head = branch.get('commit') if isinstance(branch, dict) and branch.get('name') == mr.source_branch else None
        if not isinstance(head, dict) or head.get('id') != base or current.sha != base or current.target_branch != mr.target_branch:
            raise HTTPException(409, 'MR의 브랜치 또는 기준 커밋이 변경되었습니다. 최신 MR에서 다시 실행하세요.')

        digest = sha256(json.dumps([mr.reference.project_id, mr.reference.iid, workspace.project_id,
            mr.source_branch, base, actions], ensure_ascii=False, separators=(',', ':'), sort_keys=True).encode()).hexdigest()
        marker = f'LLM-MR-Update: {digest}'
        async def append():
            try:
                commit = await upstream.post(project + '/repository/commits', {
                    'branch': mr.source_branch,
                    'commit_message': draft.commit_message + '\n\n' + marker,
                    'actions': actions,
                }, before_write=before_write)
            except GitlabError as error:
                # Read back the branch after an ambiguous response. Never resend the commit.
                if error.status < 500:
                    raise
                branch = await get_optional(upstream, branch_path)
                commit = branch.get('commit') if isinstance(branch, dict) and branch.get('name') == mr.source_branch else None
                if not isinstance(commit, dict) or not isinstance(commit.get('message'), str) or not commit['message'].rstrip().endswith('\n' + marker):
                    raise GitlabError('GitLab 커밋 반영 여부를 확인할 수 없습니다. MR의 커밋 기록을 확인하세요.') from None

            parents = commit.get('parent_ids') if isinstance(commit, dict) else None
            parent = parents[0] if isinstance(parents, list) and len(parents) == 1 and valid_sha(parents[0]) else base
            commit_sha = publication_commit(commit, parent, marker)
            result = request_result({'iid': mr.reference.iid, 'state': current.state},
                upstream.provider.base_url + '/' + quote(current.namespace, safe='/'), mr.source_branch, current.target_branch) | {
                'status': 'updated', 'commit_sha': commit_sha,
                'commit_url': upstream.provider.base_url + '/' + quote(source['project'], safe='/') + '/-/commit/' + commit_sha,
                'base_sha': base, 'parent_sha': parent,
            }
            return result, confirmed(result)

        # A stop request prevents dispatch, but an already dispatched commit needs a recorded outcome.
        pending = asyncio.create_task(append())
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            await asyncio.gather(pending, return_exceptions=True)
            raise
    except GitlabError as error:
        status = error.status if error.status in {403, 404, 413, 422, 429} else 502
        raise HTTPException(status, str(error)) from None
