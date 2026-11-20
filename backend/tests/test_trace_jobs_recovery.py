"""SSE 연결 수명과 백그라운드 작업 수명이 분리되는지 검증한다."""

import asyncio
import time

from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import get_settings
from app.modules.assistant import parallel
from app.modules.auth.models import User
from app.modules.chat import providers, scheduler
from app.modules.chat.jobs import GenerationJobs, Subscription
from app.modules.chat.models import Admission, Conversation, GenerationRun, Message
from test_chat import auth
from test_model_controls import configure, pending


def test_disconnecting_sse_does_not_cancel_the_generation_job():
    async def scenario():
        jobs = GenerationJobs()
        resume = asyncio.Event()
        completed = asyncio.Event()

        async def generate():
            yield 'event: start\ndata: {}\n\n'
            await resume.wait()
            # 실제 turn_events는 이 지점에서 답변과 기록을 DB에 체크포인트한다.
            completed.set()
            yield 'event: done\ndata: {}\n\n'

        subscription = jobs.start(generate())
        stream = subscription.stream()
        assert 'event: start' in await anext(stream)
        await stream.aclose()
        assert not subscription.listening
        assert len(jobs.tasks) == 1
        resume.set()
        await asyncio.wait_for(completed.wait(), timeout=1)
        await asyncio.gather(*list(jobs.tasks))
        assert not jobs.tasks
        await jobs.close()

    asyncio.run(scenario())


def test_slow_sse_subscriber_is_bounded_without_blocking_other_work():
    async def scenario():
        jobs = GenerationJobs()
        complete = asyncio.Event()

        async def generate():
            for index in range(600):
                yield str(index)
            complete.set()

        subscription = jobs.start(generate())
        await asyncio.wait_for(complete.wait(), timeout=2)
        await asyncio.gather(*list(jobs.tasks))
        assert not subscription.listening
        assert subscription.queue.qsize() == 1
        assert await anext(subscription.stream(), None) is None
        await jobs.close()

    asyncio.run(scenario())


def test_shutdown_cancels_running_jobs_and_runs_generator_cleanup():
    async def scenario():
        jobs = GenerationJobs()
        began = asyncio.Event()
        cleaned = asyncio.Event()

        async def generate():
            try:
                began.set()
                await asyncio.Event().wait()
                yield ''
            finally:
                cleaned.set()

        subscription = jobs.start(generate())
        await asyncio.wait_for(began.wait(), timeout=1)
        await asyncio.wait_for(jobs.close(), timeout=1)
        assert cleaned.is_set()
        assert not jobs.tasks
        assert await anext(subscription.stream(), None) is None

    asyncio.run(scenario())


def test_unhandled_job_failure_closes_the_sse_subscription(caplog):
    async def scenario():
        jobs = GenerationJobs()

        async def generate():
            yield 'event: start\ndata: {}\n\n'
            raise RuntimeError('access_token=never-log-test-token')

        subscription = jobs.start(generate())
        await asyncio.gather(*list(jobs.tasks))
        assert [item async for item in subscription.stream()] == ['event: start\ndata: {}\n\n']
        assert not jobs.tasks

    asyncio.run(scenario())
    assert 'Chat generation job failed' in caplog.text
    assert 'RuntimeError' in caplog.text
    assert 'never-log-test-token' not in caplog.text


def test_restart_recovers_tool_checkpoint_without_replaying_the_attempt(client, account, monkeypatch):
    configure(monkeypatch)
    identity = account()
    turn = pending(client, identity)
    with client.db_factory() as db:
        assert scheduler.poll(db, turn.request_id, get_settings(), providers.resolve)[0] == 'running'
        assert scheduler.release_for_tools(db, turn.request_id, int(time.time()) + 60)
        conversation = db.get(Conversation, turn.conversation_id)
        conversation.generation_until = int(time.time()) - 1
        db.get(Message, turn.assistant_id).content = '저장된 분석 결과'
        db.scalar(select(Admission).where(Admission.request_id == turn.request_id)).expires_at = int(time.time()) - 1
        db.commit()
    # 새 프로세스에는 실행 코루틴이 없다. 조회는 저장된 결과를 복구하되 작업을 재실행하지 않는다.
    monkeypatch.setattr(providers, 'stream_reply', lambda *args, **kwargs:
        (_ for _ in ()).throw(AssertionError('중단된 쓰기 작업을 자동 재실행하면 안 됩니다.')))
    response = client.get(f'/chat/conversations/{turn.conversation_id}', headers=auth(identity))
    assert response.status_code == 200
    result = response.json()
    assert result['generation'] is None
    message = result['messages'][-1]
    assert message['status'] == 'error'
    assert message['content'] == '저장된 분석 결과'
    assert message['recovery']['code'] == 'interrupted'
    assert not message['recovery']['can_resume_execution']
    with client.db_factory() as db:
        assert db.get(GenerationRun, turn.request_id).status == 'error'
        assert scheduler.requeue(db, turn.request_id, get_settings()) is False
        db.commit()


def test_parallel_source_guard_rechecks_each_worker_after_model_admission(client, account, monkeypatch):
    identity = account()
    user_id = identity['user']['subject']
    settings = get_settings()
    with client.db_factory() as db:
        bind = db.get_bind()
        connection = providers.resolve(settings, 'demo', db, db.get(User, user_id))
    validations = {}

    async def validate():
        worker = asyncio.current_task()
        validations[worker] = validations.get(worker, 0) + 1
        if validations[worker] == 2:
            raise HTTPException(409, '모델 대기 중 자료의 접근 권한이 회수되었습니다.')

    async def forbidden(*args, **kwargs):
        raise AssertionError('접근 권한이 회수된 자료를 병렬 모델에 전달하면 안 됩니다.')
        yield ''

    monkeypatch.setattr(providers, 'stream_reply', forbidden)

    async def scenario():
        tasks = [parallel.Assignment(name=f'분석{index}', question='자료를 분석하세요.') for index in range(2)]
        return [result async for result in parallel.results(bind, user_id, connection, 'demo', settings,
            tasks, '독립 관점에서 분석해줘.', ['조회된 자료'], lambda: False, validate=validate)]

    results = asyncio.run(scenario())
    assert len(results) == 2 and all(result['status'] == 'error' for result in results)
    assert all('접근 권한' in result['error'] for result in results)
    assert sorted(validations.values()) == [2, 2]
    with client.db_factory() as db:
        assert not db.scalar(select(Admission.id).where(Admission.state.in_(scheduler.ACTIVE_STATES)))
        assert {run.status for run in db.scalars(select(GenerationRun))} == {'error'}
