import json
import time
from typing import Annotated
from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter,Depends,Header,HTTPException,Query
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select,update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.db.session import get_db
from app.modules.auth.dependencies import Authenticated
from app.modules.integrations.config import digest
from app.modules.integrations.service import credentials
from app.modules.integrations.tools import GitlabSelection
from terminal_mcp.auth import verify
from . import client,service
from .models import CheckoutTicket,Workspace

router=APIRouter(prefix='/workspaces',tags=['workspaces'])
Database=Annotated[Session,Depends(get_db)]


class CreateInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    conversation_id: UUID
    selection: GitlabSelection


class ToolInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str=Field(min_length=1,max_length=64)
    arguments: dict=Field(default_factory=dict)


class TicketInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    ticket: str=Field(min_length=32,max_length=100)
    workspace_id: UUID


@router.get('/config')
def config(context: Authenticated):
    settings=get_settings()
    return {'enabled':client.enabled(),'execution_enabled':settings.terminal_execution_enabled,
        'max_steps':settings.terminal_agent_max_steps}


@router.get('')
def listing(context: Authenticated,db: Database,conversation_id: UUID):
    rows=db.scalars(select(Workspace).where(Workspace.user_id==context.user.id,
        Workspace.conversation_id==str(conversation_id)).order_by(Workspace.created_at.desc()).limit(30))
    return {'items':[service.public(row) for row in rows]}


@router.post('',status_code=201)
async def create(data: CreateInput,context: Authenticated,db: Database):
    if data.selection.mode=='files':raise HTTPException(422,'프로젝트 분석 또는 실행 모드를 선택하세요.')
    bind,user_id=db.get_bind(),context.user.id
    db.rollback()
    return service.public(await service.ensure(bind,user_id,str(data.conversation_id),data.selection))


@router.post('/{workspace_id}/tools')
async def tool(workspace_id: UUID,data: ToolInput,context: Authenticated,db: Database):
    row=service.owned(db,context.user.id,workspace_id)
    db.expunge(row)
    bind=db.get_bind();db.rollback()
    return await service.invoke(bind,row,data.name,data.arguments)


@router.delete('/{workspace_id}')
async def close(workspace_id: UUID,context: Authenticated,db: Database):
    row=service.owned(db,context.user.id,workspace_id)
    db.expunge(row);bind=db.get_bind();db.rollback()
    result=await client.call(row,'workspace_close',{'workspace_id':row.id})
    with Session(bind) as session:
        session.execute(update(Workspace).where(Workspace.id==row.id).values(status='closed'));session.commit()
    return result


@router.post('/internal/checkout',include_in_schema=False)
async def checkout(data: TicketInput,db: Database,authorization: str | None=Header(None)):
    try:
        raw=authorization or ''
        if not raw.startswith('Bearer '):raise PermissionError()
        ctx=verify(raw[7:],get_settings().terminal_shared_secret.get_secret_value(),audience='llm-terminal-broker')
    except PermissionError:
        raise HTTPException(401,'가져오기 서비스 인증이 필요합니다.') from None
    row=service.owned(db,ctx['sub'],data.workspace_id,ctx['conversation'])
    if ctx['workspace']!=row.id or row.status!='creating':raise HTTPException(403,'잘못된 가져오기 승인입니다.')
    service.authorize(db,row)
    ticket=db.get(CheckoutTicket,digest(data.ticket))
    if not ticket or ticket.workspace_id!=row.id or ticket.expires_at<=time.time() or ticket.consumed:
        raise HTTPException(400,'가져오기 승인이 만료되었습니다.')
    claimed=db.execute(update(CheckoutTicket).where(CheckoutTicket.hash==ticket.hash,CheckoutTicket.consumed.is_(False)).values(consumed=True))
    if claimed.rowcount!=1:raise HTTPException(400,'이미 사용된 가져오기 승인입니다.')
    db.commit()
    db.expunge(row)
    bind=db.get_bind();db.rollback()
    auth=await credentials(bind,row.user_id,row.connection_id)
    if auth.generation!=row.connection_generation:raise HTTPException(409,'GitLab 연결이 변경되었습니다.')
    source=json.loads(row.source_json)
    return source|{'clone_url':auth.provider.base_url+'/'+quote(source['project'],safe='/')+'.git',
                   'access_token':auth.token,'ca_bundle':get_settings().gitlab_ca_bundle}
