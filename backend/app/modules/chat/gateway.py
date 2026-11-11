"""Authenticated OpenAI-compatible text inference over configured local models."""

import asyncio
from contextlib import aclosing
from hashlib import sha256
import json
import time
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.modules.auth.models import User
from . import providers, usage
from .context import estimate
from .independent import admitted

router = APIRouter(prefix='/v1', tags=['model gateway'])
Database = Annotated[Session, Depends(get_db)]


class TextPart(BaseModel):
    model_config = ConfigDict(extra='forbid')
    type: Literal['text']
    text: str = Field(max_length=16000)


class MessageInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    role: Literal['system', 'developer', 'user', 'assistant']
    content: str | Annotated[list[TextPart], Field(max_length=8)]
    name: str | None = Field(None, max_length=64)


class StreamOptions(BaseModel):
    model_config = ConfigDict(extra='forbid')
    include_usage: bool = False


class CompletionInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    model: str = Field(min_length=1, max_length=270)
    messages: list[MessageInput] = Field(min_length=1, max_length=128)
    stream: bool = False
    stream_options: StreamOptions | None = None
    max_tokens: int | None = Field(None, ge=1, le=16384)
    max_completion_tokens: int | None = Field(None, ge=1, le=16384)
    temperature: float | None = Field(None, ge=0, le=2)
    top_p: float | None = Field(None, gt=0, le=1)
    stop: Annotated[str, Field(max_length=200)] | Annotated[list[Annotated[str, Field(max_length=200)]], Field(max_length=4)] | None = None
    n: Literal[1] = 1
    user: str | None = Field(None, max_length=128)


def error_data(status, message):
    kind = {401: 'authentication_error', 429: 'rate_limit_error', 502: 'server_error',
            504: 'server_error'}.get(status, 'invalid_request_error')
    return {'error': {'message': message, 'type': kind, 'code': str(status)}}


def error_response(error):
    if isinstance(error, HTTPException):
        status, message = error.status_code, str(error.detail)
    elif isinstance(error, TimeoutError):
        status, message = 504, '모델 응답 시간이 초과되었습니다.'
    else:
        status, message = 502, str(error)
    return JSONResponse(error_data(status, message), status_code=status)


def key_user(db, key):
    user = db.get(User, key.user_id) if key and not key.revoked else None
    if not user or not user.is_active or user.registration_status != 'approved':
        raise HTTPException(401, '유효한 서비스 API 키를 입력하세요.')
    return user


def authenticate(request, db):
    authorization = request.headers.get('authorization', '')
    scheme, _, value = authorization.partition(' ')
    if scheme.lower() != 'bearer' or not value.startswith('llm_sk_') or len(value) > 100:
        raise HTTPException(401, '유효한 서비스 API 키를 입력하세요.')
    digest = sha256(value.encode()).hexdigest()
    key = db.scalar(select(usage.ServiceKey).where(usage.ServiceKey.digest == digest))
    return key, key_user(db, key)


def model_map(db, user):
    available = {}
    for connection in providers.connections(get_settings(), db, user):
        if connection.provider == 'test':
            continue
        names = [profile.name for profile in connection.models]
        if not names and connection.model:
            names = [connection.model]
        for name in names:
            available[f'{connection.id}:{name}'] = connection, name
    return available


@router.get('/models')
def models(request: Request, db: Database):
    try:
        _, user = authenticate(request, db)
        models = model_map(db, user)
        return {'object': 'list', 'data': [
            {'id': name, 'object': 'model', 'created': 0, 'owned_by': 'local'}
            for name in models
        ]}
    except (HTTPException, providers.ProviderError) as error:
        return error_response(error)


def normalized_messages(data):
    messages = []
    for item in data.messages:
        content = item.content if isinstance(item.content, str) else '\n'.join(part.text for part in item.content)
        if len(content) > 16000:
            raise HTTPException(400, '메시지는 16,000자까지 입력할 수 있습니다.')
        messages.append({'role': 'system' if item.role == 'developer' else item.role, 'content': content})
    return messages


def event(value):
    return 'data: ' + json.dumps(value, ensure_ascii=False) + '\n\n'


