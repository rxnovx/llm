"""Schedule management and authorization for scheduled chats."""
from datetime import datetime
import re
import time
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from sqlalchemy import case, delete, exists, func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.assistant.gitlab import unquoted
from app.modules.auth.models import User
from app.modules.chat import providers, scheduler
from app.modules.chat.models import Conversation, Message
from app.modules.workflows import conversations
from app.modules.workflows.models import ProjectConversation
from app.modules.workflows.projects import project_access
from . import intent
from .models import Schedule, ScheduleClarification, ScheduleRun


MAX_ACTIVE = 10
MAX_SCHEDULES = 50
MAX_RUN_HISTORY = 30
CLARIFICATION_SECONDS = 30 * 60
CANCELLED = '사용자가 예약을 취소했습니다.'


def requested(question):
    return intent.action_for(question) is not None


def run_data(row):
    return {key: getattr(row, key) for key in (
        'id', 'status', 'scheduled_at', 'started_at', 'finished_at', 'conversation_id', 'error',
    )}


def schedule_data(db, row):
    latest = db.scalar(select(ScheduleRun).where(ScheduleRun.schedule_id == row.id)
                       .order_by(ScheduleRun.scheduled_at.desc()).limit(1))
    return public_schedule(row, latest)


def public_schedule(row, latest):
    return {key: getattr(row, key) for key in (
        'id', 'title', 'prompt', 'status', 'repeat', 'time_zone', 'next_run_at', 'created_at',
    )} | {'last_run': run_data(latest) if latest else None}


def schedules_for(db, user_id):
    last_id = select(ScheduleRun.id).where(ScheduleRun.schedule_id == Schedule.id).order_by(
        ScheduleRun.scheduled_at.desc(), ScheduleRun.id).limit(1).correlate(Schedule).scalar_subquery()
    rows = db.execute(select(Schedule, ScheduleRun).outerjoin(ScheduleRun, ScheduleRun.id == last_id)
        .where(Schedule.user_id == user_id).order_by(
        case((Schedule.status == 'active', 0), else_=1), Schedule.created_at.desc(), Schedule.id,
    ).limit(MAX_SCHEDULES))
    return [public_schedule(row, latest) for row, latest in rows]


def owned(db, user_id, schedule_id):
    row = db.get(Schedule, str(schedule_id))
    if not row or row.user_id != user_id:
        raise HTTPException(404, '예약 작업을 찾을 수 없습니다.')
    return row


def runs_for(db, user_id, schedule_id):
    row = owned(db, user_id, schedule_id)
    rows = db.scalars(select(ScheduleRun).where(ScheduleRun.schedule_id == row.id)
                      .order_by(ScheduleRun.scheduled_at.desc()).limit(MAX_RUN_HISTORY))
    return [run_data(item) for item in rows]


def active_user(db, user_id):
    user = db.get(User, user_id)
    if not user or not user.is_active or user.registration_status != 'approved':
        raise HTTPException(403, '예약을 실행할 수 있는 계정 상태인지 확인해 주세요.')
    return user


def model_available(db, user, connection_id, model):
    try:
        connection = providers.resolve(get_settings(), connection_id, db, user)
        connection.profile(model)
    except providers.ProviderError as error:
        raise HTTPException(422, '예약에 지정된 모델을 사용할 수 없습니다. ' + str(error)) from None


def scoped_schedules(user_id, project_id):
    return select(Schedule).where(Schedule.user_id == user_id, Schedule.project_id == project_id)


def conversation_project(db, conversation_id):
    link = db.get(ProjectConversation, str(conversation_id))
    return link.project_id if link else None


def pending_request(db, user_id, conversation_id, question, *, before_message_id=None):
    details = intent.clarification_details(question)
    if not details:
        return None
    row = db.get(ScheduleClarification, str(conversation_id))
    if (not row or row.user_id != user_id or row.expires_at <= time.time() or
            row.project_id != conversation_project(db, conversation_id)):
        return None
    latest = select(Message.id, Message.status).where(Message.conversation_id == str(conversation_id), Message.role == 'assistant')
    if before_message_id:
        position = select(Message.position).where(Message.id == str(before_message_id),
            Message.conversation_id == str(conversation_id)).scalar_subquery()
        latest = latest.where(Message.position < position)
    previous = db.execute(latest.order_by(Message.position.desc()).limit(1)).first()
    if previous and previous.id == row.assistant_id and previous.status == 'complete':
        return row.prompt + '\n' + details
    return None


