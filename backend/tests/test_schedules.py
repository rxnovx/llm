import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
import time
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo

from fastapi import HTTPException
import pytest
from sqlalchemy import select

from app.modules.auth.models import User
from app.modules.chat.models import Conversation, GenerationRun, Message
from app.modules.chat.router import prepare, scheduled_turn, TurnInput
from app.modules.chat import providers
from app.modules.schedules import intent, service, worker
from app.modules.schedules.models import Schedule, ScheduleClarification, ScheduleRun
from app.modules.workflows.models import ProjectConversation


SEOUL = ZoneInfo('Asia/Seoul')


def auth(session):
    return {'Authorization': 'Bearer ' + session['access_token']}


def send(client, headers, conversation, content, *, retry=False):
    response = client.post(f'/chat/conversations/{conversation}/messages', headers=headers,
                           json={'content': content, 'request_id': str(uuid4()), 'retry': retry})
    assert response.status_code == 200, response.text
    events = [(lines[0][7:], json.loads(lines[1][6:])) for part in response.text.strip().split('\n\n')
              if len(lines := part.splitlines()) == 2 and lines[0].startswith('event: ')]
    assert events[-1][0] == 'done', events[-1]
    return events[-1][1]['message']['content']


def open_chat(client, headers, project_id=None):
    response = client.post('/chat/conversations', headers=headers, json={'project_id': project_id})
    assert response.status_code == 201, response.text
    return response.json()['id']


def list_schedules(client, headers):
    response = client.get('/workflows/schedules', headers=headers)
    assert response.status_code == 200, response.text
    return response.json()['items']


def due(client, schedule_id):
    with client.db_factory() as db:
        row = db.get(Schedule, schedule_id)
        row.next_run_at = int(time.time()) - 10
        db.commit()


async def run_claim(engine, claim):
    await scheduled_turn(engine, claim.user_id, claim.conversation_id, claim.request_id,
                         claim.connection_id, claim.model, claim.prompt)
    worker.finish(engine, claim.request_id)


@pytest.mark.parametrize(('question', 'repeat', 'expected'), [
    ('매주 월요일 오전 9시에 사내 규정 변경을 확인해 줘', 'weekly', datetime(2026, 11, 16, 9, tzinfo=SEOUL)),
    ('내일 오전 9시 반에 내부 규정 정리해 줘', 'once', datetime(2026, 11, 14, 9, 30, tzinfo=SEOUL)),
    ('매일 오후 12시에 재고를 알려 줘', 'daily', datetime(2026, 11, 14, 12, tzinfo=SEOUL)),
    ('3분 뒤 알림을 보내 줘', 'once', datetime(2026, 11, 13, 14, 8, tzinfo=SEOUL)),
    ('2026-11-14T09:30:00Z에 규정을 확인해 줘', 'once', datetime(2026, 11, 14, 9, 30, tzinfo=timezone.utc)),
    ('Check the logs every Monday at 9 am UTC', 'weekly', datetime(2026, 11, 16, 9, tzinfo=timezone.utc)),
])
def test_schedule_uses_user_calendar_details(question, repeat, expected):
    now = int(datetime(2026, 11, 13, 14, 5, tzinfo=SEOUL).timestamp())
    assert intent.action_for(question) == 'create'
    occurrence = intent.parse_occurrence(question, SEOUL.key, now)
    assert occurrence.repeat == repeat and occurrence.timestamp == expected.timestamp()


@pytest.mark.parametrize('question', [
    '> 매일 오전 9시에 프로젝트를 확인해 줘',
    '```text\n내일 09:30에 자료를 확인해 줘\n```',
    '예약 목록에서 정리 작업 삭제하는 방법 알려 줘',
    '예약을 취소하지 말아 줘',
    '매일 9시에 예약하지 말고 그냥 테스트해 줘',
    'How can I schedule a reminder tomorrow at 9 am?',
    'Do not check the logs every Monday at 9 am',
])
def test_quotes_negation_and_instructions_do_not_create_or_cancel(question):
    assert not service.requested(question)


@pytest.mark.parametrize(('question', 'notice'), [
    ('매주 09:30에 자료를 확인해 줘', '요일'),
    ('내일 규정 확인하는 걸 예약해 줘', '시각'),
    ('매월 오전 9시에 결과를 정리해 줘', '반복 주기'),
    ('내일 09:00과 11:00에 규정을 알려 줘', '하나'),
    ('매일 09:00에 코드를 수정하고 커밋해 줘', '외부 변경'),
    ('2026-11-14T09:30:00+05:99에 규정을 확인해 줘', 'UTC 오프셋'),
    ('매일 09:00에 결과를 메일로 보내 줘', '외부 변경'),
])
def test_ambiguous_or_mutating_schedule_needs_actionable_feedback(question, notice):
    now = int(datetime(2026, 11, 13, 14, 5, tzinfo=SEOUL).timestamp())
    assert intent.action_for(question) == 'create'
    with pytest.raises(HTTPException, match=notice):
        intent.parse_occurrence(question, SEOUL.key, now)


