"""Database-backed admission for image generation, editing, and operator probes."""
import asyncio
from collections import Counter
import time

from fastapi import HTTPException
from sqlalchemy import delete, select, update
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.chat.models import Conversation, GenerationRun
from app.modules.chat.scheduler import lock
from .models import ImageAdmission


class Cancelled(Exception):
    pass


def _lease(db, user_id, request_id, chat, now):
    user = db.get(User, user_id)
    if not user or not user.is_active or user.registration_status != 'approved':
        raise Cancelled()
    if not chat:
        if not user.is_global_admin:
            raise Cancelled()
        return None

    run = db.get(GenerationRun, request_id)
    if not run or run.user_id != user_id or run.status not in {'queued', 'running', 'tool_waiting'}:
        raise Cancelled()
    lease = db.scalar(select(Conversation.generation_until).where(
        Conversation.id == run.conversation_id, Conversation.user_id == user_id,
        Conversation.generation_id == request_id, Conversation.cancel_requested.is_(False),
        Conversation.generation_until > now,
    ))
    if lease is None or run.deadline_at <= now:
        raise Cancelled()
    return min(lease, run.deadline_at)


def _active(db, now):
    return db.scalars(select(ImageAdmission).where(ImageAdmission.expires_at > now).order_by(ImageAdmission.id)).all()


def _waiting(rows):
    return [row for row in rows if row.state == 'queued' and not row.cancel_requested]


def counts(db):
    rows = _active(db, int(time.time()))
    return {'queued': len(_waiting(rows)), 'running': sum(row.state == 'running' for row in rows)}


def _payload(row, position):
    return {'status': row.state, 'position': position, 'request_id': row.request_id, 'kind': 'image'}


def snapshot(db, request_id):
    rows = _active(db, int(time.time()))
    row = next((item for item in rows if item.request_id == request_id), None)
    if row is None or row.state == 'queued' and row.cancel_requested:
        return None
    position = sum(item.id <= row.id for item in _waiting(rows)) if row.state == 'queued' else 0
    return _payload(row, position)


async def wait(bind, user_id, request_id, settings, *, chat=True, probe_id=None):
    """Yield queue position changes and one running event. The caller releases the slot."""
    probe_id = None if chat else (probe_id or request_id)
    with Session(bind) as db:
        lock(db)
        now = int(time.time())
        lease = _lease(db, user_id, request_id, chat, now)
        expires_at = now + settings.image_queue_wait_seconds
        if lease is not None:
            expires_at = min(expires_at, lease)
        db.execute(delete(ImageAdmission).where(ImageAdmission.expires_at <= now))
        rows = _active(db, now)
        if any(row.request_id == request_id
               or probe_id is not None and row.user_id == user_id and row.probe_id == probe_id for row in rows):
            raise HTTPException(409, '이미 처리 중인 이미지 요청입니다.')
        if len(_waiting(rows)) >= settings.image_queue_capacity:
            raise HTTPException(429, '이미지 대기열이 가득 찼습니다. 잠시 후 다시 시도해 주세요.',
                                headers={'Retry-After': '5'})
        db.add(ImageAdmission(request_id=request_id, user_id=user_id, probe_id=probe_id,
                              state='queued', expires_at=expires_at))
        db.commit()

    last_position = None
    while True:
        with Session(bind) as db:
            lock(db)
            now = int(time.time())
            _lease(db, user_id, request_id, chat, now)
            rows = _active(db, now)
            row = next((item for item in rows if item.request_id == request_id), None)
            if row is None:
                raise HTTPException(504, '이미지 대기 시간이 초과되었습니다. 다시 시도해 주세요.')
            if row.cancel_requested:
                raise Cancelled()
            running = Counter(item.user_id for item in rows if item.state == 'running')
            queued = _waiting(rows)
            first = next((item for item in queued if running[item.user_id] < settings.image_user_concurrency), None)
            if first is row and running.total() < settings.image_max_concurrent:
                row.state = 'running'
                row.expires_at = now + settings.image_generation_timeout_seconds + 15
            position = sum(item.id <= row.id for item in queued) if row.state == 'queued' else 0
            payload = _payload(row, position)
            db.commit()

        if payload['status'] == 'running':
            yield payload
            return
        if position != last_position:
            yield payload
            last_position = position
        await asyncio.sleep(0.5)


def request_probe_cancel(db, user_id, probe_id):
    """Mark an owned probe for cancellation and leave the transaction to the caller."""
    lock(db)
    result = db.execute(update(ImageAdmission).where(
        ImageAdmission.user_id == user_id, ImageAdmission.probe_id == probe_id, ImageAdmission.probe_id.is_not(None),
        ImageAdmission.expires_at > int(time.time()),
    ).values(cancel_requested=True))
    return result.rowcount == 1


def probe_cancelled(db, user_id, request_id):
    """A lost lease or admin role also stops the provider request."""
    active = db.scalar(select(ImageAdmission.id).join(User, User.id == ImageAdmission.user_id).where(
        ImageAdmission.request_id == request_id, ImageAdmission.user_id == user_id, ImageAdmission.probe_id.is_not(None),
        ImageAdmission.cancel_requested.is_(False), ImageAdmission.expires_at > int(time.time()),
        User.is_active.is_(True), User.registration_status == 'approved', User.is_global_admin.is_(True),
    ))
    return active is None


def finish(bind, request_id):
    with Session(bind) as db:
        lock(db)
        db.execute(delete(ImageAdmission).where(ImageAdmission.request_id == request_id))
        db.commit()
