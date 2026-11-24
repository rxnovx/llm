"""기존 대화·검색·수동 제목을 보존하는 개인별 고정/보관."""
import time

from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,StrictBool,model_validator

from .models import ConversationOrganization


class OrganizationInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision: int=Field(ge=0,strict=True)
    pinned: StrictBool|None=None
    archived: StrictBool|None=None

    @model_validator(mode='after')
    def valid_change(self):
        if self.pinned is None and self.archived is None:
            raise ValueError('고정 또는 보관 상태를 지정하세요.')
        if self.pinned and self.archived:
            raise ValueError('보관과 고정을 동시에 켤 수 없습니다.')
        return self


def public(conversation,*,shared=False):
    state=conversation.organization_state if not shared else None
    return {'pinned':bool(state and state.pinned),'archived':bool(state and state.archived),
        'pinned_at':state.pinned_at if state else 0,'organization_revision':state.revision if state else 0}


def change(conversation,data):
    state=conversation.organization_state
    revision=state.revision if state else 0
    if revision!=data.revision:
        raise HTTPException(409,'다른 화면에서 대화 정리 상태가 변경되었습니다. 목록을 다시 불러온 뒤 시도하세요.')
    if not state:
        state=ConversationOrganization(pinned=False,archived=False,pinned_at=0,revision=0)
        conversation.organization_state=state
    if data.pinned is not None:
        if data.pinned and not state.pinned:state.pinned_at=int(time.time())
        if not data.pinned:state.pinned_at=0
        state.pinned=data.pinned
        if data.pinned:state.archived=False
    if data.archived is not None:
        state.archived=data.archived
        if data.archived:state.pinned=False;state.pinned_at=0
    state.revision+=1


def wake(conversation):
    state=conversation.organization_state
    if state and state.archived:
        state.archived=False
        state.revision+=1
