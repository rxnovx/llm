import asyncio
from dataclasses import dataclass
from contextlib import aclosing
import json
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.modules.auth.dependencies import Authenticated
from app.modules.chat.models import Admission, ContextNote, Conversation, GenerationRun, Message
from app.modules.chat import context as context_budget, providers, scheduler

router = APIRouter(prefix='/chat', tags=['chat'])
Database = Annotated[Session, Depends(get_db)]


class ConversationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    connection_id: str = Field('demo', min_length=1, max_length=64)
    model: str = Field('', max_length=200)


class TitleInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    title: str = Field(min_length=1, max_length=120)


class TurnInput(ConversationInput):
    content: str = Field(min_length=1, max_length=16000)
    request_id: UUID
    retry: bool = False


class CancelInput(BaseModel):
    request_id: UUID


class PreviewInput(ConversationInput):
    content: str = Field('', max_length=16000)


class NoteInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    summary: str = Field('', max_length=6000)
    through_position: int = Field(-1, ge=-1)
    revision: int = Field(0, ge=0)


def conversation_data(item: Conversation):
    return {key: getattr(item, key) for key in ('id', 'title', 'connection_id', 'model', 'created_at', 'updated_at')} | {
        'busy': bool(item.generation_id and item.generation_until > time.time())}


def message_data(item: Message):
    return {key: getattr(item, key) for key in ('id', 'role', 'content', 'status', 'model', 'is_test', 'error', 'created_at')}


def owned(db: Session, conversation_id: UUID, user_id: str) -> Conversation:
    item = db.scalar(select(Conversation).where(Conversation.id == str(conversation_id), Conversation.user_id == user_id))
    if item is None:
        raise HTTPException(404, '대화를 찾을 수 없습니다.')
    return item


def connection_for(connection_id: str, db=None, user=None):
    try:
        return providers.resolve(get_settings(), connection_id, db, user)
    except providers.ProviderError as error:
        raise HTTPException(503, str(error)) from None


@router.get('/config')
def config(context: Authenticated, db: Database):
    try:
        return {'connections': [item.public() for item in providers.connections(get_settings(), db, context.user)]}
    except providers.ProviderError as error:
        raise HTTPException(503, str(error)) from None


@router.post('/connections/{connection_id}/check')
async def check_connection(connection_id: str, context: Authenticated, db: Database):
    connection = connection_for(connection_id, db, context.user)
    try:
        models = await providers.list_models(connection)
        if connection.models:
            models = [model for model in models if model in {item.name for item in connection.models}]
        return {'models': models, 'is_test': connection.provider == 'test'}
    except providers.ProviderError as error:
        raise HTTPException(502, str(error)) from None