def pending_requested(db, user_id, conversation_id, question, *, before_message_id=None):
    return pending_request(db, user_id, conversation_id, question, before_message_id=before_message_id) is not None


def pending_for_turn(bind, user_id, turn):
    with Session(bind) as db:
        return pending_requested(db, user_id, turn.conversation_id, turn.start['user_message']['content'],
                                 before_message_id=turn.assistant_id)


def save_clarification(db, user_id, turn, question, now):
    project_id = conversation_project(db, turn.conversation_id)
    row = db.get(ScheduleClarification, turn.conversation_id)
    if row is None:
        row = ScheduleClarification(conversation_id=turn.conversation_id, user_id=user_id)
        db.add(row)
    row.project_id = project_id
    row.assistant_id, row.prompt, row.expires_at = turn.assistant_id, question, now + CLARIFICATION_SECONDS


def make_title(question):
    text = ' '.join(question.split())
    return text if len(text) <= 80 else text[:79] + '…'


def create(db, user, conversation, message_id, question, now):
    project_id = conversation_project(db, conversation.id)
    if project_id:
        project_access(db, user.id, project_id, write=True)
    existing = db.scalar(scoped_schedules(user.id, project_id).where(Schedule.creation_key == message_id))
    if existing:
        return existing
    if db.scalar(select(Schedule.id).where(Schedule.creation_key == message_id)):
        raise HTTPException(409, '이 메시지의 예약은 다른 프로젝트 범위에 있습니다. 새 메시지로 다시 요청해 주세요.')
    scope = scoped_schedules(user.id, project_id)
    active_count = db.scalar(scope.with_only_columns(func.count(Schedule.id)).where(Schedule.status == 'active'))
    if active_count >= MAX_ACTIVE:
        raise HTTPException(422, f'활성 예약은 이 범위에서 최대 {MAX_ACTIVE}개입니다. 기존 예약을 취소한 뒤 다시 요청해 주세요.')
    if db.scalar(select(func.count(Schedule.id)).where(Schedule.user_id == user.id, Schedule.status == 'active')) >= MAX_ACTIVE * 2:
        raise HTTPException(422, '활성 예약 한도입니다. 기존 예약을 취소한 뒤 다시 요청해 주세요.')
    if len(question) > 2000:
        raise HTTPException(422, '예약에서 실행할 내용은 2,000자 이내로 작성해 주세요.')
    occurrence = intent.parse_occurrence(question, user.time_zone, now)
    total_count = db.scalar(select(func.count(Schedule.id)).where(Schedule.user_id == user.id))
    if total_count >= MAX_SCHEDULES:
        stale_ids = select(Schedule.id).where(Schedule.user_id == user.id, Schedule.status != 'active',
            ~exists().where(ScheduleRun.schedule_id == Schedule.id, ScheduleRun.status == 'running'),
        ).order_by(Schedule.created_at, Schedule.id).limit(total_count - MAX_SCHEDULES + 1)
        db.execute(delete(Schedule).where(Schedule.id.in_(stale_ids)))
    model_available(db, user, conversation.connection_id, conversation.model)
    row = Schedule(user_id=user.id, project_id=project_id,
                   creation_key=message_id, title=make_title(question), prompt=question,
                   connection_id=conversation.connection_id, model=conversation.model,
                   repeat=occurrence.repeat, time_zone=occurrence.time_zone,
                   next_run_at=occurrence.timestamp, local_minute=occurrence.local_minute, created_at=now)
    db.add(row)
    db.flush()
    return row


def cancel_locked(db, row):
    if row.status != 'active':
        return
    row.status = 'cancelled'
    row.next_run_at = None
    run = db.scalar(select(ScheduleRun).where(ScheduleRun.schedule_id == row.id, ScheduleRun.status == 'running'))
    if run:
        conversation = db.get(Conversation, run.conversation_id) if run.conversation_id else None
        if conversation and conversation.generation_id == run.request_id:
            conversation.cancel_requested = True
        else:
            finish_locked(db, row, run, 'cancelled', CANCELLED, int(time.time()))


