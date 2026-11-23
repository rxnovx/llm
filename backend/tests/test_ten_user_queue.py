"""공유 SQLite 대기열의 다중 사용자 경합과 늦은 콜백을 실제 세션으로 검증한다."""

from concurrent.futures import ThreadPoolExecutor
import asyncio
import json
import threading
import time

import pytest
import httpx
from sqlalchemy import func, select

from app.core.config import get_settings
from app.modules.chat import providers, scheduler
from app.modules.chat.models import Admission, GenerationRun
from test_chat import auth, events, send
from test_model_controls import configure, pending


def test_ten_users_share_fifo_model_limit_without_starvation(client, account, monkeypatch):
    configure(monkeypatch, max_concurrent=3)
    monkeypatch.setenv('LLM_QUEUE_CAPACITY', '30')
    monkeypatch.setenv('LLM_USER_CONCURRENCY', '1')
    get_settings.cache_clear()
    settings = get_settings()
    turns = [pending(client, account(f'worker{index}')) for index in range(10)]
    running = set()
    started = []
    lock = threading.Lock()
    high_water = 0
    start = time.monotonic()

    def process(turn):
        nonlocal high_water
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            with client.db_factory() as db:
                state, _, _ = scheduler.poll(db, turn.request_id, settings, providers.resolve)
                db.commit()
            if state == 'running':
                with lock:
                    running.add(turn.request_id)
                    started.append(turn.request_id)
                    high_water = max(high_water, len(running))
                # 여러 프로세스가 공유하는 추론 슬롯을 짧은 실제 작업으로 점유한다.
                time.sleep(0.03)
                with lock:
                    running.remove(turn.request_id)
                with client.db_factory() as db:
                    scheduler.lock(db)
                    scheduler.finish(db, turn.request_id, 'complete')
                    db.get(GenerationRun, turn.request_id).status = 'complete'
                    db.commit()
                return
            assert state == 'queued'
            time.sleep(0.005)
        pytest.fail('10명 중 대기열에서 완료되지 못한 사용자가 있습니다.')

    with ThreadPoolExecutor(max_workers=10) as executor:
        for result in executor.map(process, turns):
            assert result is None
    elapsed = time.monotonic() - start
    assert high_water == 3
    # 슬롯 할당 순서는 DB FIFO가 보장한다. 스레드의 Python 실행 재개 순서는 다를 수 있다.
    assert len(set(started)) == 10
    assert not running
    with client.db_factory() as db:
        assert db.scalar(select(func.count(Admission.id)).where(Admission.state == 'complete')) == 10
        assert not db.scalar(select(Admission.id).where(Admission.state.in_(scheduler.ACTIVE_STATES)))
    print(f'local SQLite queue: users=10 completed=10 max_model_concurrency={high_water} elapsed_s={elapsed:.3f}')


def test_tool_wait_releases_slot_and_rejoins_after_older_queued_requests(client, account, monkeypatch):
    configure(monkeypatch)
    first, second, third = [pending(client, account(f'fifo{index}')) for index in range(3)]
    settings = get_settings()
    with client.db_factory() as db:
        assert scheduler.poll(db, first.request_id, settings, providers.resolve)[0] == 'running'
        assert scheduler.release_for_tools(db, first.request_id, int(time.time()) + 60)
        db.commit()
    with client.db_factory() as db:
        assert scheduler.poll(db, first.request_id, settings, providers.resolve)[0] == 'tool_waiting'
        assert scheduler.poll(db, second.request_id, settings, providers.resolve)[0] == 'running'
        assert scheduler.requeue(db, first.request_id, settings)
        scheduler.finish(db, second.request_id, 'complete')
        db.commit()
    with client.db_factory() as db:
        assert scheduler.poll(db, first.request_id, settings, providers.resolve)[0] == 'queued'
        assert scheduler.poll(db, third.request_id, settings, providers.resolve)[0] == 'running'
        db.commit()