@router.get('/conversations')
def list_conversations(context: Authenticated, db: Database, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    items = db.scalars(select(Conversation).where(Conversation.user_id == context.user.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc()).offset(offset).limit(limit + 1)).all()
    return {'items': [conversation_data(item) for item in items[:limit]], 'has_more': len(items) > limit}


@router.post('/conversations', status_code=201)
def create_conversation(data: ConversationInput, context: Authenticated, db: Database):
    connection = connection_for(data.connection_id, db, context.user)
    if data.model or connection.model:
        try:
            connection.profile(data.model or connection.model)
        except providers.ProviderError as error:
            raise HTTPException(422, str(error)) from None
    item = Conversation(user_id=context.user.id, connection_id=connection.id, model=data.model or connection.model)
    db.add(item)
    db.commit()
    return conversation_data(item)


@router.get('/conversations/{conversation_id}')
def get_conversation(conversation_id: UUID, context: Authenticated, db: Database):
    item = owned(db, conversation_id, context.user.id)
    messages = db.scalars(select(Message).where(Message.conversation_id == item.id).order_by(Message.position)).all()
    admission = db.scalar(select(Admission).where(Admission.request_id == item.generation_id)) if item.generation_id else None
    position = db.scalar(select(func.count(Admission.id)).join(GenerationRun).where(
        Admission.state == 'queued', Admission.id <= admission.id, Admission.expires_at > int(time.time()),
        or_(GenerationRun.connection_id == item.connection_id, GenerationRun.user_id == context.user.id))) if admission and admission.state == 'queued' else 0
    return {'conversation': conversation_data(item), 'messages': [message_data(message) for message in messages],
        'generation': {'request_id': item.generation_id, 'status': admission.state if admission else 'running', 'position': position}
            if item.generation_id and item.generation_until > time.time() else None}


@router.post('/conversations/{conversation_id}/context/preview')
def preview_context(conversation_id: UUID, data: PreviewInput, context: Authenticated, db: Database):
    item = owned(db, conversation_id, context.user.id)
    connection = connection_for(data.connection_id, db, context.user)
    try:
        _, report = context_budget.plan(db, item.id, connection, data.model or connection.model or 'demo', data.content, strict=False)
        return report
    except providers.ProviderError as error:
        raise HTTPException(422, str(error)) from None


@router.patch('/conversations/{conversation_id}/context')
def save_context(conversation_id: UUID, data: NoteInput, context: Authenticated, db: Database):
    scheduler.lock(db)
    item = owned(db, conversation_id, context.user.id)
    if item.generation_id and item.generation_until > time.time():
        raise HTTPException(409, '응답이나 대기를 중지한 뒤 요약 메모를 변경하세요.')
    row = db.get(ContextNote, item.id)
    if data.revision != (row.revision if row else 0):
        raise HTTPException(409, '다른 탭에서 요약 메모를 변경했습니다. 다시 불러와 주세요.')
    summary = data.summary.strip()
    if summary and data.through_position >= 0:
        covered = db.scalar(select(Message).where(Message.conversation_id == item.id,
            Message.position == data.through_position, Message.role == 'assistant', Message.status == 'complete'))
        if covered is None:
            raise HTTPException(422, '요약 범위는 완료된 답변까지 지정해야 합니다.')
    if row is None:
        row = ContextNote(conversation_id=item.id, revision=0)
        db.add(row)
    row.summary, row.through_position = summary, data.through_position if summary else -1
    row.revision += 1
    db.commit()
    return {'summary': row.summary, 'through_position': row.through_position, 'revision': row.revision}


@router.patch('/conversations/{conversation_id}')
def rename_conversation(conversation_id: UUID, data: TitleInput, context: Authenticated, db: Database):
    item = owned(db, conversation_id, context.user.id)
    title = data.title.strip()
    if not title:
        raise HTTPException(422, '대화 제목을 입력해 주세요.')
    item.title = title
    db.commit()
    return conversation_data(item)


@router.delete('/conversations/{conversation_id}', status_code=204)
def delete_conversation(conversation_id: UUID, context: Authenticated, db: Database):
    item = owned(db, conversation_id, context.user.id)
    if item.generation_id and item.generation_until > time.time():
        raise HTTPException(409, '응답을 중지한 뒤 대화를 삭제해 주세요.')
    db.delete(item)
    db.commit()
    return Response(status_code=204)


@router.post('/conversations/{conversation_id}/cancel', status_code=204)
def cancel(conversation_id: UUID, data: CancelInput, context: Authenticated, db: Database):
    item = owned(db, conversation_id, context.user.id)
    db.execute(update(Conversation).where(Conversation.id == item.id, Conversation.generation_id == str(data.request_id))
               .values(cancel_requested=True))
    db.commit()
    return Response(status_code=204)


@dataclass
class Turn:
    conversation_id: str
    request_id: str
    assistant_id: str
    history: list[dict]
    start: dict
    previous_message: dict | None = None
    previous_connection: str | None = None
    started_clock: float = 0


def prepare(db: Session, conversation_id: UUID, user_id: str, data: TurnInput, connection: providers.Connection) -> Turn:
    started_clock = time.monotonic()
    scheduler.lock(db)
    item = owned(db, conversation_id, user_id)
    content = data.content.strip()
    if not content:
        raise HTTPException(422, '메시지를 입력해 주세요.')
    model = 'demo' if connection.provider == 'test' else (data.model.strip() or connection.model)
    if not model:
        raise HTTPException(422, '연결 설정에서 모델을 선택해 주세요.')
    now = int(time.time())
    if db.get(GenerationRun, str(data.request_id)):
        raise HTTPException(409, '이미 사용한 요청 ID입니다. 새 요청으로 다시 시도해 주세요.')
    previous_generation = item.generation_id
    claimed = db.execute(update(Conversation).where(Conversation.id == item.id,
        or_(Conversation.generation_id.is_(None), Conversation.generation_until <= now))
        .values(generation_id=str(data.request_id), generation_until=now + get_settings().llm_queue_wait_seconds + 15,
                cancel_requested=False))
    if claimed.rowcount != 1:
        raise HTTPException(409, '이 대화에서 응답을 생성하고 있습니다. 잠시 후 다시 시도해 주세요.')
    if previous_generation:
        scheduler.finish(db, previous_generation, 'error')
        db.execute(update(GenerationRun).where(GenerationRun.id == previous_generation,
            GenerationRun.status.in_(['running', 'queued'])).values(status='error', error_code='interrupted', finished_at=now,
                duration_ms=(now - GenerationRun.started_at) * 1000))
    db.execute(update(GenerationRun).where(GenerationRun.conversation_id == item.id,
        GenerationRun.status.in_(['running', 'queued']), GenerationRun.deadline_at <= now).values(
            status='error', error_code='interrupted', finished_at=now,
            duration_ms=(now - GenerationRun.started_at) * 1000))
    rows = list(db.scalars(select(Message).where(Message.conversation_id == item.id).order_by(Message.position)))
    for message in rows:
        if message.status == 'pending':
            message.status, message.error = 'error', '이전 응답이 중단되었습니다.'
    previous_message, previous_connection = None, None
    if data.retry:
        if len(rows) < 2 or rows[-2].role != 'user' or rows[-1].role != 'assistant' or rows[-2].content != content:
            raise HTTPException(409, '마지막 메시지만 다시 생성할 수 있습니다.')
        user_message, assistant = rows[-2:]
        note = db.get(ContextNote, item.id)
        if note and note.summary and note.through_position >= assistant.position:
            raise HTTPException(409, '이 답변이 요약 메모에 포함되어 있습니다. 요약 범위를 조정한 뒤 다시 생성하세요.')
        if assistant.status == 'complete':
            previous_message, previous_connection = message_data(assistant), item.connection_id
        else:
            assistant.content = ''
        assistant.error, assistant.status = None, 'pending'
        assistant.model, assistant.is_test = model, connection.provider == 'test'
    else:
        if len(rows) >= 400:
            raise HTTPException(422, '이 대화가 길어졌습니다. 새 대화를 시작해 주세요.')
        position = rows[-1].position + 1 if rows else 0
        user_message = Message(conversation_id=item.id, position=position, role='user', content=content, model=model)
        assistant = Message(conversation_id=item.id, position=position + 1, role='assistant', content='',
            status='pending', model=model, is_test=connection.provider == 'test')
        db.add_all([user_message, assistant])
    try:
        history, report = context_budget.plan(db, item.id, connection, model, content)
    except providers.ProviderError as error:
        raise HTTPException(422, str(error)) from None
    item.connection_id, item.model, item.updated_at = connection.id, model, now
    if not rows:
        db.execute(update(Conversation).where(Conversation.id == item.id, Conversation.title == '새 대화')
            .values(title=content.splitlines()[0][:60]))
        db.refresh(item, attribute_names=['title'])
    run = GenerationRun(id=str(data.request_id), user_id=user_id, conversation_id=item.id,
        connection_id=connection.id, provider=connection.provider, model=model,
        is_test=connection.provider == 'test', status='queued', started_at=now,
        deadline_at=now + get_settings().llm_queue_wait_seconds + 15)
    db.add(run)
    try:
        db.flush()
        scheduler.enqueue(db, run, get_settings())
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, '동시에 처리된 요청입니다. 새 요청으로 다시 시도해 주세요.') from None
    return Turn(item.id, str(data.request_id), assistant.id, history,
        {'conversation': conversation_data(item), 'user_message': message_data(user_message),
         'assistant_message': message_data(assistant) | {'content': ''}, 'context': report}, previous_message, previous_connection, started_clock)