def cancel(db, user_id, schedule_id):
    scheduler.lock(db)
    row = owned(db, user_id, schedule_id)
    cancel_locked(db, row)
    db.commit()
    return schedule_data(db, row)


def select_cancellation(db, user_id, question):
    # Match an explicit ID or quoted title from the current message. An unqualified
    # request is accepted only when there is exactly one active schedule.
    text = unquoted(question)
    ids = set(re.findall(r'\b[0-9a-fA-F]{8}-(?:[0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}\b', text))
    if len(ids) == 1:
        return owned(db, user_id, next(iter(ids)).lower())
    rows = db.scalars(select(Schedule).where(Schedule.user_id == user_id, Schedule.status == 'active')).all()
    exact = [row for row in rows if row.title.casefold() in text.casefold()]
    if len(exact) == 1 and not ids:
        return exact[0]
    bare = re.fullmatch(r'(?:내\s*)?(?:예약(?:된)?\s*(?:작업|일정)?|일정)\s*(?:을|를)?\s*(?:취소|중지|삭제)\s*'
                       r'(?:해\s*(?:줘|주세(?:요)?|줄래)|하세요)?[.!?]*|(?:please\s+)?(?:cancel|stop|delete)\s+'
                       r'(?:my\s+|the\s+)?(?:schedule|reminder|scheduled\s+task)[.!?]*', intent.clean(text), re.I)
    if len(rows) == 1 and bare:
        return rows[0]
    if not rows and not ids:
        raise HTTPException(404, '활성 예약이 없습니다.')
    raise HTTPException(422, '취소할 예약을 하나 지정해 주세요. 예약 목록에 표시된 ID 또는 제목을 함께 보내면 됩니다.')


def escape_markdown(text):
    return re.sub(r'([\\`*_{}\[\]<>()#+!|])', r'\\\1', text)


def time_label(timestamp, zone):
    return datetime.fromtimestamp(timestamp, ZoneInfo(zone)).strftime('%Y-%m-%d %H:%M') + f' ({zone})'


def command_result(action, data):
    if action == 'list':
        if not data:
            return '등록된 예약 작업이 없습니다.'
        lines = []
        for item in data[:12]:
            state = {'active': '실행 대기', 'complete': '종료', 'cancelled': '취소'}[item['status']]
            when = ' · ' + time_label(item['next_run_at'], item['time_zone']) if item['next_run_at'] else ''
            last = item['last_run']
            link = f' · [최근 결과](/c/{last["conversation_id"]})' if last and last['conversation_id'] else ''
            lines.append(f'- {escape_markdown(item["title"])} · {state}{when}{link} · `{item["id"]}`')
        return '예약 작업\n\n' + '\n'.join(lines) + ('\n\n나머지 예약은 + → 예약 작업에서 볼 수 있습니다.' if len(data) > 12 else '')
    title = escape_markdown(data['title'])
    if action == 'cancel':
        state = '예약을 취소했습니다.' if data['status'] == 'cancelled' else '이미 종료된 예약입니다.'
        return f'{state}\n\n{title} · `{data["id"]}`'
    if data['status'] != 'active':
        return f'이 메시지에서 만든 예약은 이미 종료되었습니다.\n\n{title} · `{data["id"]}`'
    repeat = {'once': '한 번', 'daily': '매일', 'weekly': '매주'}[data['repeat']]
    next_run = time_label(data['next_run_at'], data['time_zone']) if data['next_run_at'] else '실행 중'
    return (f'예약했습니다. 첫 실행은 **{next_run}**이고 반복 주기는 **{repeat}**입니다.\n\n'
            f'{title}\n\n결과는 새 대화에 남고, + → 예약 작업에서 확인하거나 중지할 수 있습니다.\n\n'
            f'예약 ID `{data["id"]}`')


