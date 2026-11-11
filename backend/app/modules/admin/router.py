import asyncio
from contextlib import aclosing
from hashlib import sha256
import secrets
from uuid import UUID, uuid4
from datetime import datetime, time as day_time, timedelta
import time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import Session, aliased

from app.core.config import get_settings
from app.db.session import get_db
from app.modules.auth.dependencies import AuthContext, Authenticated
from app.modules.auth.models import AuditLog, RefreshSession, User
from app.modules.chat.models import ConnectionSetting, Conversation, GenerationRun as Run, Message
from app.modules.chat import providers, registry, scheduler, usage
from app.modules.chat.independent import admitted
from app.modules.auth.audit import add_audit_log

router = APIRouter(prefix='/admin', tags=['operations'])
Database = Annotated[Session, Depends(get_db)]
Mode = Literal['all', 'live', 'test']


def require_admin(context: Authenticated) -> AuthContext:
    if not context.user.is_global_admin:
        raise HTTPException(403, '운영자 권한이 필요합니다.')
    return context


Admin = Annotated[AuthContext, Depends(require_admin)]


def mode_filter(mode: Mode):
    return [Run.is_test.is_(mode == 'test')] if mode != 'all' else []


def effective_status(now: int):
    return case((Run.status.in_(['running', 'queued','tool_waiting']) & (Run.deadline_at <= now), 'interrupted'), else_=Run.status)


def counts(db: Session, *conditions):
    status = effective_status(int(time.time()))
    row = db.execute(select(func.count(Run.id).label('total'),
        *[func.sum(case((status == value, 1), else_=0)).label(value)
          for value in ['complete', 'error', 'cancelled', 'queued', 'running', 'interrupted','tool_waiting']],
        func.avg(case((status == 'complete', Run.duration_ms))).label('average_ms'))
        .where(*conditions)).mappings().one()
    result = {key: int(row[key] or 0) for key in ['total', 'complete', 'error', 'cancelled', 'queued', 'running', 'interrupted','tool_waiting']}
    result['average_ms'] = round(row['average_ms']) if row['average_ms'] is not None else None
    denominator = result['complete'] + result['error'] + result['interrupted']
    result['success_rate'] = round(result['complete'] / denominator * 100, 1) if denominator else None
    return result


@router.get('/overview')
def overview(request: Request, context: Admin, db: Database, mode: Mode = 'all'):
    now, settings = int(time.time()), get_settings()
    db.execute(text('SELECT 1'))
    sessions = [RefreshSession.revoked_at.is_(None), RefreshSession.expires_at > now,
                User.is_active.is_(True), User.registration_status == 'approved']
    users = {
        'total': db.scalar(select(func.count(User.id))),
        'active': db.scalar(select(func.count(User.id)).where(User.is_active.is_(True))),
        'new_24h': db.scalar(select(func.count(User.id)).where(User.created_at >= now - 86400)),
        'logged_in_24h': db.scalar(select(func.count(User.id)).where(User.last_login_at >= now - 86400)),
        'sessions': db.scalar(select(func.count(RefreshSession.id)).join(User).where(*sessions)),
    }
    try:
        zone = ZoneInfo(context.user.time_zone or 'UTC')
    except ZoneInfoNotFoundError:
        zone = ZoneInfo('UTC')
    today = datetime.fromtimestamp(now, zone).date()
    days = []
    for offset in range(6, -1, -1):
        date = today - timedelta(days=offset)
        start = int(datetime.combine(date, day_time(), zone).timestamp())
        end = int(datetime.combine(date + timedelta(days=1), day_time(), zone).timestamp())
        days.append({'date': date.isoformat(), **counts(db, Run.started_at >= start, Run.started_at < end, *mode_filter(mode))})
    grouped = db.execute(select(Run.connection_id, Run.provider, Run.model, Run.is_test,
        func.count(Run.id).label('requests'), func.sum(Run.output_chars).label('output_chars'))
        .where(Run.started_at >= now - 86400, *mode_filter(mode))
        .group_by(Run.connection_id, Run.provider, Run.model, Run.is_test)
        .order_by(func.count(Run.id).desc()).limit(20)).mappings().all()
    try:
        registered = [connection.public() for connection in providers.connections(settings, db)]
        configuration_error = False
    except providers.ProviderError:
        registered, configuration_error = [], True
    started_at = getattr(request.app.state, 'started_at', now)
    return {
        'generated_at': now, 'time_zone': str(zone), 'mode': mode, 'users': users,
        'conversations': db.scalar(select(func.count(Conversation.id))),
        'messages': db.scalar(select(func.count(Message.id))),
        'requests_24h': counts(db, Run.started_at >= now - 86400, *mode_filter(mode)),
        'generating': db.scalar(select(func.count(Run.id)).where(
            Run.status == 'running', Run.deadline_at > now, *mode_filter(mode))),
        'queued': db.scalar(select(func.count(Run.id)).where(
            Run.status == 'queued', Run.deadline_at > now, *mode_filter(mode))),
        'first_record_at': db.scalar(select(func.min(Run.started_at)).where(*mode_filter(mode))),
        'daily': days, 'model_usage': [dict(row) for row in grouped],
        'connections': registered, 'configuration_error': configuration_error,
        'service': {'database': 'ready', 'uptime_seconds': max(0, now - started_at),
            'app_env': settings.app_env, 'test_mode': settings.llm_test_mode,
            'registration_enabled': settings.public_registration_enabled},
    }