def test_recurring_time_stays_local_and_skips_missed_intervals():
    zone = ZoneInfo('America/New_York')
    previous = int(datetime(2026, 3, 7, 9, tzinfo=zone).timestamp())
    due_after_downtime = int(datetime(2026, 3, 10, 12, tzinfo=zone).timestamp())
    scheduled = intent.next_recurrence(previous, 'daily', zone.key, 540, due_after_downtime)
    assert datetime.fromtimestamp(scheduled, zone) == datetime(2026, 3, 11, 9, tzinfo=zone)
    previous = int(datetime(2026, 3, 1, 2, 30, tzinfo=zone).timestamp())
    scheduled = intent.next_recurrence(previous, 'weekly', zone.key, 150, previous)
    assert datetime.fromtimestamp(scheduled, zone) == datetime(2026, 3, 8, 3, 30, tzinfo=zone)
    following = intent.next_recurrence(scheduled, 'weekly', zone.key, 150, scheduled)
    assert datetime.fromtimestamp(following, zone) == datetime(2026, 3, 15, 2, 30, tzinfo=zone)


def test_chat_schedule_lifecycle_and_owner_scope(client, account):
    headers, outsider = auth(account()), auth(account('other'))
    with client.db_factory() as db:
        db.scalar(select(User).where(User.username == 'alice')).time_zone = SEOUL.key
        db.commit()
    chat = open_chat(client, headers)
    question = '매주 월요일 오전 9시에 사내 규정 변경을 확인해 줘'
    answer = send(client, headers, chat, question)
    assert '예약했습니다.' in answer and 'Asia/Seoul' in answer
    assert '예약했습니다.' in send(client, headers, chat, question, retry=True)
    row, = list_schedules(client, headers)
    assert row['repeat'] == 'weekly' and row['status'] == 'active' and row['last_run'] is None
    next_run = datetime.fromtimestamp(row['next_run_at'], SEOUL)
    assert next_run.weekday() == 0 and (next_run.hour, next_run.minute) == (9, 0)
    assert list_schedules(client, outsider) == []
    assert client.get(f'/workflows/schedules/{row["id"]}/runs', headers=outsider).status_code == 404
    assert client.delete(f'/workflows/schedules/{row["id"]}', headers=outsider).status_code == 404
    listed = send(client, headers, chat, '내 예약 작업 보여 줘')
    assert row['id'] in listed and question in listed
    assert '예약을 취소했습니다.' in send(client, headers, chat, f'예약 `{row["id"]}` 취소해 줘')
    stopped, = list_schedules(client, headers)
    assert stopped['status'] == 'cancelled' and stopped['next_run_at'] is None


def test_chat_fills_missing_schedule_time_from_the_next_turn_only(client, account):
    headers = auth(account())
    chat = open_chat(client, headers)
    different_chat = open_chat(client, headers)
    question = '매주 월요일에 내부 자료를 확인해서 알려 줘'
    assert '실행할 시각' in send(client, headers, chat, question)
    assert list_schedules(client, headers) == []
    assert '예약했습니다.' not in send(client, headers, different_chat, '오전 9시로 해줘')
    assert '예약했습니다.' in send(client, headers, chat, '오전 9시로 해줘')
    row, = list_schedules(client, headers)
    assert row['repeat'] == 'weekly' and question in row['prompt']
    next_run = datetime.fromtimestamp(row['next_run_at'], ZoneInfo(row['time_zone']))
    assert (next_run.weekday(), next_run.hour, next_run.minute) == (0, 9, 0)
    assert '예약했습니다.' in send(client, headers, chat, '오전 9시로 해줘', retry=True)
    assert [item['id'] for item in list_schedules(client, headers)] == [row['id']]
    assert '실행할 시각' in send(client, headers, chat, question)
    assert '예약했습니다.' not in send(client, headers, chat, '오전 9시로 해줘. 파일을 삭제해 줘')
    assert '예약했습니다.' not in send(client, headers, chat, '오후 2시로 해줘')
    assert len(list_schedules(client, headers)) == 1