def event(name: str, data: dict) -> str:
    return f'event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


class TurnCancelled(Exception):
    pass


async def guarded_parts(iterator, cancelled):
    """Observe stop requests even while the upstream has not sent its first byte."""
    pending = None
    try:
        while True:
            pending = asyncio.create_task(anext(iterator))
            while not pending.done():
                await asyncio.wait({pending}, timeout=0.25)
                if cancelled():
                    raise TurnCancelled()
            try:
                yield pending.result()
            except StopAsyncIteration:
                return
    finally:
        if pending:
            if not pending.done():
                pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        close = getattr(iterator, 'aclose', None)
        if close:
            await close()


@router.post('/conversations/{conversation_id}/messages')
def send(conversation_id: UUID, data: TurnInput, context: Authenticated, db: Database):
    connection = connection_for(data.connection_id, db, context.user)
    turn = prepare(db, conversation_id, context.user.id, data, connection)
    settings, bind = get_settings(), db.get_bind()

    async def generate():
        text, status, error = '', 'cancelled', None
        first_token_ms, error_code = None, 'cancelled'
        saved = None
        def cancelled():
            with Session(bind) as session:
                active = session.get(Conversation, turn.conversation_id)
                return not active or active.generation_id != turn.request_id or active.cancel_requested
        try:
            yield event('start', turn.start)
            last_queue_notice = 0.0
            while True:
                with Session(bind, expire_on_commit=False) as session:
                    state, position, admitted = scheduler.poll(session, turn.request_id, settings, providers.resolve)
                    if state == 'running':
                        turn.history, report = context_budget.plan(session, turn.conversation_id, admitted,
                            turn.start['conversation']['model'], data.content.strip())
                    session.commit()
                if state != 'queued':
                    break
                if time.monotonic() - last_queue_notice >= 1:
                    yield event('queue', {'status': 'queued', 'position': position, 'request_id': turn.request_id})
                    last_queue_notice = time.monotonic()
                await asyncio.sleep(0.25)
            if state == 'expired':
                raise TimeoutError('queue')
            if state == 'running':
                yield event('queue', {'status': 'running', 'position': 0, 'request_id': turn.request_id})
                yield event('context', report)
                async with asyncio.timeout(settings.llm_timeout_seconds):
                    iterator = providers.stream_reply(admitted, turn.start['conversation']['model'], turn.history, settings)
                    async with aclosing(guarded_parts(iterator, cancelled)) as parts:
                        async for part in parts:
                            if len(text) + len(part) > 200000:
                                raise providers.ProviderError('응답 길이 제한에 도달했습니다. 질문을 나눠서 보내 주세요.')
                            text += part
                            if first_token_ms is None:
                                first_token_ms = max(0, int((time.monotonic() - turn.started_clock) * 1000))
                            yield event('delta', {'content': part})
                    if not text.strip():
                        raise providers.ProviderError('모델이 빈 응답을 보냈습니다. 다시 시도해 주세요.')
                    status, error_code = 'complete', None
        except TurnCancelled:
            pass
        except asyncio.CancelledError:
            raise
        except (providers.ProviderError, TimeoutError) as caught:
            status = 'error'
            error_code = 'provider_error' if isinstance(caught, providers.ProviderError) else 'queue_timeout' if str(caught) == 'queue' else 'timeout'
            error = str(caught) if isinstance(caught, providers.ProviderError) else '대기 시간이 초과되었습니다. 잠시 후 다시 시도해 주세요.' if str(caught) == 'queue' else '응답 시간이 초과되었습니다. 다시 시도해 주세요.'
        except HTTPException as caught:
            status, error, error_code = 'error', str(caught.detail), 'context_limit'
        except Exception:
            status, error = 'error', '응답을 처리하지 못했습니다. 다시 시도해 주세요.'
            error_code = 'internal_error'
        finally:
            with Session(bind, expire_on_commit=False) as session:
                scheduler.lock(session)
                run = session.get(GenerationRun, turn.request_id)
                item = session.scalar(select(Conversation).where(Conversation.id == turn.conversation_id,
                    Conversation.generation_id == turn.request_id))
                message = session.get(Message, turn.assistant_id)
                if item and item.cancel_requested and status == 'complete':
                    status, error_code = 'cancelled', 'cancelled'
                if run and run.status in {'running', 'queued'}:
                    run.status, run.error_code = status, error_code
                    run.finished_at = int(time.time())
                    run.duration_ms = max(0, int((time.monotonic() - turn.started_clock) * 1000))
                    run.first_token_ms, run.output_chars = first_token_ms, len(text)
                scheduler.finish(session, turn.request_id, status)
                if item and message:
                    message.content, message.status, message.error = text, status, error
                    # A failed/cancelled regeneration must not destroy a good answer.
                    if status != 'complete' and turn.previous_message:
                        previous = turn.previous_message
                        message.content, message.status, message.error = previous['content'], previous['status'], previous['error']
                        message.model, message.is_test = previous['model'], previous['is_test']
                        item.connection_id, item.model = turn.previous_connection, previous['model']
                    item.generation_id, item.generation_until, item.cancel_requested = None, 0, False
                    item.updated_at = int(time.time())
                    session.commit()
                    saved = {'conversation': conversation_data(item), 'message': message_data(message)}
                else:
                    session.commit()
        if saved:
            yield event('done' if status == 'complete' else status, saved | {'detail': error})

    return StreamingResponse(generate(), media_type='text/event-stream',
        headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
