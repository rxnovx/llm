"""Read a bounded window from the conversation's stable message positions."""
from typing import Annotated

from fastapi import HTTPException, Query
from sqlalchemy import select

from .models import Message

DEFAULT_LIMIT = 40
MessageLimit = Annotated[int | None, Query(ge=2, le=100)]
MessagePosition = Annotated[int | None, Query(ge=0, le=2_147_483_647)]


def read(db, conversation_id, limit, *, shared=False, before=None, after=None, around=None):
    visible = [Message.conversation_id == conversation_id]
    if shared:
        visible.append(Message.status == 'complete')
    statement = select(Message).where(*visible)
    cursors = sum(value is not None for value in (before, after, around))
    if cursors > 1:
        raise HTTPException(422, '대화 기록의 조회 위치를 하나만 지정하세요.')
    if limit is None and not cursors:
        return list(db.scalars(statement.order_by(Message.position))), None

    limit = limit or DEFAULT_LIMIT
    if around is not None:
        position = db.scalar(select(Message.position).where(*visible, Message.id == str(around)))
        if position is None:
            raise HTTPException(404, '이 대화에서 해당 메시지를 찾을 수 없습니다.')
        older = list(db.scalars(statement.where(Message.position <= position)
            .order_by(Message.position.desc()).limit(limit // 2)))
        newer = list(db.scalars(statement.where(Message.position > position)
            .order_by(Message.position).limit(limit - len(older))))
        messages = older[::-1] + newer
    elif after is not None:
        messages = list(db.scalars(statement.where(Message.position > after).order_by(Message.position).limit(limit)))
    else:
        if before is not None:
            statement = statement.where(Message.position < before)
        messages = list(db.scalars(statement.order_by(Message.position.desc()).limit(limit)))[::-1]

    page = {'older_before': None, 'newer_after': None}
    if messages:
        first, last = messages[0].position, messages[-1].position
        older, newer = db.execute(select(
            select(Message.id).where(*visible, Message.position < first).exists(),
            select(Message.id).where(*visible, Message.position > last).exists(),
        )).one()
        page = {'older_before': first if older else None, 'newer_after': last if newer else None}
    return messages, page