def test_ten_chat_streams_complete_and_remain_private_under_a_shared_model_limit(client, account, monkeypatch):
    configure(monkeypatch, max_concurrent=3)
    monkeypatch.setenv('LLM_QUEUE_CAPACITY', '30')
    monkeypatch.setenv('ASSISTANT_AUTOMATIC_ENABLED', 'false')
    get_settings.cache_clear()
    identities = [account(f'chatload{index}') for index in range(10)]
    conversations = []
    for identity in identities:
        response = client.post('/chat/conversations', headers=auth(identity),
            json={'connection_id': 'office', 'model': 'model-a'})
        assert response.status_code == 201, response.text
        conversations.append(response.json()['id'])
    active = 0
    high_water = 0
    closed = 0
    guard = threading.Lock()

    class ResponseBody(httpx.AsyncByteStream):
        def __init__(self):
            self.opened = False

        async def aclose(self):
            nonlocal active, closed
            # HTTPX가 [DONE] 이후 호출하는 명시적 종료를 계측한다.
            # 기본 AsyncByteStream.aclose는 비어 있어 generator GC로 세면 늦게 감소한다.
            with guard:
                if self.opened:
                    self.opened = False
                    active -= 1
                    closed += 1

        async def __aiter__(self):
            nonlocal active, high_water
            with guard:
                self.opened = True
                active += 1
                high_water = max(high_water, active)
            try:
                for text in ['안녕하세요. ', '동시 채팅이 정상 동작합니다.']:
                    value = {'choices': [{'delta': {'content': text}, 'finish_reason': None}]}
                    yield ('data: ' + json.dumps(value, ensure_ascii=False) + '\n\n').encode()
                    await asyncio.sleep(0.04)
                yield b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\ndata: [DONE]\n\n'
            finally:
                await self.aclose()

    def handle(request):
        assert request.method == 'POST' and request.url.path == '/v1/chat/completions'
        assert json.loads(request.content)['stream']
        with client.db_factory() as db:
            assert db.scalar(select(func.count(Admission.id)).where(Admission.state == 'running')) <= 3
        return httpx.Response(200, stream=ResponseBody())

    monkeypatch.setattr(providers, 'client', lambda *args, **kwargs:
        httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    started = time.monotonic()

    def submit(index):
        response = send(client, auth(identities[index]), conversations[index],
            content=f'안녕, {index}번째 사용자야.', connection_id='office', model='model-a')
        assert response.status_code == 200, response.text
        stream = events(response)
        assert stream[-1][0] == 'done', stream[-1]
        assert ''.join(value['content'] for kind, value in stream if kind == 'delta') == '안녕하세요. 동시 채팅이 정상 동작합니다.'

    with ThreadPoolExecutor(max_workers=10) as executor:
        list(executor.map(submit, range(10)))
    elapsed = time.monotonic() - started
    assert high_water == 3
    assert active == 0
    assert closed == 10
    for index, identity in enumerate(identities):
        snapshot = client.get(f'/chat/conversations/{conversations[index]}', headers=auth(identity))
        assert snapshot.status_code == 200
        assert snapshot.json()['messages'][-1]['content'] == '안녕하세요. 동시 채팅이 정상 동작합니다.'
        assert not snapshot.json()['conversation']['busy']
        other = identities[(index + 1) % 10]
        assert client.get(f'/chat/conversations/{conversations[index]}', headers=auth(other)).status_code == 404
    print(f'mock SSE model: users=10 completed=10 peak_calls={high_water} elapsed_s={elapsed:.3f}')


@pytest.mark.parametrize('terminal', ['complete', 'cancelled', 'error'])
@pytest.mark.parametrize('independent', [False, True])
def test_finished_attempt_cannot_be_requeued_or_acquire_a_tool_lease(
        client, account, monkeypatch, terminal, independent):
    configure(monkeypatch)
    turn = pending(client, account())
    settings = get_settings()
    with client.db_factory() as db:
        run = db.get(GenerationRun, turn.request_id)
        if independent:
            run.conversation_id = None
        scheduler.finish(db, turn.request_id, terminal)
        run.status = terminal
        db.commit()
    with client.db_factory() as db:
        assert scheduler.requeue(db, turn.request_id, settings) is False
        assert scheduler.release_for_tools(db, turn.request_id, int(time.time()) + 60) is False
        assert scheduler.poll(db, turn.request_id, settings, providers.resolve, independent=independent)[0] == 'cancelled'
        db.commit()
        admission = db.scalar(select(Admission).where(Admission.request_id == turn.request_id))
        assert admission.state == terminal
        assert db.get(GenerationRun, turn.request_id).status == terminal


def test_terminal_admission_alone_blocks_a_stale_callback(client, account, monkeypatch):
    configure(monkeypatch)
    turn = pending(client, account())
    with client.db_factory() as db:
        scheduler.finish(db, turn.request_id, 'cancelled')
        # 이전 작업자의 run 상태가 아직 갱신되지 않은 경합도 취소 상태를 우선한다.
        assert db.get(GenerationRun, turn.request_id).status == 'queued'
        assert scheduler.release_for_tools(db, turn.request_id, int(time.time()) + 60) is False
        assert scheduler.requeue(db, turn.request_id, get_settings()) is False
        assert scheduler.poll(db, turn.request_id, get_settings(), providers.resolve)[0] == 'cancelled'
        db.commit()
