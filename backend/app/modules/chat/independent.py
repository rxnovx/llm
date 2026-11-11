"""Model calls outside a browser conversation share the same admission queue."""

import asyncio
from contextlib import asynccontextmanager
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from . import providers, scheduler
from .models import GenerationRun


class RequestCancelled(Exception):
    pass


@asynccontextmanager
async def admitted(bind, user_id, connection, model, settings, *, guard=None):
    request_id = str(uuid4())
    started = int(time.time())
    with Session(bind) as db:
        scheduler.lock(db)
        run = GenerationRun(
            id=request_id, user_id=user_id, conversation_id=None, connection_id=connection.id,
            provider=connection.provider, model=model, is_test=connection.provider == 'test',
            status='queued', started_at=started, deadline_at=started + settings.llm_queue_wait_seconds,
        )
        db.add(run)
        db.flush()
        scheduler.enqueue(db, run, settings)
        db.commit()
    status = 'error'
    try:
        while True:
            with Session(bind) as db:
                state, _, selected = scheduler.poll(db, request_id, settings, providers.resolve, independent=True)
                if guard:
                    guard(db)
                db.commit()
            if state == 'running':
                break
            if state != 'queued':
                raise HTTPException(429, '모델 대기 시간이 끝났거나 요청이 취소되었습니다.')
            await asyncio.sleep(0.25)
        async with asyncio.timeout(settings.llm_timeout_seconds):
            yield selected, request_id
        status = 'complete'
    except (asyncio.CancelledError, GeneratorExit, RequestCancelled):
        status = 'cancelled'
        raise
    finally:
        with Session(bind) as db:
            scheduler.lock(db)
            scheduler.finish(db, request_id, status)
            run = db.get(GenerationRun, request_id)
            if run:
                run.status, run.finished_at = status, int(time.time())
                run.duration_ms = (run.finished_at - started) * 1000
            db.commit()