@pytest.mark.parametrize('change', ['expire', 'move_to_project'])
def test_pending_schedule_cannot_cross_expiry_or_project_scope(client, account, change):
    headers = auth(account())
    chat = open_chat(client, headers)
    assert '실행할 시각' in send(client, headers, chat, '매주 월요일에 내부 자료를 확인해서 알려 줘')
    if change == 'expire':
        with client.db_factory() as db:
            db.get(ScheduleClarification, chat).expires_at = int(time.time()) - 1
            db.commit()
    else:
        project = client.post('/workflows/projects', headers=headers, json={'name': '공유 프로젝트'}).json()
        response = client.patch(f'/workflows/conversations/{chat}/project', headers=headers,
                                json={'project_id': project['id']})
        assert response.status_code == 200, response.text
    assert '예약했습니다.' not in send(client, headers, chat, '오전 9시로 해줘')
    assert list_schedules(client, headers) == []


def test_worker_preserves_run_and_conversation_when_reclaiming_after_restart(client, account):
    headers = auth(account())
    chat = open_chat(client, headers)
    send(client, headers, chat, '2분 뒤 근무 규정 확인해 줘')
    row, = list_schedules(client, headers)
    due(client, row['id'])
    engine = client.db_factory.kw['bind']
    with ThreadPoolExecutor(max_workers=4) as executor:
        claimed = [run for run in executor.map(lambda _: worker.claim(engine), range(4)) if run]
    original, = claimed
    recovered = worker.claim(engine, now=original.lease_until + 1)
    assert recovered.request_id != original.request_id
    assert recovered.conversation_id == original.conversation_id
    asyncio.run(run_claim(engine, original))
    with client.db_factory() as db:
        assert not db.get(GenerationRun, original.request_id)
    asyncio.run(run_claim(engine, recovered))
    row, = list_schedules(client, headers)
    assert row['status'] == 'complete'
    assert row['last_run']['status'] == 'complete'
    assert row['last_run']['conversation_id'] == recovered.conversation_id
    result = client.get(f'/chat/conversations/{recovered.conversation_id}', headers=headers).json()
    assert [message['role'] for message in result['messages']] == ['user', 'assistant']
    assert '예약 작업 실행' in result['messages'][0]['content']
    assert result['messages'][1]['status'] == 'complete'
    assert '예약했습니다.' not in result['messages'][1]['content']
    history = client.get(f'/workflows/schedules/{row["id"]}/runs', headers=headers).json()['items']
    assert len(history) == 1


def test_interrupted_started_run_is_finalized_without_a_second_invocation(client, account):
    headers = auth(account())
    chat = open_chat(client, headers)
    send(client, headers, chat, '2분 뒤 실행 결과를 확인해 줘')
    row, = list_schedules(client, headers)
    due(client, row['id'])
    engine = client.db_factory.kw['bind']
    claimed = worker.claim(engine)
    with client.db_factory() as db:
        data = TurnInput(content=claimed.prompt, request_id=claimed.request_id, model=claimed.model)
        prepare(db, UUID(claimed.conversation_id), claimed.user_id, data, providers.Connection(
            id='demo', name='테스트 모드', provider='test', model='demo'), scheduled=True)
    assert worker.claim(engine, now=claimed.lease_until + 1) is None
    row, = list_schedules(client, headers)
    assert row['status'] == 'complete' and row['last_run']['status'] == 'error'
    assert row['last_run']['error'] == worker.INTERRUPTED
    with client.db_factory() as db:
        assert db.get(GenerationRun, claimed.request_id).status == 'error'
        assert not db.get(Conversation, claimed.conversation_id).generation_id
        assert db.scalar(select(Message).where(Message.conversation_id == claimed.conversation_id,
            Message.role == 'assistant')).status == 'error'


def test_cancel_after_claim_prevents_model_execution(client, account):
    headers = auth(account())
    chat = open_chat(client, headers)
    send(client, headers, chat, '2분 뒤 규정을 정리해 줘')
    row, = list_schedules(client, headers)
    due(client, row['id'])
    engine = client.db_factory.kw['bind']
    run = worker.claim(engine)
    result = client.delete(f'/workflows/schedules/{row["id"]}', headers=headers).json()['schedule']
    assert result['status'] == 'cancelled' and result['last_run']['status'] == 'cancelled'
    asyncio.run(run_claim(engine, run))
    with client.db_factory() as db:
        assert not db.get(GenerationRun, run.request_id)