@router.get('/requests')
def requests(context: Admin, db: Database, mode: Mode = 'all',
             status: Literal['all', 'complete', 'error', 'cancelled', 'queued', 'running', 'interrupted','tool_waiting'] = 'all',
             limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    now = int(time.time())
    query = select(Run, User.username).outerjoin(User).where(*mode_filter(mode))
    if status != 'all':
        query = query.where(effective_status(now) == status)
    rows = db.execute(query.order_by(Run.started_at.desc(), Run.id.desc()).offset(offset).limit(limit + 1)).all()
    return {'has_more': len(rows) > limit, 'items': [
        {'id': run.id, 'username': username, 'model': run.model, 'connection_id': run.connection_id,
         'provider': run.provider, 'is_test': run.is_test,
         'status': 'interrupted' if run.status in {'running', 'queued','tool_waiting'} and run.deadline_at <= now else run.status,
         'started_at': run.started_at, 'duration_ms': run.duration_ms, 'first_token_ms': run.first_token_ms,
         'output_chars': run.output_chars, 'error_code': 'interrupted' if run.status in {'running', 'queued','tool_waiting'} and run.deadline_at <= now else run.error_code}
        for run, username in rows[:limit]]}


@router.get('/users')
def users(context: Admin, db: Database, q: str = Query('', max_length=100),
          limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    query = select(User)
    if q.strip():
        query = query.where(or_(User.username.icontains(q.strip(), autoescape=True), User.display_name.icontains(q.strip(), autoescape=True)))
    rows = db.scalars(query.order_by(User.created_at.desc(), User.id.desc()).offset(offset).limit(limit + 1)).all()
    active = dict(db.execute(select(RefreshSession.user_id, func.count(RefreshSession.id)).where(
        RefreshSession.user_id.in_([user.id for user in rows[:limit]]), RefreshSession.revoked_at.is_(None),
        RefreshSession.expires_at > int(time.time())).group_by(RefreshSession.user_id)).all())
    return {'has_more': len(rows) > limit, 'items': [
        {'id': user.id, 'username': user.username, 'display_name': user.display_name,
         'is_active': user.is_active, 'is_admin': user.is_global_admin,
         'created_at': user.created_at, 'last_login_at': user.last_login_at,
         'sessions': active.get(user.id, 0) if user.is_active and user.registration_status == 'approved' else 0}
        for user in rows[:limit]]}


@router.get('/audit')
def audit(context: Admin, db: Database, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    actor, target = aliased(User), aliased(User)
    rows = db.execute(select(AuditLog, actor.username, target.username)
        .outerjoin(actor, AuditLog.actor_user_id == actor.id).outerjoin(target, AuditLog.target_user_id == target.id)
        .order_by(AuditLog.created_at.desc(), AuditLog.id.desc()).offset(offset).limit(limit + 1)).all()
    return {'has_more': len(rows) > limit, 'items': [
        {'id': record.id, 'action': record.action, 'outcome': record.outcome,
         'actor': username, 'target': target_name, 'created_at': record.created_at}
        for record, username, target_name in rows[:limit]]}


@router.post('/connections/{connection_id}/check')
async def check(connection_id: str, context: Admin, db: Database):
    try:
        connection = providers.resolve(get_settings(), connection_id, db)
        models = await providers.list_models(connection)
        return {'status': 'test' if connection.provider == 'test' else 'reachable', 'models': models}
    except providers.ProviderError as error:
        raise HTTPException(502, str(error)) from None


@router.get('/connections')
def managed_connections(context: Admin, db: Database):
    try:
        return registry.management_data(get_settings(), db)
    except providers.ProviderError as error:
        raise HTTPException(503, str(error)) from None


def save_connection(data: registry.ConnectionInput, context, db, *, creating=False):
    settings = get_settings()
    scheduler.lock(db)
    try:
        configured = {item.id: item for item in providers.connections(settings, db, include_disabled=True)}
        original = {item.id: item for item in providers.connections(settings)}
        keys = registry.keyring(settings)
    except providers.ProviderError as error:
        raise HTTPException(503, str(error)) from None
    row = db.get(ConnectionSetting, data.id)
    if creating and data.id in configured:
        raise HTTPException(409, '이미 등록된 연결 ID입니다.')
    if not creating and data.id not in configured:
        raise HTTPException(404, '연결을 찾을 수 없습니다.')
    if data.revision != (row.revision if row else 0):
        raise HTTPException(409, '다른 운영자가 연결을 변경했습니다. 다시 불러와 주세요.')
    if data.auth_mode == 'inherit' and data.id not in original:
        raise HTTPException(422, '이 연결에는 상속할 환경 설정이 없습니다.')
    if data.auth_mode == 'reference' and not keys.get(data.key_ref):
        raise HTTPException(422, '서버에 등록되지 않았거나 비어 있는 인증키 이름입니다.')
    known = set(db.scalars(select(User.username).where(User.username.in_(data.allowed_users))).all())
    if known != set(data.allowed_users):
        raise HTTPException(422, '사용 허용 목록에 존재하지 않는 사용자명이 있습니다.')
    if row is None:
        row = ConnectionSetting(id=data.id, revision=0)
        db.add(row)
    row.configuration = data.model_dump_json(exclude={'revision'})
    row.revision += 1
    add_audit_log(db, actor_user_id=context.user.id, action='admin.connection.create' if creating else 'admin.connection.update',
        details={'connection_id': data.id, 'enabled': data.enabled})
    db.commit()
    return next(item for item in registry.management_data(settings, db)['items'] if item['id'] == data.id)


@router.post('/connections', status_code=201)
def create_connection(data: registry.ConnectionInput, context: Admin, db: Database):
    return save_connection(data, context, db, creating=True)


@router.patch('/connections/{connection_id}')
def update_connection(connection_id: str, data: registry.ConnectionInput, context: Admin, db: Database):
    if connection_id != data.id:
        raise HTTPException(422, '연결 ID는 변경할 수 없습니다.')
    return save_connection(data, context, db)


class TokenBudgetInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    daily_tokens: int = Field(ge=0, le=1_000_000_000)


class ServiceKeyInput(TokenBudgetInput):
    user_id: UUID
    label: str = Field(min_length=1, max_length=100)


class ServiceKeyUpdate(BaseModel):
    model_config = ConfigDict(extra='forbid')
    daily_tokens: int | None = Field(None, ge=0, le=1_000_000_000)
    revoke: bool = False


class ProbeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = Field('', max_length=200)


@router.get('/usage')
def user_usage(context: Admin, db: Database, q: str = Query('', max_length=100),
               offset: int = Query(0, ge=0)):
    users = select(User).order_by(User.username)
    if q.strip():
        users = users.where(User.username.icontains(q.strip(), autoescape=True))
    rows = list(db.scalars(users.offset(offset).limit(21)))
    items = []
    for user in rows[:20]:
        budget = db.get(usage.UserBudget, user.id)
        items.append({'user_id': user.id, 'username': user.username,
                      'daily_tokens': budget.daily_tokens if budget else 0,
                      **usage.totals(db, user_id=user.id)})
    return {'items': items, 'has_more': len(rows) > 20, 'day': usage.day()}


@router.patch('/usage/{user_id}')
def set_user_budget(user_id: UUID, data: TokenBudgetInput, request: Request, context: Admin, db: Database):
    scheduler.lock(db)
    if not db.get(User, str(user_id)):
        raise HTTPException(404, '사용자를 찾을 수 없습니다.')
    row = db.get(usage.UserBudget, str(user_id))
    if not row:
        row = usage.UserBudget(user_id=str(user_id))
        db.add(row)
    row.daily_tokens = data.daily_tokens
    add_audit_log(db, action='llm.budget.updated', actor_user_id=context.user.id,
                  target_user_id=str(user_id), request=request, details={'daily_tokens': data.daily_tokens})
    db.commit()
    return {'daily_tokens': data.daily_tokens}


def key_summary(row, db):
    return {name: getattr(row, name) for name in (
        'id', 'user_id', 'label', 'prefix', 'daily_tokens', 'created_at', 'revoked'
    )} | usage.totals(db, key_id=row.id)


@router.get('/service-keys')
def service_keys(user_id: UUID, context: Admin, db: Database):
    rows = db.scalars(select(usage.ServiceKey).where(usage.ServiceKey.user_id == str(user_id))
                      .order_by(usage.ServiceKey.revoked, usage.ServiceKey.created_at.desc()).limit(50))
    return {'items': [key_summary(row, db) for row in rows]}


@router.post('/service-keys', status_code=201)
def create_service_key(data: ServiceKeyInput, request: Request, context: Admin, db: Database):
    scheduler.lock(db)
    user = db.get(User, str(data.user_id))
    if not user or not user.is_active or user.registration_status != 'approved':
        raise HTTPException(404, '승인된 활성 사용자를 선택하세요.')
    if not data.label.strip():
        raise HTTPException(422, '서비스 이름을 입력하세요.')
    count = db.scalar(select(func.count(usage.ServiceKey.id)).where(
        usage.ServiceKey.user_id == user.id, usage.ServiceKey.revoked.is_(False)))
    if count >= 20:
        raise HTTPException(422, '사용자별 활성 서비스 키는 최대 20개입니다.')
    secret = 'llm_sk_' + secrets.token_urlsafe(32)
    row = usage.ServiceKey(id=str(uuid4()), user_id=user.id, label=data.label.strip(),
                           digest=sha256(secret.encode()).hexdigest(), prefix=secret[:15],
                           daily_tokens=data.daily_tokens, created_at=int(time.time()))
    db.add(row)
    add_audit_log(db, action='llm.service_key.created', actor_user_id=context.user.id,
                  target_user_id=user.id, request=request, details={'key_id': row.id})
    db.commit()
    return key_summary(row, db) | {'secret': secret}


@router.patch('/service-keys/{key_id}')
def update_service_key(key_id: UUID, data: ServiceKeyUpdate, request: Request, context: Admin, db: Database):
    scheduler.lock(db)
    row = db.get(usage.ServiceKey, str(key_id))
    if not row:
        raise HTTPException(404, '서비스 키를 찾을 수 없습니다.')
    if data.daily_tokens is not None:
        row.daily_tokens = data.daily_tokens
    if data.revoke:
        row.revoked = True
    add_audit_log(db, action='llm.service_key.revoked' if data.revoke else 'llm.service_key.updated',
                  actor_user_id=context.user.id, target_user_id=row.user_id,
                  request=request, details={'key_id': row.id})
    db.commit()
    return key_summary(row, db)


@router.post('/connections/{connection_id}/probe')
async def probe_connection(connection_id: str, data: ProbeInput, context: Admin, db: Database):
    settings = get_settings()
    bind, user_id = db.get_bind(), context.user.id
    try:
        connection = providers.resolve(settings, connection_id, db, context.user)
    except providers.ProviderError as error:
        raise HTTPException(502, str(error)) from None
    db.rollback()
    try:
        models = await providers.list_models(connection)
    except providers.ProviderError as error:
        raise HTTPException(502, '모델 목록 조회 실패 · ' + str(error)) from None
    if connection.provider == 'test':
        return {'status': 'test', 'models': models, 'model': 'demo', 'sample': None, 'elapsed_ms': None}
    allowed = {profile.name for profile in connection.models}
    available = [name for name in models if not allowed or name in allowed]
    model = data.model or connection.model or (available[0] if available else '')
    if model not in available:
        raise HTTPException(422, '목록 조회는 성공했습니다. 서버에 등록된 테스트 모델을 선택하세요.')
    started = time.monotonic()
    parts = []
    try:
        async with asyncio.timeout(45):
            async with admitted(bind, user_id, connection, model, settings) as (selected, run_id):
                token = usage.scope.set(usage.Scope(bind, user_id, run_id))
                try:
                    source = providers.stream_reply(selected, model,
                        [{'role': 'user', 'content': 'Reply with a short confirmation: 연결 확인'}],
                        settings, max_tokens=min(256, selected.profile(model).max_output_tokens))
                    async with aclosing(source):
                        async for part in source:
                            parts.append(part)
                            if sum(map(len, parts)) > 4096:
                                raise providers.ProviderError('테스트 응답이 길이 한도를 초과했습니다.')
                    if not ''.join(parts).strip():
                        raise providers.ProviderError('표시할 텍스트 답변이 비어 있습니다.')
                finally:
                    usage.scope.reset(token)
    except (providers.ProviderError, TimeoutError) as error:
        raise HTTPException(502, '목록 조회 성공 · 테스트 답변 실패 · ' + (str(error) or '시간 초과')) from None
    return {'status': 'ready', 'models': available, 'model': model,
            'sample': ''.join(parts)[:500], 'elapsed_ms': round((time.monotonic() - started) * 1000)}
