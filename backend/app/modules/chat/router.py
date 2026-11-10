import asyncio
from dataclasses import dataclass
import json
import time
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.modules.auth.dependencies import Authenticated
from app.modules.chat.models import Conversation, Message
from app.modules.chat import providers

router = APIRouter(prefix='/chat', tags=['chat'])
Database = Annotated[Session, Depends(get_db)]


class ConversationInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    connection_id: str = Field('demo', min_length=1, max_length=64)
    model: str = Field('', max_length=200)


class TitleInput(BaseModel):
    title: str = Field(min_length=1, max_length=120)


class TurnInput(ConversationInput):
    content: str = Field(min_length=1, max_length=16000)
    request_id: UUID
    retry: bool = False


class CancelInput(BaseModel):
    request_id: UUID


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


def connection_for(connection_id: str):
    try:
        return providers.resolve(get_settings(), connection_id)
    except providers.ProviderError as error:
        raise HTTPException(503, str(error)) from None


@router.get('/config')
def config(context: Authenticated):
    try:
        return {'connections': [item.public() for item in providers.connections(get_settings())]}
    except providers.ProviderError as error:
        raise HTTPException(503, str(error)) from None


@router.post('/connections/{connection_id}/check')
async def check_connection(connection_id: str, context: Authenticated):
    connection = connection_for(connection_id)
    try:
        return {'models': await providers.list_models(connection), 'is_test': connection.provider == 'test'}
    except providers.ProviderError as error:
        raise HTTPException(502, str(error)) from None


@router.get('/conversations')
def list_conversations(context: Authenticated, db: Database, limit: int = Query(50, ge=1, le=100), offset: int = Query(0, ge=0)):
    items = db.scalars(select(Conversation).where(Conversation.user_id == context.user.id)
        .order_by(Conversation.updated_at.desc(), Conversation.id.desc()).offset(offset).limit(limit + 1)).all()
    return {'items': [conversation_data(item) for item in items[:limit]], 'has_more': len(items) > limit}


@router.post('/conversations', status_code=201)
def create_conversation(data: ConversationInput, context: Authenticated, db: Database):
    connection = connection_for(data.connection_id)
    item = Conversation(user_id=context.user.id, connection_id=connection.id, model=data.model or connection.model)
    db.add(item)
    db.commit()
    return conversation_data(item)


@router.get('/conversations/{conversation_id}')
def get_conversation(conversation_id: UUID, context: Authenticated, db: Database):
    item = owned(db, conversation_id, context.user.id)
    messages = db.scalars(select(Message).where(Message.conversation_id == item.id).order_by(Message.position)).all()
    return {'conversation': conversation_data(item), 'messages': [message_data(message) for message in messages]}


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


