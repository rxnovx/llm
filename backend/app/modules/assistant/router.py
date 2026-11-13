import json
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter,Depends,HTTPException
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import func,select
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.core.config import get_settings
from app.modules.auth.dependencies import Authenticated
from app.modules.admin.router import Admin
from app.modules.auth.audit import add_audit_log
from app.modules.chat.scheduler import lock
from app.modules.knowledge.models import Document,VectorGarbage
from . import mcp, personal_tools
from .models import Preference,McpSetting

router=APIRouter(prefix='/assistant',tags=['assistant'])
Database=Annotated[Session,Depends(get_db)]

class PreferencesInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    instructions: str=Field('',max_length=1800)
    memory_enabled: bool=False
    revision: int=Field(0,ge=0)
class MemoryInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    content: str=Field(min_length=1,max_length=600)

@router.get('/catalog')
def catalog(context: Authenticated,db: Database):
    values=[]
    for server in mcp.servers(db):
        if not server.enabled or server.allowed_users and context.user.username not in server.allowed_users and not context.user.is_global_admin:continue
        values.append({'id':server.id,'name':server.name,'read_tools':server.read_tools,'write_tools':server.write_tools,'description':server.description,'auto_select':server.auto_select})
    return {'servers':values}

@router.get('/preferences')
def preferences(context: Authenticated,db: Database):
    row=db.get(Preference,context.user.id)
    return {'instructions':row.instructions if row else '', 'memory_enabled':row.memory_enabled if row else False,'revision':row.revision if row else 0}

@router.put('/preferences')
def edit_preferences(data: PreferencesInput,context: Authenticated,db: Database):
    lock(db);row=db.get(Preference,context.user.id)
    if data.revision!=(row.revision if row else 0):raise HTTPException(409,'다른 창에서 설정이 변경되었습니다. 다시 여세요.')
    if not row:row=Preference(user_id=context.user.id);db.add(row)
    row.instructions,row.memory_enabled,row.revision=data.instructions.strip(),data.memory_enabled,data.revision+1
    db.commit();return {'instructions':row.instructions,'memory_enabled':row.memory_enabled,'revision':row.revision}

@router.get('/memories')
def memories(context: Authenticated,db: Database):
    return {'items': personal_tools.list_memories(db, context.user.id)}

@router.post('/memories',status_code=201)
def remember(data: MemoryInput,context: Authenticated,db: Database):
    row = personal_tools.save_memory(db, context.user.id, data.content)
    db.commit()
    return row

@router.delete('/memories/{memory_id}')
def forget(memory_id: UUID,context: Authenticated,db: Database):
    personal_tools.delete_memory(db, context.user.id, memory_id)
    db.commit()
    return {'deleted':True}

@router.get('/mcp/servers')
def servers(context: Admin,db: Database):return {'items':[s.model_dump() for s in mcp.servers(db)]}

@router.put('/mcp/servers/{server_id}')
def register(server_id: str,data: mcp.ServerInput,context: Admin,db: Database):
    if server_id!=data.id:raise HTTPException(422,'서버 ID가 일치하지 않습니다.')
    lock(db);existing=db.get(McpSetting,data.id)
    if data.revision!=(existing.revision if existing else 0):raise HTTPException(409,'서버 등록이 변경되었습니다. 다시 조회하세요.')
    if not existing and db.scalar(select(func.count()).select_from(McpSetting))>=30:raise HTTPException(422,'MCP 서버 등록 한도입니다.')
    row=existing or McpSetting(id=data.id)
    row.data_json=data.model_dump_json(exclude={'revision'});row.revision=data.revision+1
    db.add(row);add_audit_log(db,action='assistant.mcp.updated',actor_user_id=context.user.id,details={'id':data.id,'enabled':data.enabled})
    db.commit();return data.model_dump()|{'revision':row.revision}

@router.post('/mcp/servers/{server_id}/check')
async def check(server_id: str,context: Admin,db: Database):
    server=next((s for s in mcp.servers(db) if s.id==server_id),None)
    if not server:raise HTTPException(404,'MCP 서버를 찾을 수 없습니다.')
    db.rollback()
    result=await mcp.operation(server,lambda client:client.list_tools())
    return {'tools':[{'name':t.name,'description':(t.description or '')[:400],'allowed':t.name in server.read_tools+server.write_tools} for t in result.tools][:100]}

@router.get('/operations')
def operations(context: Admin,db: Database):
    statuses=dict(db.execute(select(Document.status,func.count()).group_by(Document.status)).all())
    return {'documents':statuses,'vector_cleanup_pending':db.scalar(select(func.count()).select_from(VectorGarbage)),
        'search_mode':'lexical' if get_settings().knowledge_embedding_provider=='disabled' else 'hybrid',
        'mcp_servers':len(mcp.servers(db))}
