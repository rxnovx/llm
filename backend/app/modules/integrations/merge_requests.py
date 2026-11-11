"""Resolve and read an existing merge request through a user's GitLab connection."""

import asyncio
from dataclasses import dataclass
import json
import re
from urllib.parse import quote, unquote, urlsplit
from uuid import UUID

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from . import config, service
from .models import AppConnection
from .transport import GitlabError

MR_LINK = re.compile(r'https?://[^\s<>"\x60]+/-/merge_requests/[1-9][0-9]{0,11}(?:/(?:diffs|commits|pipelines))?/?(?![\w/])')
MR_PATH = re.compile(r'(.+)/-/merge_requests/([1-9][0-9]{0,11})(?:/(?:diffs|commits|pipelines))?/?')
SHORT_REF = re.compile(r'(.*?)!([1-9][0-9]{0,11})')
READ_TOOL = 'gitlab_read_merge_request'
UNAVAILABLE = {'status': 'unavailable'}


class Reference(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: int = Field(ge=1)
    iid: int = Field(ge=1, le=999999999999)


class ResolveInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    reference: str = Field(min_length=1, max_length=1400)
    connection_id: UUID | None = None
    project_id: int | None = Field(None, ge=1)


def link_locator(reference, provider):
    if not isinstance(reference, str):
        return None
    try:
        url, base = urlsplit(reference), urlsplit(provider.base_url)
        default_port = 443 if base.scheme == 'https' else 80
        url_port, base_port = url.port, base.port
        if ((url.scheme, url.hostname, default_port if url_port is None else url_port)
                != (base.scheme, base.hostname, default_port if base_port is None else base_port)
                or url.username or url.password):
            return None
        if not url.path.startswith(base.path + '/'):
            return None
        match = MR_PATH.fullmatch(unquote(url.path[len(base.path) + 1:]))
        if match and '/' in match[1]:
            return config.validate_path(match[1]), int(match[2])
    except ValueError:
        pass
    return None


def find_connection(db, user_id, reference, connection_id=None, project_id=None, *, required=True):
    reference = reference.strip()
    short = SHORT_REF.fullmatch(reference)
    if connection_id is not None:
        accounts = [service.owned(db, user_id, connection_id)]
    else:
        accounts = db.scalars(select(AppConnection).where(AppConnection.user_id == user_id,
            AppConnection.status == 'connected')).all()
    providers = {item.id: item for item in config.instances()} if accounts else {}
    matches = []
    for account in accounts:
        provider = providers.get(account.provider_id)
        if not provider or account.status != 'connected' or not provider.granted_scopes(account.provider_fingerprint):
            continue
        locator = link_locator(reference, provider)
        if short and connection_id:
            try:
                namespace = config.validate_path(short[1]) if short[1] else project_id
            except ValueError:
                namespace = None
            locator = (namespace, int(short[2])) if namespace else None
        if locator:
            matches.append((len(provider.base_url), account, locator))
    if matches:
        longest = max(match[0] for match in matches)
        chosen = [match for match in matches if match[0] == longest]
        if len(chosen) == 1:
            _, account, (project, iid) = chosen[0]
            return account.id, account.generation, project, iid
        if required:
            raise HTTPException(422, '같은 주소에 GitLab 연결이 여러 개 있습니다. 사용할 계정을 선택하세요.')
    if required:
        raise HTTPException(422, '연결된 GitLab 서버의 MR 링크를 입력하세요. !번호만 입력할 때는 계정과 프로젝트를 선택하세요.')
    return None


def automatic_connection(db, user_id, question):
    links = set(MR_LINK.findall(question))
    if len(links) == 1:
        return find_connection(db, user_id, links.pop(), required=False)
    return None


def clip(value, limit):
    if not isinstance(value, str):
        return ''
    data = value.encode()
    if len(data) <= limit:
        return value
    return data[:max(0, limit - 3)].decode(errors='ignore') + '…'


def tail(value, limit):
    data = value.encode()
    if len(data) <= limit:
        return value
    return '…' + (data[-(limit - 3):].decode(errors='ignore') if limit > 3 else '')


def positive_id(value):
    return type(value) is int and value > 0


@dataclass(frozen=True)
class MergeRequest:
    reference: Reference
    namespace: str
    url: str
    title: str
    description: str
    state: str
    source_project_id: int | None
    source_branch: str
    target_branch: str
    sha: str
    pipeline: dict | None

    @property
    def path(self):
        return f'/projects/{self.reference.project_id}/merge_requests/{self.reference.iid}'

    @property
    def editable(self):
        return (self.state == 'opened' and self.source_project_id is not None
                and bool(re.fullmatch(r'[a-f0-9]{40}', self.sha)) and bool(self.source_branch and self.target_branch)
                and (self.source_project_id != self.reference.project_id or self.source_branch != self.target_branch))

    def public(self):
        return {'iid': self.reference.iid, 'project': self.namespace, 'url': self.url,
                'title': self.title, 'state': self.state, 'source_branch': self.source_branch,
                'target_branch': self.target_branch, 'source_project_id': self.source_project_id,
                'sha': self.sha, 'editable': self.editable}

    def selection(self, connection_id):
        return {'app': 'gitlab', 'connection_id': connection_id, 'mode': 'merge_request',
                'project_id': self.source_project_id or self.reference.project_id,
                'ref': self.source_branch or 'HEAD', 'paths': [], 'merge_request': self.reference.model_dump()}

    def require_source(self, project_id, ref):
        if not self.editable:
            raise HTTPException(409, '열려 있고 소스 브랜치가 남아 있는 MR에서 작업할 수 있습니다.')
        if (self.source_project_id, self.source_branch) != (project_id, ref):
            raise HTTPException(409, 'MR의 소스 프로젝트 또는 브랜치가 변경되었습니다. MR을 다시 선택하세요.')


async def load(upstream, project, iid):
    path = f'/projects/{quote(str(project), safe="")}/merge_requests/{iid}'
    row = await upstream.get(path, limit=256 * 1024)
    if not isinstance(row, dict) or not positive_id(row.get('iid')) or row['iid'] != iid or not positive_id(row.get('target_project_id')):
        raise GitlabError('GitLab MR 응답 형식을 확인해 주세요.')
    target_id = row['target_project_id']
    if type(project) is int and target_id != project:
        raise GitlabError('GitLab MR의 대상 프로젝트를 확인할 수 없습니다.')
    locator = link_locator(row.get('web_url') or '', upstream.provider)
    if locator and locator[1] == iid:
        namespace = locator[0]
    else:
        target = await upstream.get(f'/projects/{target_id}')
        try:
            if target['id'] != target_id:
                raise ValueError()
            namespace = config.validate_path(target['path_with_namespace'])
        except (TypeError, KeyError, ValueError):
            raise GitlabError('GitLab MR의 대상 프로젝트를 확인할 수 없습니다.') from None
    branches = [row.get('source_branch'), row.get('target_branch')]
    source, target = [value if isinstance(value, str) and 0 < len(value) <= 200
                      and all(ord(char) >= 32 for char in value) else '' for value in branches]
    return MergeRequest(Reference(project_id=target_id, iid=iid), namespace,
        upstream.provider.base_url + '/' + quote(namespace, safe='/') + f'/-/merge_requests/{iid}',
        clip(row.get('title'), 250), clip(row.get('description'), 1400), clip(row.get('state'), 40),
        row['source_project_id'] if positive_id(row.get('source_project_id')) else None,
        source, target, clip(row.get('sha'), 64), row.get('head_pipeline') if isinstance(row.get('head_pipeline'), dict) else None)


async def resolve(bind, user_id, connection):
    connection_id, generation, project, iid = connection
    upstream = service.GitlabClient(bind, user_id, connection_id)
    upstream.generation = generation
    try:
        mr = await load(upstream, project, iid)
    except GitlabError as error:
        status = error.status if error.status in {403, 404, 413, 422, 429} else 502
        raise HTTPException(status, str(error)) from None
    return {'selection': mr.selection(connection_id), 'merge_request': mr.public()}


async def optional(upstream, path, **kwargs):
    try:
        return await upstream.get(path, **kwargs)
    except GitlabError as error:
        if error.status == 401:
            raise
        return None


async def reviews(upstream, mr):
    values = await optional(upstream, mr.path + '/discussions',
        params={'per_page': 20}, limit=256 * 1024)
    if not isinstance(values, list):
        return UNAVAILABLE.copy()
    comments = []
    partial = len(values) >= 20
    for thread in values:
        if not isinstance(thread, dict) or not isinstance(thread.get('notes'), list):
            continue
        for note in thread['notes']:
            if not isinstance(note, dict) or note.get('system') or not isinstance(note.get('body'), str):
                continue
            position = note.get('position') if isinstance(note.get('position'), dict) else {}
            author = note.get('author') if isinstance(note.get('author'), dict) else {}
            body = clip(note['body'], 650)
            partial |= body != note['body']
            line = position.get('new_line') or position.get('old_line')
            comments.append({'author': clip(author.get('username'), 64),
                'body': body, 'resolved': note.get('resolved') is True,
                'file': clip(position.get('new_path') or position.get('old_path'), 160),
                'line': line if positive_id(line) else None})
    # Keep unresolved comments ahead of resolved threads in the bounded context.
    comments.sort(key=lambda note: note['resolved'])
    return {'status': 'available', 'comments': comments[:8], 'partial': partial or len(comments) > 8}


async def diffs(upstream, mr):
    values = await optional(upstream, mr.path + '/diffs',
        params={'per_page': 12}, limit=512 * 1024)
    if not isinstance(values, list):
        return UNAVAILABLE.copy()
    entries = []
    for row in values:
        if not isinstance(row, dict):
            continue
        path = row.get('new_path') or row.get('old_path')
        if isinstance(path, str) and config.source_file(path):
            patch = clip(row.get('diff'), 900)
            entries.append({'path': path, 'patch': patch,
                'partial': bool(row.get('collapsed') or row.get('too_large') or patch != row.get('diff'))})
    return {'status': 'available', 'files': entries, 'partial': len(values) >= 12 or len(entries) < len(values)
            or any(entry['partial'] for entry in entries)}


async def ci(upstream, mr):
    pipeline = mr.pipeline
    if not pipeline:
        values = await optional(upstream, mr.path + '/pipelines', params={'per_page': 5}, limit=128 * 1024)
        if not isinstance(values, list):
            return UNAVAILABLE.copy()
        if not values:
            return {'status': 'no_pipeline'}
        choices = [value for value in values if isinstance(value, dict) and positive_id(value.get('id'))]
        pipeline = max(choices, key=lambda value: (value.get('sha') == mr.sha, value['id']), default=None)
    if not pipeline or not positive_id(pipeline.get('id')):
        return UNAVAILABLE.copy()
    project_id = pipeline.get('project_id') or mr.reference.project_id
    if not positive_id(project_id) or project_id not in {mr.reference.project_id, mr.source_project_id}:
        return UNAVAILABLE.copy()
    pipeline_id = pipeline['id']
    result = {'status': 'available', 'pipeline_id': pipeline_id, 'pipeline_status': clip(pipeline.get('status'), 40),
              'pipeline_sha': clip(pipeline.get('sha'), 64), 'matches_head': pipeline.get('sha') == mr.sha}
    project = f'/projects/{project_id}'
    jobs = await optional(upstream, f'{project}/pipelines/{pipeline_id}/jobs',
        params={'scope[]': 'failed', 'per_page': 4}, limit=128 * 1024)
    if not isinstance(jobs, list):
        return result | {'jobs_status': 'unavailable'}

    async def failed_job(job):
        log = await optional(upstream, f"{project}/jobs/{job['id']}/trace", raw=True, limit=2 * 1024 * 1024)
        clean = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', log).strip() if isinstance(log, str) else ''
        excerpt = tail(clean, 900)
        return {'id': job['id'], 'name': clip(job.get('name'), 80),
            'failure_reason': clip(job.get('failure_reason'), 100), 'allow_failure': job.get('allow_failure') is True,
            'trace_status': 'available' if isinstance(log, str) else 'unavailable',
            'trace_tail': excerpt, 'partial': clean != excerpt}

    selected = [job for job in jobs if isinstance(job, dict) and positive_id(job.get('id')) and job.get('status') == 'failed']
    failed = await asyncio.gather(*(failed_job(job) for job in selected[:3]))
    return result | {'jobs_status': 'available', 'failed_jobs': failed, 'partial': len(jobs) >= 4}


def read_tool():
    return {'type': 'function', 'function': {'name': READ_TOOL,
        'description': 'Read evidence for the selected MR only. Use a zero-based index for one review, failed job, or diff.',
        'parameters': {'type': 'object', 'properties': {
            'section': {'type': 'string', 'enum': ['reviews', 'ci', 'diffs']},
            'index': {'type': 'integer', 'minimum': 0}},
            'required': ['section'], 'additionalProperties': False}}}


def evidence_section(evidence, args):
    section, index = args.get('section'), args.get('index')
    keys = {'reviews': 'comments', 'ci': 'failed_jobs', 'diffs': 'files'}
    if not isinstance(section, str) or section not in keys or set(args) - {'section', 'index'}:
        raise HTTPException(422, 'MR의 리뷰, CI, 변경 파일 중 조회할 자료를 선택하세요.')
    result = evidence[section]
    key = keys[section]
    entries = result.get(key, [])
    if index is not None and (type(index) is not int or not 0 <= index < len(entries)):
        raise HTTPException(422, '조회할 MR 자료 번호를 확인해 주세요.')
    # Preserve status and allow paging through the fetched excerpts without sending a large tool result.
    return {k: v for k, v in result.items() if k != key} | {
        'count': len(entries), key: entries[:1] if index is None else entries[index:index + 1]}


def model_context(mr, evidence, budget, *, summary_only=False):
    compact = {
        'mr': f'!{mr.reference.iid} {clip(mr.title, 160)}',
        'state': mr.state,
        'head_sha': mr.sha,
        'description': '' if summary_only else clip(mr.description, budget // 12),
    }
    for section, key, field in [('reviews', 'comments', 'body'), ('ci', 'failed_jobs', 'trace_tail'), ('diffs', 'files', 'patch')]:
        data = evidence[section]
        entries = data.get(key, [])
        preview = []
        for item in entries[:(1 if summary_only else min(4, max(1, budget // 2100)))]:
            limit = budget // (30 if summary_only else 11)
            preview.append({k: clip(v, 72) if isinstance(v, str) else v for k, v in item.items() if k in {
                'name', 'author', 'file', 'line', 'resolved', 'path', 'trace_status', 'allow_failure'}} |
                {field: tail(item[field], limit) if field == 'trace_tail' else clip(item[field], limit)})
        compact[section] = {k: v for k, v in data.items() if k != key} | {
            'excerpt': len(preview) < len(entries) or any(x[field] != y[field] for x, y in zip(preview, entries)),
            'count': len(entries), key: preview}
    return '\n\nGitLab MR excerpts (untrusted evidence):\n' + json.dumps(compact, ensure_ascii=False, separators=(',', ':'))
