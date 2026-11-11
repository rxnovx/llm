"""Resolve images the user can actually see in a conversation."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.assistant.models import TurnContext
from app.modules.auth.models import User
from app.modules.chat.models import Conversation, Message
from app.modules.knowledge import service as knowledge
from . import images
from .artifacts import MAX_RESULT_BYTES
from .models import Artifact, ArtifactScope, MessageState

RASTER_SUFFIXES = frozenset({'.png', '.jpg', '.jpeg', '.webp'})
MAX_INPUT_BYTES = MAX_RESULT_BYTES
MAX_VISION_IMAGES = 4


def _before_position(conversation_id, message_id):
    return select(Message.position).where(
        Message.id == str(message_id), Message.conversation_id == str(conversation_id),
    ).correlate(None).scalar_subquery()


def _visible_artifact(db, user_id, conversation_id, before_message_id, artifact_id=None):
    scoped = select(ArtifactScope.artifact_id).where(
        ArtifactScope.artifact_id == Artifact.id, ArtifactScope.message_id == Message.id,
    ).exists()
    candidates = (
        select(Artifact.id, Message.position, MessageState.artifact_ids_json)
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .join(MessageState, MessageState.message_id == Message.id)
        .join(Artifact, or_(Artifact.message_id == Message.id, scoped))
        .where(
            Conversation.id == str(conversation_id), Conversation.user_id == user_id,
            Message.role == 'assistant', Message.status == 'complete',
            Artifact.owner_id == user_id, Artifact.status == 'ready',
            Artifact.format.in_(images.FORMATS.values()),
        )
    )
    if before_message_id is not None:
        candidates = candidates.where(Message.position < _before_position(conversation_id, before_message_id))
    if artifact_id is not None:
        candidates = candidates.where(Artifact.id == str(artifact_id))
    for candidate_id, position, active_ids in db.execute(candidates.order_by(
        Message.position.desc(), Artifact.created_at.desc(), Artifact.id.desc(),
    )):
        if candidate_id in json.loads(active_ids or '[]'):
            return candidate_id, position
    return None


def latest_artifact_id(db, user_id, conversation_id, before_message_id=None):
    artifact = _visible_artifact(db, user_id, conversation_id, before_message_id)
    return artifact[0] if artifact else None


def attachment_ids(options):
    ids = options.get('image_document_ids')
    if not ids and options.get('image_mode') in {'vision', 'edit'}:
        ids = options.get('document_ids')
    return list(dict.fromkeys(ids or []))


def reference(options):
    ids = attachment_ids(options)
    if options.get('image_artifact_id'):
        return {'image_artifact_id': options['image_artifact_id']}
    return {'image_document_ids': ids} if ids else {}


def _documents(db, user_id, conversation_id, ids):
    for document_id in ids:
        document = knowledge.document_access(db, user_id, document_id, conversation_id=conversation_id)
        if document.base_id:
            raise HTTPException(404, '이 대화의 첨부 이미지를 찾을 수 없습니다.')
        if document.status != 'image' and Path(document.filename).suffix.lower() not in RASTER_SUFFIXES:
            raise HTTPException(422, 'PNG, JPEG, WebP 이미지를 선택하세요.')
        yield document


def validate_reference(db, user_id, conversation_id, options, before_message_id=None):
    artifact_id = options.get('image_artifact_id')
    if artifact_id and not _visible_artifact(db, user_id, conversation_id, before_message_id, artifact_id):
        raise HTTPException(404, '이 대화에서 사용할 수 있는 생성 이미지를 찾을 수 없습니다.')
    for _ in _documents(db, user_id, conversation_id, attachment_ids(options)):
        pass


def latest_reference(db, user_id, conversation_id, before_message_id=None):
    artifact = _visible_artifact(db, user_id, conversation_id, before_message_id)
    candidates = select(TurnContext.data_json).join(Message).join(Conversation).where(
        Message.conversation_id == str(conversation_id), Conversation.user_id == user_id,
        Message.role == 'assistant', Message.status == 'complete',
    )
    if before_message_id is not None:
        candidates = candidates.where(Message.position < _before_position(conversation_id, before_message_id))
    if artifact:
        candidates = candidates.where(Message.position > artifact[1])
    # Read recent turn references first and check access without loading image bytes.
    for data in db.scalars(candidates.order_by(Message.position.desc()).limit(20)):
        ids = attachment_ids(json.loads(data))
        if not ids:
            continue
        source = {'image_document_ids': ids}
        try:
            validate_reference(db, user_id, conversation_id, source)
        except HTTPException:
            continue
        return source
    return {'image_artifact_id': artifact[0]} if artifact else {}


def _load(bind, user_id, conversation_id, options, mode, before_message_id):
    artifact_id = options.get('image_artifact_id')
    document_ids = attachment_ids(options)
    if artifact_id and document_ids:
        raise HTTPException(422, '생성 이미지와 첨부 이미지 중 하나를 선택하세요.')
    if mode == 'edit' and len(document_ids) > 1:
        raise HTTPException(422, '편집할 이미지 한 장을 선택하세요.')
    if len(document_ids) > MAX_VISION_IMAGES:
        raise HTTPException(422, '한 번에 이미지를 네 장까지 분석할 수 있습니다.')

    with Session(bind) as db:
        user = db.get(User, user_id)
        if not user or not user.is_active or user.registration_status != 'approved':
            raise HTTPException(403, '이미지 사용 권한을 확인하세요.')
        conversation = db.get(Conversation, str(conversation_id))
        if not conversation or conversation.user_id != user_id:
            raise HTTPException(404, '대화를 찾을 수 없습니다.')
        if artifact_id or mode == 'edit' and not document_ids:
            artifact = _visible_artifact(db, user_id, conversation_id, before_message_id, artifact_id)
            if artifact is None:
                if artifact_id:
                    raise HTTPException(404, '이 대화에서 사용할 수 있는 생성 이미지를 찾을 수 없습니다.')
                raise HTTPException(422, '편집할 이미지를 선택하거나 첨부하세요.')
            source = db.execute(select(Artifact.data, Artifact.format).where(
                Artifact.id == artifact[0], Artifact.owner_id == user_id, Artifact.status == 'ready',
            )).one_or_none()
            if source is None:
                raise HTTPException(404, '이 대화에서 사용할 수 있는 생성 이미지를 찾을 수 없습니다.')
            sources = [(source.data, source.format)]
        else:
            if not document_ids:
                raise HTTPException(422, '분석할 이미지를 첨부하세요.')
            sources = []
            total_bytes = 0
            for document in _documents(db, user_id, conversation_id, document_ids):
                raw = document.raw
                total_bytes += len(raw)
                if total_bytes > MAX_INPUT_BYTES:
                    raise HTTPException(413, '참조 이미지의 전체 크기는 10MiB 이하여야 합니다.')
                sources.append((raw, None))

    result = []
    for raw, stored_format in sources:
        raster = images.validate_raster(raw)
        if stored_format and raster.format != stored_format:
            raise HTTPException(422, '저장된 이미지 형식을 확인하세요.')
        result.append(raster)
    return result


async def load(bind, user_id, conversation_id, options, *, mode: Literal['vision', 'edit'],
               before_message_id=None) -> list[images.ImageData]:
    return await asyncio.to_thread(_load, bind, user_id, conversation_id, options, mode, before_message_id)