def run_command(bind, user_id, turn):
    if getattr(turn, 'scheduled', False):
        return None
    message = turn.start['user_message']
    question = message['content']
    action = intent.action_for(question)
    try:
        with Session(bind, expire_on_commit=False) as db:
            scheduler.lock(db)
            user = active_user(db, user_id)
            conversation = db.get(Conversation, turn.conversation_id)
            if (not conversation or conversation.user_id != user_id or conversation.cancel_requested
                    or conversation.generation_id != turn.request_id):
                raise HTTPException(409, '현재 대화 요청에서 예약을 변경할 수 없습니다.')
            if action in {None, 'create'}:
                pending = pending_request(db, user_id, turn.conversation_id, question, before_message_id=turn.assistant_id)
                if pending:
                    question, action = pending, 'create'
                elif action is None:
                    return None
            if action in {'list', 'cancel'} and conversations.project_for(db, conversation.id):
                raise HTTPException(422, '예약 목록과 취소는 개인 대화 또는 + → 예약 작업에서 확인해 주세요.')
            if action == 'list':
                data = schedules_for(db, user_id)
            elif action == 'cancel':
                row = select_cancellation(db, user_id, question)
                cancel_locked(db, row)
                db.flush()
                data = schedule_data(db, row)
            else:
                now = int(time.time())
                try:
                    row = create(db, user, conversation, message['id'], question, now)
                except intent.MissingDetails as error:
                    save_clarification(db, user_id, turn, question, now)
                    db.commit()
                    return str(error.detail)
                data = schedule_data(db, row)
            db.commit()
            return command_result(action, data)
    except HTTPException as error:
        return str(error.detail)


def verify_scope(db, row, conversation=None):
    user = active_user(db, row.user_id)
    if row.project_id:
        project_access(db, user.id, row.project_id, write=True)
    if conversation:
        link = db.get(ProjectConversation, conversation.id)
        if conversation.user_id != user.id or (link.project_id if link else None) != row.project_id:
            raise HTTPException(409, '예약 결과 대화의 프로젝트 범위가 변경되었습니다.')
    model_available(db, user, row.connection_id, row.model)


def start_allowed(db, user_id, conversation_id, request_id):
    run = db.scalar(select(ScheduleRun).where(ScheduleRun.request_id == str(request_id)))
    if not run or run.status != 'running' or run.lease_until <= time.time() or run.conversation_id != str(conversation_id):
        return False
    row = db.get(Schedule, run.schedule_id)
    if not row or row.user_id != user_id or row.status != 'active':
        return False
    conversation = db.get(Conversation, str(conversation_id))
    if not conversation:
        return False
    verify_scope(db, row, conversation)
    if conversation.generation_id or db.scalar(select(Message.id).where(Message.conversation_id == conversation.id).limit(1)):
        raise HTTPException(409, '예약 결과 대화가 변경되었습니다. 새 예약으로 다시 요청해 주세요.')
    return True


def run_access_valid(db, user_id, conversation_id, request_id):
    # Check live ACLs during the run. Unlike start_allowed, an existing turn is required here.
    row = db.scalar(select(Schedule).join(ScheduleRun).where(ScheduleRun.request_id == str(request_id),
        ScheduleRun.conversation_id == str(conversation_id), ScheduleRun.status == 'running',
        Schedule.user_id == user_id, Schedule.status == 'active', ScheduleRun.lease_until > time.time()))
    if not row:
        return False
    conversation = db.get(Conversation, str(conversation_id))
    if not conversation or conversation.generation_id != str(request_id):
        return False
    try:
        verify_scope(db, row, conversation)
    except HTTPException:
        return False
    return True


def finish_locked(db, row, run, status, error, now):
    run.status, run.error, run.finished_at, run.lease_until = status, error, now, 0
    if row.status == 'active' and row.repeat == 'once':
        row.status, row.next_run_at = 'complete', None
    latest = select(ScheduleRun.id).where(ScheduleRun.schedule_id == row.id).order_by(
        ScheduleRun.scheduled_at.desc()).limit(MAX_RUN_HISTORY)
    db.flush()
    db.execute(delete(ScheduleRun).where(ScheduleRun.schedule_id == row.id, ScheduleRun.id.not_in(latest)))
