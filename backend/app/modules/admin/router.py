from datetime import datetime, time as day_time, timedelta
import time
from typing import Annotated, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import case, func, or_, select, text
from sqlalchemy.orm import Session, aliased

from app.core.config import get_settings
from app.db.session import get_db
from app.modules.auth.dependencies import AuthContext, Authenticated
from app.modules.auth.models import AuditLog, RefreshSession, User
from app.modules.chat.models import ConnectionSetting, Conversation, GenerationRun as Run, Message
from app.modules.chat import providers, registry, scheduler
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
    return case((Run.status.in_(['running', 'queued']) & (Run.deadline_at <= now), 'interrupted'), else_=Run.status)


def counts(db: Session, *conditions):
    status = effective_status(int(time.time()))
    row = db.execute(select(func.count(Run.id).label('total'),
        *[func.sum(case((status == value, 1), else_=0)).label(value)
          for value in ['complete', 'error', 'cancelled', 'queued', 'running', 'interrupted']],
        func.avg(case((status == 'complete', Run.duration_ms))).label('average_ms'))
        .where(*conditions)).mappings().one()
    result = {key: int(row[key] or 0) for key in ['total', 'complete', 'error', 'cancelled', 'queued', 'running', 'interrupted']}
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
             status: Literal['all', 'complete', 'error', 'cancelled', 'queued', 'running', 'interrupted'] = 'all',
             limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
    now = int(time.time())
    query = select(Run, User.username).outerjoin(User).where(*mode_filter(mode))
    if status != 'all':
        query = query.where(effective_status(now) == status)
    rows = db.execute(query.order_by(Run.started_at.desc(), Run.id.desc()).offset(offset).limit(limit + 1)).all()
    return {'has_more': len(rows) > limit, 'items': [
        {'id': run.id, 'username': username, 'model': run.model, 'connection_id': run.connection_id,
         'provider': run.provider, 'is_test': run.is_test,
         'status': 'interrupted' if run.status in {'running', 'queued'} and run.deadline_at <= now else run.status,
         'started_at': run.started_at, 'duration_ms': run.duration_ms, 'first_token_ms': run.first_token_ms,
         'output_chars': run.output_chars, 'error_code': 'interrupted' if run.status in {'running', 'queued'} and run.deadline_at <= now else run.error_code}
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