def prepare(db: Session, conversation_id: UUID, user_id: str, data: TurnInput, connection: providers.Connection) -> Turn:
    item = owned(db, conversation_id, user_id)
    content = data.content.strip()
    if not content:
        raise HTTPException(422, '메시지를 입력해 주세요.')
    model = 'demo' if connection.provider == 'test' else (data.model.strip() or connection.model)
    if not model:
        raise HTTPException(422, '연결 설정에서 모델을 선택해 주세요.')
    now = int(time.time())
    claimed = db.execute(update(Conversation).where(Conversation.id == item.id,
        or_(Conversation.generation_id.is_(None), Conversation.generation_until <= now))
        .values(generation_id=str(data.request_id), generation_until=now + get_settings().llm_timeout_seconds + 30,
                cancel_requested=False))
    if claimed.rowcount != 1:
        raise HTTPException(409, '이 대화에서 응답을 생성하고 있습니다. 잠시 후 다시 시도해 주세요.')
    rows = list(db.scalars(select(Message).where(Message.conversation_id == item.id).order_by(Message.position)))
    for message in rows:
        if message.status == 'pending':
            message.status, message.error = 'error', '이전 응답이 중단되었습니다.'
    previous_message, previous_connection = None, None
    if data.retry:
        if len(rows) < 2 or rows[-2].role != 'user' or rows[-1].role != 'assistant' or rows[-2].content != content:
            raise HTTPException(409, '마지막 메시지만 다시 생성할 수 있습니다.')
        user_message, assistant = rows[-2:]
        if assistant.status == 'complete':
            previous_message, previous_connection = message_data(assistant), item.connection_id
        else:
            assistant.content = ''
        assistant.error, assistant.status = None, 'pending'
        assistant.model, assistant.is_test = model, connection.provider == 'test'
        context_rows = rows[:-1]
    else:
        if len(rows) >= 400:
            raise HTTPException(422, '이 대화가 길어졌습니다. 새 대화를 시작해 주세요.')
        position = rows[-1].position + 1 if rows else 0
        user_message = Message(conversation_id=item.id, position=position, role='user', content=content, model=model)
        assistant = Message(conversation_id=item.id, position=position + 1, role='assistant', content='',
            status='pending', model=model, is_test=connection.provider == 'test')
        db.add_all([user_message, assistant])
        context_rows = rows + [user_message]
    history, pending_user = [], None
    for message in context_rows:
        if message.role == 'user':
            pending_user = {'role': 'user', 'content': message.content}
        elif message.status == 'complete' and pending_user:
            history.extend([pending_user, {'role': 'assistant', 'content': message.content}])
            pending_user = None
    if pending_user:
        history.append(pending_user)
    history = history[-31:]
    while len(history) > 1 and sum(len(message['content']) for message in history) > 64000:
        history = history[2:]
    item.connection_id, item.model, item.updated_at = connection.id, model, now
    if not rows:
        item.title = content.splitlines()[0][:60]
    db.commit()
    return Turn(item.id, str(data.request_id), assistant.id, history,
        {'conversation': conversation_data(item), 'user_message': message_data(user_message),
         'assistant_message': message_data(assistant) | {'content': ''}}, previous_message, previous_connection)


def event(name: str, data: dict) -> str:
    return f'event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n'


@router.post('/conversations/{conversation_id}/messages')
def send(conversation_id: UUID, data: TurnInput, context: Authenticated, db: Database):
    connection = connection_for(data.connection_id)
    turn = prepare(db, conversation_id, context.user.id, data, connection)
    settings, bind = get_settings(), db.get_bind()

    async def generate():
        text, status, error = '', 'cancelled', None
        saved = None
        last_check = 0.0
        try:
            yield event('start', turn.start)
            async with asyncio.timeout(settings.llm_timeout_seconds):
                async for part in providers.stream_reply(connection, turn.start['conversation']['model'], turn.history, settings):
                    if time.monotonic() - last_check > 0.25:
                        with Session(bind) as session:
                            active = session.scalar(select(Conversation).where(Conversation.id == turn.conversation_id))
                            if not active or active.generation_id != turn.request_id or active.cancel_requested:
                                break
                        last_check = time.monotonic()
                    if len(text) + len(part) > 200000:
                        raise providers.ProviderError('응답 길이 제한에 도달했습니다. 질문을 나눠서 보내 주세요.')
                    text += part
                    yield event('delta', {'content': part})
                else:
                    if not text.strip():
                        raise providers.ProviderError('모델이 빈 응답을 보냈습니다. 다시 시도해 주세요.')
                    status = 'complete'
        except asyncio.CancelledError:
            raise
        except (providers.ProviderError, TimeoutError) as caught:
            status = 'error'
            error = str(caught) if isinstance(caught, providers.ProviderError) else '응답 시간이 초과되었습니다. 다시 시도해 주세요.'
        except Exception:
            status, error = 'error', '응답을 처리하지 못했습니다. 다시 시도해 주세요.'
        finally:
            with Session(bind, expire_on_commit=False) as session:
                item = session.scalar(select(Conversation).where(Conversation.id == turn.conversation_id,
                    Conversation.generation_id == turn.request_id))
                message = session.get(Message, turn.assistant_id)
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
        if saved:
            yield event('done' if status == 'complete' else status, saved | {'detail': error})

    return StreamingResponse(generate(), media_type='text/event-stream',
        headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
