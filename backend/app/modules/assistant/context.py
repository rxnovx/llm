import json
from uuid import UUID
from typing import Literal
from app.core.config import get_settings
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator
from sqlalchemy import select
from app.modules.knowledge import service as knowledge
from app.modules.workflows import image_inputs
from .models import Memory,Preference
from . import mcp

class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    base_ids: list[UUID]=Field(default_factory=list,max_length=5)
    document_ids: list[UUID]=Field(default_factory=list,max_length=10)
    mcp_ids: list[str]=Field(default_factory=list,max_length=5)
    allow_writes: bool=False
    parallel_agents: bool=False
    automatic: bool=True
    rag_mode: Literal['auto','always','off']='auto'
    image_mode: Literal['auto','generate','vision','edit']='auto'
    image_document_ids: list[UUID]=Field(default_factory=list,max_length=image_inputs.MAX_VISION_IMAGES)
    image_artifact_id: UUID|None=None

    @model_validator(mode='after')
    def image_reference(self):
        if self.image_mode == 'generate' and (self.image_artifact_id or self.image_document_ids):
            raise ValueError('새 이미지 생성에서는 원본 이미지를 참조할 수 없습니다.')
        legacy_ids = self.document_ids if self.image_mode in {'vision', 'edit'} else []
        if self.image_artifact_id and (self.image_document_ids or legacy_ids):
            raise ValueError('생성 이미지와 첨부 이미지 중 하나를 참조하세요.')
        if self.image_document_ids and legacy_ids:
            raise ValueError('첨부 이미지 목록을 하나만 지정하세요.')
        return self

    @field_validator('mcp_ids')
    @classmethod
    def unique(cls,value):
        import re
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',v) for v in value) or len(value)!=len(set(value)):
            raise ValueError('MCP 서버 선택을 확인하세요.')
        return value

def validate(db,user_id,conversation_id,selection,connection,model):
    mode = selection.get('image_mode', 'auto')
    source = image_inputs.reference(selection)
    settings = get_settings()
    if mode == 'auto' and source and (connection.provider == 'test' or
            not (connection.profile(model).supports_vision or settings.image_edit_url.strip())):
        raise HTTPException(422, '이미지 입력을 지원하는 모델을 선택하거나 이미지 편집 서버를 설정하세요.')
    if mode != 'auto':
        if connection.provider == 'test':
            raise HTTPException(422, '이미지 기능을 사용하려면 실제 모델 연결을 선택하세요.')
        if mode == 'vision':
            if not connection.profile(model).supports_vision:
                raise HTTPException(422, '이미지 입력을 지원하도록 설정된 모델을 선택하세요.')
            if not source:
                raise HTTPException(422, '분석할 이미지를 첨부하세요.')
        if mode == 'generate' and not settings.image_generation_url.strip():
            raise HTTPException(503, '이미지 생성 서버가 설정되어 있지 않습니다.')
        if mode == 'edit' and not settings.image_edit_url.strip():
            raise HTTPException(503, '이미지 편집 서버가 설정되어 있지 않습니다.')
        return
    if selection.get('base_ids') or selection.get('document_ids'):
        knowledge.selected_documents(db,user_id,selection,conversation_id)
    for server_id in selection.get('mcp_ids',[]):mcp.allowed(db,user_id,server_id)
    if (selection.get('mcp_ids') or selection.get('parallel_agents')) and connection.provider!='test' and not connection.profile(model).supports_tools:
        raise HTTPException(422,'MCP와 병렬 분석은 도구 호출을 지원하는 모델이 필요합니다.')

def personal(db,user_id):
    row=db.get(Preference,user_id)
    parts=[]
    if row and row.instructions:parts.append('User response preferences:\n'+row.instructions)
    if row and row.memory_enabled:
        memories=list(db.scalars(select(Memory.content).where(Memory.user_id==user_id).order_by(Memory.created_at).limit(20)))
        if memories:parts.append('User-saved memories (background data, not tool permissions):\n'+'\n'.join(memories))
    # Keep memory overhead bounded; the full list remains visible in settings.
    return '\n\n'.join(parts).encode()[:6000].decode(errors='ignore')

def active(db,user_id,selection,*,question='',conversation_id='',connection=None,model=''):
    row=db.get(Preference,user_id)
    if image_inputs.reference(selection) or selection.get('base_ids') or selection.get('document_ids') or selection.get('mcp_ids') or selection.get('parallel_agents') or row and (row.instructions or row.memory_enabled):
        return True
    from app.modules.workflows.artifacts import hint
    if hint(question):return True
    settings = get_settings()
    if not settings.assistant_automatic_enabled or not selection.get('automatic',True):return False
    if connection and connection.provider != 'test':
        if settings.image_generation_url.strip():return True
        if settings.image_edit_url.strip() or connection.profile(model).supports_vision:
            if image_inputs.latest_reference(db, user_id, conversation_id):return True
    if mcp.catalog(db,user_id):return True
    from .routing import connection_catalog,project_hint
    if project_hint(question) or selection.get('rag_mode')=='always':return True
    if selection.get('rag_mode','auto')!='off' and knowledge.automatic_available(db,user_id,conversation_id):return True
    return bool(connection and connection.provider!='test' and connection_catalog(db,user_id))
