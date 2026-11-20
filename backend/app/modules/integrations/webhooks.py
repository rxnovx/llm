"""GitLab CE와 호환되는 인증된 상태 수신. 이벤트가 도구 실행을 유발하지 않습니다."""

from hashlib import sha256
import hmac
import json
import re
import secrets
import time

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.modules.auth.audit import add_audit_log
from app.modules.auth.models import User
from . import config, service
from .models import GitlabWebhookEvent, GitlabWebhookSubscription


def owned(db, user_id, subscription_id):
    row = db.scalar(select(GitlabWebhookSubscription).where(GitlabWebhookSubscription.id == str(subscription_id),
        GitlabWebhookSubscription.user_id == user_id))
    if not row:
        raise HTTPException(404, 'GitLab 상태 구독을 찾을 수 없습니다.')
    account = service.owned(db, user_id, row.connection_id)
    if account.status != 'connected' or account.generation != row.generation:
        raise HTTPException(409, 'GitLab 계정이 변경되었습니다. 상태 구독을 다시 설정하세요.')
    if config.instance(account.provider_id).granted_scopes(account.provider_fingerprint) is None:
        raise HTTPException(409, 'GitLab 서버 설정이 변경되었습니다. 다시 연결하세요.')
    return row


def public(row):
    return {'id': row.id, 'connection_id': row.connection_id, 'project_id': row.project_id,
        'project': row.project_path, 'notifications_enabled': row.notifications_enabled,
        'callback_path': '/api/apps/gitlab/webhooks/' + row.id, 'created_at': row.created_at}


async def create(bind, user_id, connection_id, project_id, notifications=False):
    client = service.GitlabClient(bind, user_id, connection_id)
    project = await client.get(f'/projects/{project_id}', limit=128 * 1024)
    if not isinstance(project, dict) or project.get('id') != project_id:
        raise HTTPException(502, 'GitLab 프로젝트를 확인할 수 없습니다.')
    try:
        namespace = config.validate_path(project['path_with_namespace'])
    except (ValueError, TypeError, KeyError):
        raise HTTPException(502, 'GitLab 프로젝트 경로를 확인할 수 없습니다.') from None
    secret = secrets.token_urlsafe(32)
    with Session(bind) as db:
        account = service.owned(db, user_id, connection_id)
        if account.generation != client.generation:
            raise HTTPException(409, '설정 중 GitLab 계정이 변경되었습니다.')
        row = GitlabWebhookSubscription(user_id=user_id, connection_id=str(connection_id), project_id=project_id,
            project_path=namespace, secret_hash=sha256(secret.encode()).hexdigest(), notifications_enabled=notifications,
            generation=account.generation, created_at=int(time.time()))
        db.add(row)
        add_audit_log(db, action='gitlab_webhook_subscribe', actor_user_id=user_id,
            details={'project_id': project_id, 'notifications': notifications})
        try:
            db.commit()
        except IntegrityError:
            raise HTTPException(409, '이 프로젝트의 상태 구독이 이미 있습니다. 기존 구독을 확인하세요.') from None
        return public(row) | {'secret_token': secret,
            'setup': 'GitLab 프로젝트 Webhooks에 callback_path가 연결된 백엔드 주소와 Secret token을 등록하세요. '
                     'Merge request, Pipeline, Comments 이벤트를 선택하세요. 비밀값은 지금 한 번만 표시됩니다.'}


