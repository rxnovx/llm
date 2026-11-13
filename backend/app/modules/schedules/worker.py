"""Claim scheduled work and run it through the existing chat queue."""
import asyncio
from dataclasses import dataclass
import logging
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import delete, exists, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.chat import scheduler
from app.modules.chat.models import Conversation, GenerationRun, Message
from app.modules.workflows import conversations
from app.modules.workflows.models import Artifact, ProjectConversation
from . import intent
from .models import Schedule, ScheduleRun
from .service import CANCELLED, finish_locked, verify_scope


logger = logging.getLogger(__name__)
MAX_GLOBAL_RUNS = 4
MAX_USER_RUNS = 1
POLL_SECONDS = 5
QUEUE_RETRY_SECONDS = 15
INTERRUPTED = '서버 재시작 또는 중단으로 실행을 마치지 못했습니다. 결과 대화에서 다시 시도할 수 있습니다.'


@dataclass(frozen=True)
class ClaimedRun:
    request_id: str
    conversation_id: str
    user_id: str
    connection_id: str
    model: str
    prompt: str
    lease_until: int


def generation_result(db, run, *, expired_at=None, cancelled=False):
    generation = db.get(GenerationRun, run.request_id)
    if not generation:
        return None
    if generation.status in {'complete', 'error', 'cancelled'}:
        if generation.status != 'error':
            return generation.status, None if generation.status == 'complete' else '예약 실행이 중지되었습니다.'
        error = db.scalar(select(Message.error).where(Message.conversation_id == run.conversation_id,
            Message.role == 'assistant').order_by(Message.position.desc()).limit(1))
        return 'error', error or '예약된 응답을 생성하지 못했습니다. 모델 연결을 확인해 주세요.'
    if expired_at is None:
        return None
    status, code, error = ('cancelled', 'cancelled', CANCELLED) if cancelled else ('error', 'interrupted', INTERRUPTED)
    generation.status, generation.error_code, generation.finished_at = status, code, expired_at
    generation.duration_ms = max(0, (expired_at - generation.started_at) * 1000)
    scheduler.finish(db, run.request_id, status)
    conversation = db.get(Conversation, run.conversation_id) if run.conversation_id else None
    if conversation and conversation.generation_id == run.request_id:
        conversation.generation_id, conversation.generation_until, conversation.cancel_requested = None, 0, False
        db.execute(delete(Artifact).where(Artifact.run_id == run.request_id, Artifact.owner_id == conversation.user_id))
        pending = db.scalars(select(Message).where(Message.conversation_id == conversation.id,
                                                  Message.status == 'pending'))
        for message in pending:
            message.status, message.error = status, error
            state = conversations.state_for(db, message)
            state.error_code = code
    return status, error


def issue(db, row, run, now, lease_seconds):
    conversation = db.get(Conversation, run.conversation_id) if run.conversation_id else None
    try:
        verify_scope(db, row, conversation)
    except HTTPException as error:
        finish_locked(db, row, run, 'error', str(error.detail), now)
        row.status, row.next_run_at = 'complete', None
        return None
    if conversation is None:
        conversation = Conversation(user_id=row.user_id, title=('예약 · ' + row.title)[:120],
                                    connection_id=row.connection_id, model=row.model)
        db.add(conversation)
        db.flush()
        if row.project_id:
            db.add(ProjectConversation(project_id=row.project_id, conversation_id=conversation.id))
        run.conversation_id = conversation.id
    run.request_id = str(uuid4())
    run.lease_until = now + lease_seconds
    prompt = ('예약 작업 실행입니다. 아래 요청을 현재 시각에 한 번 수행하고 결과를 이 대화에 남겨 주세요. '
              '추가 예약을 만들거나 외부 상태를 변경하지 마세요.\n\n' + row.prompt)
    return ClaimedRun(run.request_id, conversation.id, row.user_id, row.connection_id, row.model, prompt, run.lease_until)


