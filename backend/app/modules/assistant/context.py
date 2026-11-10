import json
from uuid import UUID
from typing import Literal
from app.core.config import get_settings
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,field_validator
from sqlalchemy import select
from app.modules.knowledge import service as knowledge
from .models import Memory,Preference
from . import mcp

class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    base_ids: list[UUID]=Field(default_factory=list,max_length=5)
    document_ids: list[UUID]=Field(default_factory=list,max_length=10)
    mcp_ids: list[str]=Field(default_factory=list,max_length=5)
    allow_writes: bool=False
    automatic: bool=True
    rag_mode: Literal['auto','always','off']='auto'

    @field_validator('mcp_ids')
    @classmethod
    def unique(cls,value):
        import re
        if any(not re.fullmatch(r'[A-Za-z0-9_-]{1,40}',v) for v in value) or len(value)!=len(set(value)):
            raise ValueError('MCP 서버 선택을 확인하세요.')
        return value

def validate(db,user_id,conversation_id,selection,connection,model):
    if selection.get('base_ids') or selection.get('document_ids'):
        knowledge.selected_documents(db,user_id,selection,conversation_id)
    for server_id in selection.get('mcp_ids',[]):mcp.allowed(db,user_id,server_id)
    if selection.get('mcp_ids') and connection.provider!='test' and not connection.profile(model).supports_tools:
        raise HTTPException(422,'MCP를 사용하려면 도구 호출을 지원하도록 설정된 모델을 선택하세요.')

def personal(db,user_id):
    row=db.get(Preference,user_id)
    parts=[]
    if row and row.instructions:parts.append('User response preferences:\n'+row.instructions)
    if row and row.memory_enabled:
        memories=list(db.scalars(select(Memory.content).where(Memory.user_id==user_id).order_by(Memory.created_at).limit(20)))
        if memories:parts.append('User-saved memories (background data, not tool permissions):\n'+'\n'.join(memories))
    # Keep memory overhead bounded; the full list remains visible in settings.
    return '\n\n'.join(parts).encode()[:6000].decode(errors='ignore')

def active(db,user_id,selection,*,question='',conversation_id='',connection=None):
    row=db.get(Preference,user_id)
    if selection.get('base_ids') or selection.get('document_ids') or selection.get('mcp_ids') or row and (row.instructions or row.memory_enabled):
        return True
    if not get_settings().assistant_automatic_enabled or not selection.get('automatic',True):return False
    from .routing import connection_catalog,project_hint
    if project_hint(question) or selection.get('rag_mode')=='always':return True
    if selection.get('rag_mode','auto')!='off' and knowledge.automatic_available(db,user_id,conversation_id):return True
    return bool(connection and connection.provider!='test' and connection_catalog(db,user_id))