async def receive(bind, subscription_id, token, event_name, delivery, body):
    with Session(bind) as db:
        row = db.get(GitlabWebhookSubscription, str(subscription_id))
        if not row or not token or len(token) > 200 or not hmac.compare_digest(row.secret_hash, sha256(token.encode()).hexdigest()):
            raise HTTPException(401, 'GitLab 웹훅 인증을 확인하세요.')
        owned(db, row.user_id, subscription_id)
        user = db.get(User, row.user_id)
        if not user or not user.is_active or user.registration_status != 'approved':
            raise HTTPException(403, '이 상태 구독은 사용할 수 없습니다.')
        user_id, project_id = row.user_id, row.project_id
    if len(body) > 512 * 1024:
        raise HTTPException(413, 'GitLab 이벤트 크기 제한을 초과했습니다.')
    try:
        payload = json.loads(body)
        project = payload['project']
        attributes = payload['object_attributes']
        if not isinstance(project, dict) or type(project.get('id')) is not int or project['id'] != project_id or not isinstance(attributes, dict):
            raise ValueError()
        if event_name == 'Merge Request Hook' and payload.get('object_kind') == 'merge_request':
            kind, object_id, status = 'merge_request', attributes.get('iid'), attributes.get('state')
            if attributes.get('target_project_id', project_id) != project_id or status not in {'opened', 'closed', 'merged', 'locked'}:
                raise ValueError()
        elif event_name == 'Pipeline Hook' and payload.get('object_kind') == 'pipeline':
            kind, object_id, status = 'pipeline', attributes.get('id'), attributes.get('status')
            if status not in {'created', 'waiting_for_resource', 'preparing', 'pending', 'running', 'success', 'failed',
                              'canceled', 'skipped', 'manual', 'scheduled'}:
                raise ValueError()
        elif event_name == 'Note Hook' and payload.get('object_kind') == 'note' and attributes.get('noteable_type') == 'MergeRequest':
            kind, object_id, status = 'merge_request_comment', attributes.get('noteable_iid') or payload.get('merge_request', {}).get('iid'), 'updated'
        else:
            raise HTTPException(422, 'MR·파이프라인·MR 댓글 상태 이벤트만 지원합니다.')
        if type(object_id) is not int or object_id < 1:
            raise ValueError()
    except (ValueError, KeyError, TypeError, AttributeError, UnicodeError):
        raise HTTPException(422, 'GitLab 이벤트의 프로젝트·유형·번호를 확인하세요.') from None
    if delivery and not re.fullmatch(r'[a-zA-Z0-9_-]{1,64}', delivery):
        raise HTTPException(422, 'GitLab 이벤트 식별자를 확인하세요.')
    identity = delivery or sha256(body).hexdigest()
    with Session(bind) as db:
        current = owned(db, user_id, subscription_id)
        if db.scalar(select(GitlabWebhookEvent.id).where(GitlabWebhookEvent.subscription_id == current.id,
                GitlabWebhookEvent.delivery_id == identity)):
            return {'accepted': True, 'duplicate': True}
        db.add(GitlabWebhookEvent(subscription_id=current.id, delivery_id=identity, kind=kind,
            object_id=object_id, status=status, received_at=int(time.time()), seen=not current.notifications_enabled))
        try:
            db.commit()
        except IntegrityError:
            return {'accepted': True, 'duplicate': True}
        keep = select(GitlabWebhookEvent.id).where(GitlabWebhookEvent.subscription_id == current.id).order_by(
            GitlabWebhookEvent.received_at.desc(), GitlabWebhookEvent.id.desc()).limit(100)
        db.execute(delete(GitlabWebhookEvent).where(GitlabWebhookEvent.subscription_id == current.id,
            GitlabWebhookEvent.id.not_in(keep)))
        db.commit()
    return {'accepted': True, 'duplicate': False}


async def events(bind, user_id, subscription_id):
    with Session(bind) as db:
        row = owned(db, user_id, subscription_id)
        connection_id, project_id = row.connection_id, row.project_id
    # 구독 시점뿐 아니라 조회 시점에도 실제 프로젝트 접근 권한을 검사합니다.
    client = service.GitlabClient(bind, user_id, connection_id)
    await client.get(f'/projects/{project_id}', limit=128 * 1024)
    with Session(bind) as db:
        row = owned(db, user_id, subscription_id)
        rows = db.scalars(select(GitlabWebhookEvent).where(GitlabWebhookEvent.subscription_id == row.id)
            .order_by(GitlabWebhookEvent.received_at.desc(), GitlabWebhookEvent.id.desc()).limit(100)).all()
        return public(row) | {'events': [{'id': item.id, 'kind': item.kind, 'object_id': item.object_id,
            'status': item.status, 'received_at': item.received_at,
            'notify': row.notifications_enabled and not item.seen} for item in rows]}