def test_worker_executes_due_schedule_in_background_and_preserves_recurrence(client, account, monkeypatch):
    monkeypatch.setattr(worker, 'POLL_SECONDS', 0.01)
    headers = auth(account())
    chat = open_chat(client, headers)
    send(client, headers, chat, '매일 오전 9시에 내부 규정을 정리해 줘')
    row, = list_schedules(client, headers)
    due(client, row['id'])
    engine = client.db_factory.kw['bind']

    async def wait_for_result():
        stop = asyncio.Event()
        task = asyncio.create_task(worker.worker(engine, stop, scheduled_turn))
        try:
            async with asyncio.timeout(5):
                while True:
                    with client.db_factory() as db:
                        run = db.scalar(select(ScheduleRun.status).where(ScheduleRun.schedule_id == row['id']))
                    if run == 'complete':
                        return
                    await asyncio.sleep(0.01)
        finally:
            stop.set()
            await task

    asyncio.run(wait_for_result())
    row, = list_schedules(client, headers)
    assert row['status'] == 'active' and row['last_run']['status'] == 'complete'
    assert row['next_run_at'] > time.time()
    assert row['last_run']['conversation_id'] != chat
    assert worker.claim(engine) is None


def test_project_schedule_never_falls_back_to_personal_scope(client, account):
    headers = auth(account())
    project = client.post('/workflows/projects', headers=headers, json={'name': '공유 프로젝트'}).json()
    private_chat = open_chat(client, headers)
    send(client, headers, private_chat, '2분 뒤 개인 규정을 정리해 줘')
    project_chat = open_chat(client, headers, project['id'])
    assert '개인 대화' in send(client, headers, project_chat, '내 예약 작업 보여 줘')
    send(client, headers, project_chat, '2분 뒤 프로젝트 규정을 정리해 줘')
    schedule = next(row for row in list_schedules(client, headers) if '프로젝트 규정' in row['title'])
    due(client, schedule['id'])
    engine = client.db_factory.kw['bind']
    claim = worker.claim(engine)
    with client.db_factory() as db:
        assert db.get(ProjectConversation, claim.conversation_id).project_id == project['id']
    assert client.delete(f'/workflows/projects/{project["id"]}', headers=headers).status_code == 200
    with pytest.raises(HTTPException):
        asyncio.run(run_claim(engine, claim))
    worker.finish(engine, claim.request_id)
    with client.db_factory() as db:
        assert not db.get(GenerationRun, claim.request_id)
        assert db.get(Schedule, schedule['id']).status == 'complete'
        assert db.scalar(select(ScheduleRun.status).where(ScheduleRun.schedule_id == schedule['id'])) == 'error'


def test_project_revocation_discards_a_scheduled_answer_already_in_progress(client, account, monkeypatch):
    owner, editor = auth(account()), auth(account('editor'))
    project = client.post('/workflows/projects', headers=owner, json={'name': '공유 프로젝트'}).json()
    members_path = f'/workflows/projects/{project["id"]}/members'
    member = client.post(members_path, headers=owner, json={'username': 'editor', 'role': 'editor'})
    assert member.status_code == 200, member.text
    chat = open_chat(client, editor, project['id'])
    send(client, editor, chat, '2분 뒤 업무 계획을 정리해 줘')
    schedule, = list_schedules(client, editor)
    due(client, schedule['id'])
    engine = client.db_factory.kw['bind']
    claim = worker.claim(engine)
    received = asyncio.Event()
    resume = asyncio.Event()
    private_output = '아직 게시되지 않은 프로젝트 답변입니다.'

    async def reply(*_args, **_kwargs):
        yield private_output
        received.set()
        await resume.wait()
        yield ' 추가 내용입니다.'

    monkeypatch.setattr(providers, 'stream_reply', reply)

    async def revoke_while_running():
        async with asyncio.TaskGroup() as group:
            group.create_task(run_claim(engine, claim))
            async with asyncio.timeout(5):
                await received.wait()
                running = await asyncio.to_thread(client.get, f'/chat/conversations/{claim.conversation_id}', headers=editor)
                assert running.status_code == 200
                assert running.json()['messages'][-1]['content'] == ''
                response = await asyncio.to_thread(client.delete, f'{members_path}/{member.json()["id"]}', headers=owner)
                assert response.status_code == 200, response.text
                resume.set()

    asyncio.run(revoke_while_running())
    result = client.get(f'/chat/conversations/{claim.conversation_id}', headers=editor).json()['messages'][-1]
    assert result['status'] == 'cancelled' and result['content'] == ''
    assert result['sources'] == [] and result['artifacts'] == []
    row, = list_schedules(client, editor)
    assert row['status'] == 'complete' and row['last_run']['status'] == 'error'