def completion_usage(bind, identifier):
    with Session(bind) as db:
        counts = usage.totals(db, request_id=identifier)
    input_tokens, output_tokens = counts['reported_input'], counts['reported_output']
    if not counts['reported_calls']:
        return None
    return {'prompt_tokens': input_tokens, 'completion_tokens': output_tokens,
            'total_tokens': input_tokens + output_tokens}


@router.post('/chat/completions')
async def completions(request: Request, data: CompletionInput, db: Database):
    try:
        key, user = authenticate(request, db)
        mapping = model_map(db, user)
        if data.model not in mapping:
            raise HTTPException(404, '허용된 모델 ID를 /v1/models에서 확인하세요.')
        connection, model = mapping[data.model]
        profile = connection.profile(model)
        output_limit = data.max_completion_tokens or data.max_tokens or profile.max_output_tokens
        messages = normalized_messages(data)
        if output_limit > profile.max_output_tokens or estimate(messages) + output_limit + 256 > profile.context_window:
            raise HTTPException(400, '메시지 또는 최대 출력이 모델 한도를 초과합니다.')
        bind, user_id, key_id = db.get_bind(), user.id, key.id
        db.rollback()
    except (HTTPException, providers.ProviderError) as error:
        return error_response(error)

    settings = get_settings()
    identifier = 'chatcmpl-' + uuid4().hex
    meta = {'id': identifier, 'created': int(time.time()), 'model': data.model}
    response_metadata = {}
    usage_result = None

    def guard(session):
        key_user(session, session.get(usage.ServiceKey, key_id))

    async def reply():
        nonlocal usage_result
        async with admitted(bind, user_id, connection, model, settings, guard=guard) as (selected, request_id):
            selected_profile = selected.profile(model)
            if output_limit > selected_profile.max_output_tokens or estimate(messages) + output_limit + 256 > selected_profile.context_window:
                raise HTTPException(400, '대기 중 변경된 모델 한도를 초과합니다.')
            token = usage.scope.set(usage.Scope(bind, user_id, request_id, key_id))
            try:
                size, last_check = 0, 0.0
                source = providers.stream_reply(selected, model, messages, settings, max_tokens=output_limit,
                    temperature=data.temperature, top_p=data.top_p, stop=data.stop, metadata=response_metadata)
                async with aclosing(source):
                    async for part in source:
                        if time.monotonic() - last_check > 1:
                            with Session(bind) as session:
                                guard(session)
                            last_check = time.monotonic()
                        size += len(part)
                        if size > 200000:
                            raise HTTPException(502, '모델 응답이 길이 한도를 초과했습니다.')
                        yield part
                with Session(bind) as session:
                    guard(session)
                if not size:
                    raise providers.ProviderError('모델이 텍스트 응답을 보내지 않았습니다.')
                usage_result = completion_usage(bind, request_id)
            finally:
                usage.scope.reset(token)

    if not data.stream:
        try:
            chunks = [part async for part in reply()]
            return meta | {'object': 'chat.completion', 'choices': [{
                'index': 0, 'message': {'role': 'assistant', 'content': ''.join(chunks)},
                'finish_reason': response_metadata.get('finish_reason', 'stop'),
            }], 'usage': usage_result}
        except (HTTPException, providers.ProviderError, TimeoutError) as error:
            return error_response(error)

    async def stream():
        def chunk(delta, finish=None):
            return event(meta | {'object': 'chat.completion.chunk',
                'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]})

        try:
            async with aclosing(reply()) as source:
                first = True
                async for part in source:
                    if first:
                        yield chunk({'role': 'assistant'})
                        first = False
                    yield chunk({'content': part})
            yield chunk({}, response_metadata.get('finish_reason', 'stop'))
            if data.stream_options and data.stream_options.include_usage:
                yield event(meta | {'object': 'chat.completion.chunk', 'choices': [], 'usage': usage_result})
        except (HTTPException, providers.ProviderError, TimeoutError) as error:
            status = error.status_code if isinstance(error, HTTPException) else 504 if isinstance(error, TimeoutError) else 502
            message = str(error.detail) if isinstance(error, HTTPException) else str(error) or '모델 응답 시간이 초과되었습니다.'
            yield event(error_data(status, message))
        yield 'data: [DONE]\n\n'

    return StreamingResponse(stream(), media_type='text/event-stream',
                             headers={'Cache-Control': 'no-store', 'X-Accel-Buffering': 'no'})