def claim(bind, *, now=None):
    now = int(time.time()) if now is None else now
    settings = get_settings()
    lease_seconds = (settings.llm_queue_wait_seconds + max(settings.terminal_agent_timeout_seconds,
        settings.llm_timeout_seconds * 2, settings.image_queue_wait_seconds + settings.image_generation_timeout_seconds) + 60)
    with Session(bind) as db:
        # Idle API processes do not contend for the chat write lock every poll.
        pending = db.scalar(select(ScheduleRun.id).where(ScheduleRun.status == 'running',
            ScheduleRun.lease_until <= now).limit(1)) or db.scalar(select(Schedule.id).where(
                Schedule.status == 'active', Schedule.next_run_at <= now).limit(1))
        db.rollback()
        if not pending:
            return None
        scheduler.lock(db)
        expired = db.execute(select(ScheduleRun, Schedule).join(Schedule).where(
            ScheduleRun.status == 'running', ScheduleRun.lease_until <= now,
        ).order_by(ScheduleRun.scheduled_at).limit(12)).all()
        for run, row in expired:
            result = generation_result(db, run, expired_at=now, cancelled=row.status == 'cancelled')
            if result:
                finish_locked(db, row, run, *result, now)
            elif row.status != 'active':
                finish_locked(db, row, run, 'cancelled', CANCELLED, now)
            elif item := issue(db, row, run, now, lease_seconds):
                db.commit()
                return item
        active_runs = select(ScheduleRun.id).where(ScheduleRun.status == 'running')
        if db.scalar(select(func.count()).select_from(active_runs.subquery())) >= MAX_GLOBAL_RUNS:
            db.commit()
            return None
        due = db.scalars(select(Schedule).where(Schedule.status == 'active', Schedule.next_run_at <= now,
            ~exists().where(ScheduleRun.schedule_id == Schedule.id, ScheduleRun.status == 'running'),
        ).order_by(Schedule.next_run_at, Schedule.id).limit(30)).all()
        for row in due:
            count = db.scalar(select(func.count()).select_from(ScheduleRun).join(Schedule).where(
                ScheduleRun.status == 'running', Schedule.user_id == row.user_id))
            if count >= MAX_USER_RUNS:
                continue
            scheduled_at = row.next_run_at
            row.next_run_at = (None if row.repeat == 'once' else intent.next_recurrence(
                scheduled_at, row.repeat, row.time_zone, row.local_minute, now))
            run = ScheduleRun(schedule_id=row.id, request_id=str(uuid4()), scheduled_at=scheduled_at,
                              started_at=now, lease_until=now, status='running')
            db.add(run)
            if item := issue(db, row, run, now, lease_seconds):
                db.commit()
                return item
        db.commit()
        return None


def finish(bind, request_id, *, detail=None, retry=False):
    with Session(bind) as db:
        scheduler.lock(db)
        run = db.scalar(select(ScheduleRun).where(ScheduleRun.request_id == request_id, ScheduleRun.status == 'running'))
        if not run:
            return
        row = db.get(Schedule, run.schedule_id)
        now = int(time.time())
        if row.status == 'active':
            try:
                verify_scope(db, row)
            except HTTPException as error:
                finish_locked(db, row, run, 'error', str(error.detail), now)
                row.status, row.next_run_at = 'complete', None
                db.commit()
                return
        result = generation_result(db, run)
        if result:
            finish_locked(db, row, run, *result, now)
        elif row.status != 'active':
            finish_locked(db, row, run, 'cancelled', CANCELLED, now)
        elif retry and now < run.started_at + get_settings().llm_queue_wait_seconds and not db.get(GenerationRun, request_id):
            # If admission could not start, keep the same result conversation and retry later.
            run.lease_until = now + QUEUE_RETRY_SECONDS
        else:
            finish_locked(db, row, run, 'error', (detail or '예약 작업을 실행하지 못했습니다.')[:300], now)
        db.commit()


async def worker(bind, stop, run_turn):
    async def execute(run):
        detail, retry = None, False
        try:
            await run_turn(bind, run.user_id, run.conversation_id, run.request_id, run.connection_id, run.model, run.prompt)
        except asyncio.CancelledError:
            # Leave an unstarted run recoverable; a started turn writes its own cancelled status.
            raise
        except HTTPException as error:
            detail, retry = str(error.detail), error.status_code == 429
        except Exception:
            logger.exception('Scheduled chat failed')
            detail = '예약 작업을 실행하지 못했습니다. 모델 연결과 사용 권한을 확인해 주세요.'
        finalization = asyncio.create_task(asyncio.to_thread(finish, bind, run.request_id, detail=detail, retry=retry))
        try:
            await asyncio.shield(finalization)
        except asyncio.CancelledError:
            await asyncio.gather(finalization, return_exceptions=True)
            raise

    tasks = set()
    pending_claim = None
    try:
        while not stop.is_set():
            done = {task for task in tasks if task.done()}
            for task in done:
                if not task.cancelled() and (error := task.exception()):
                    logger.error('Scheduled chat finalization failed', exc_info=error)
            tasks -= done
            try:
                while not stop.is_set() and len(tasks) < MAX_GLOBAL_RUNS:
                    pending_claim = asyncio.create_task(asyncio.to_thread(claim, bind))
                    run = await asyncio.shield(pending_claim)
                    pending_claim = None
                    if run is None:
                        break
                    tasks.add(asyncio.create_task(execute(run)))
            except Exception:
                logger.exception('Schedule worker could not claim due work')
            try:
                await asyncio.wait_for(stop.wait(), POLL_SECONDS)
            except TimeoutError:
                pass
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if pending_claim is not None:
            # A DB transaction in a worker thread cannot be cancelled by the event loop.
            # Let it commit before the API disposes its engine; the lease remains recoverable.
            await asyncio.gather(pending_claim, return_exceptions=True)
