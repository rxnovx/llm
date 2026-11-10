"""Transactional FIFO admission shared by API workers; no Redis/native dependency."""
import time

from fastapi import HTTPException
from sqlalchemy import select, update

from app.modules.auth.models import User
from app.modules.chat.models import Admission, Conversation, GenerationRun, SchedulerLock


def lock(db):
    # An UPDATE takes a write/row lock on both SQLite and PostgreSQL. Keep the
    # transaction short; never hold this mutex across network calls or awaits.
    db.execute(update(SchedulerLock).where(SchedulerLock.id == 1).values(version=SchedulerLock.version + 1))


def active_rows(db, now):
    return db.execute(select(Admission, GenerationRun).join(GenerationRun)
        .where(Admission.state.in_(['queued', 'running']), Admission.expires_at > now)
        .order_by(Admission.id)).all()


def enqueue(db, run, settings):
    now = int(time.time())
    rows = active_rows(db, now)
    if sum(a.state == 'queued' for a, _ in rows) >= settings.llm_queue_capacity:
        raise HTTPException(429, '모델 대기열이 가득 찼습니다. 잠시 후 다시 시도해 주세요.', headers={'Retry-After': '5'})
    if sum(a.state == 'queued' and r.user_id == run.user_id for a, r in rows) >= settings.llm_user_queue_limit:
        raise HTTPException(429, '대기 중인 요청이 너무 많습니다. 기존 요청을 완료하거나 취소하세요.', headers={'Retry-After': '5'})
    db.add(Admission(request_id=run.id, state='queued', enqueued_at=now, expires_at=now + settings.llm_queue_wait_seconds))


def poll(db, request_id, settings, resolve):
    """Return (state, position, connection). Caller commits before yielding SSE."""
    lock(db)
    now = int(time.time())
    admission = db.scalar(select(Admission).where(Admission.request_id == request_id))
    run = db.get(GenerationRun, request_id)
    if not admission or not run:
        return 'cancelled', 0, None
    conversation = db.get(Conversation, run.conversation_id) if run.conversation_id else None
    user = db.get(User, run.user_id) if run.user_id else None
    if (not conversation or conversation.generation_id != request_id or conversation.cancel_requested
            or not user or not user.is_active or user.registration_status != 'approved'):
        admission.state = 'cancelled'
        return 'cancelled', 0, None
    if admission.expires_at <= now:
        admission.state = 'error'
        return 'expired', 0, None
    # Revalidate disabled connections, permissions and model limits while queued.
    connection = resolve(settings, run.connection_id, db, user)
    profile = connection.profile(run.model)
    rows = active_rows(db, now)
    running = [(a, r) for a, r in rows if a.state == 'running']
    ahead = [(a, r) for a, r in rows if a.state == 'queued' and a.id < admission.id
             and (r.connection_id == run.connection_id or r.user_id == run.user_id)]
    blocked = (any(r.user_id == run.user_id for _, r in ahead)
        or any(r.connection_id == run.connection_id for _, r in ahead)
        or sum(r.user_id == run.user_id for _, r in running) >= settings.llm_user_concurrency
        or sum(r.connection_id == run.connection_id for _, r in running) >= connection.max_concurrent
        or sum(r.connection_id == run.connection_id and r.model == run.model for _, r in running) >= profile.max_concurrent)
    if blocked:
        run.status = 'queued'
        return 'queued', len(ahead) + 1, connection
    admission.state, admission.started_at = 'running', now
    admission.expires_at = now + settings.llm_timeout_seconds + 15
    run.status = 'running'
    run.provider, run.is_test = connection.provider, connection.provider == 'test'
    run.deadline_at = admission.expires_at
    conversation.generation_until = admission.expires_at
    return 'running', 0, connection


def finish(db, request_id, status):
    db.execute(update(Admission).where(Admission.request_id == request_id,
        Admission.state.in_(['queued', 'running'])).values(state=status, expires_at=int(time.time())))
