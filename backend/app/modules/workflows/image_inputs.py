"""Resolve images the user can actually see in a conversation."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.modules.auth.models import User
from app.modules.chat.models import Conversation, Message
from app.modules.knowledge import service as knowledge
from . import images
from .artifacts import MAX_RESULT_BYTES
from .models import Artifact, ArtifactScope, MessageState

RASTER_SUFFIXES = frozenset({'.png', '.jpg', '.jpeg', '.webp'})
MAX_INPUT_BYTES = MAX_RESULT_BYTES
MAX_VISION_IMAGES = 4


def _visible_artifact_id(db, user_id, conversation_id, before_message_id, artifact_id=None):
    scoped = select(ArtifactScope.artifact_id).where(
        ArtifactScope.artifact_id == Artifact.id, ArtifactScope.message_id == Message.id,
    ).exists()
    candidates = (
        select(Artifact.id, MessageState.artifact_ids_json)
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
        cutoff = select(Message.position).where(
            Message.id == str(before_message_id), Message.conversation_id == str(conversation_id),
        ).correlate(None).scalar_subquery()
        candidates = candidates.where(Message.position < cutoff)
    if artifact_id is not None:
        candidates = candidates.where(Artifact.id == str(artifact_id))
    for candidate_id, active_ids in db.execute(candidates.order_by(
        Message.position.desc(), Artifact.created_at.desc(), Artifact.id.desc(),
    )):
        if candidate_id in json.loads(active_ids or '[]'):
            return candidate_id
    return None


def latest_artifact_id(db, user_id, conversation_id, before_message_id=None):
    return _visible_artifact_id(db, user_id, conversation_id, before_message_id)


def _load(bind, user_id, conversation_id, options, mode, before_message_id):
    artifact_id = options.get('image_artifact_id')
    document_ids = list(dict.fromkeys(options.get('document_ids', [])))
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
            selected_id = _visible_artifact_id(db, user_id, conversation_id, before_message_id, artifact_id)
            if selected_id is None:
                if artifact_id:
                    raise HTTPException(404, '이 대화에서 사용할 수 있는 생성 이미지를 찾을 수 없습니다.')
                raise HTTPException(422, '편집할 이미지를 선택하거나 첨부하세요.')
            source = db.execute(select(Artifact.data, Artifact.format).where(
                Artifact.id == selected_id, Artifact.owner_id == user_id, Artifact.status == 'ready',
            )).one_or_none()
            if source is None:
                raise HTTPException(404, '이 대화에서 사용할 수 있는 생성 이미지를 찾을 수 없습니다.')
            sources = [(source.data, source.format)]
        else:
            if not document_ids:
                raise HTTPException(422, '분석할 이미지를 첨부하세요.')
            sources = []
            total_bytes = 0
            for document_id in document_ids:
                document = knowledge.document_access(db, user_id, document_id, conversation_id=conversation_id)
                if document.base_id:
                    raise HTTPException(404, '이 대화의 첨부 이미지를 찾을 수 없습니다.')
                if document.status != 'image' and Path(document.filename).suffix.lower() not in RASTER_SUFFIXES:
                    raise HTTPException(422, 'PNG, JPEG, WebP 이미지를 선택하세요.')
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
