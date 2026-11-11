from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import Authenticated
from . import config, service, transport

router = APIRouter(prefix='/apps',tags=['apps'])
Database = Annotated[Session,Depends(get_db)]


class AuthorizeInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    chat_id: UUID | None = None
    popup: bool = False
    publish: bool = False


class CompleteInput(BaseModel):
    model_config = ConfigDict(extra='forbid')
    state: str = Field(min_length=32,max_length=200)
    code: str = Field('',max_length=2000)
    error: str = Field('',max_length=100)


@router.get('/catalog')
def catalog(context: Authenticated,db: Database):
    return service.catalog(db,context.user.id)


@router.post('/gitlab/{provider_id}/authorize')
def authorize(provider_id: str,data: AuthorizeInput,context: Authenticated,db: Database,
              x_oauth_browser: str | None = Header(None)):
    return service.authorize(db,context,provider_id,x_oauth_browser,str(data.chat_id) if data.chat_id else None,data.popup,
        publish=data.publish)


@router.post('/gitlab/oauth/complete')
async def complete(data: CompleteInput,context: Authenticated,db: Database,
                   x_oauth_browser: str | None = Header(None)):
    if bool(data.code)==bool(data.error):
        raise HTTPException(400,'GitLab 인증 응답이 올바르지 않습니다.')
    bind,user_id,auth_version,session_id = db.get_bind(),context.user.id,context.user.auth_version,context.payload['sid']
    db.rollback()  # No SQLite read transaction held over network I/O.
    try:
        return await service.complete(bind,user_id,auth_version,session_id,data.state,data.code,data.error,x_oauth_browser)
    except transport.GitlabError as error:
        raise HTTPException(502,str(error)) from None


@router.delete('/connections/{connection_id}')
async def disconnect(connection_id: UUID,context: Authenticated,db: Database):
    bind,user_id = db.get_bind(),context.user.id
    db.rollback()
    return await service.disconnect(bind,user_id,connection_id)


def client_for(db,context,connection_id):
    client = service.GitlabClient(db.get_bind(),context.user.id,connection_id)
    db.rollback()
    return client


async def read(client,path,**kwargs):
    try:
        return await client.get(path,**kwargs)
    except transport.GitlabError as error:
        code = error.status if error.status in {403,404,413,422,429} else 502
        raise HTTPException(code,str(error)) from None


@router.get('/connections/{connection_id}/projects')
async def projects(connection_id: UUID,context: Authenticated,db: Database,
                   q: str = Query('',max_length=100),page: int = Query(1,ge=1,le=100)):
    rows = await read(client_for(db,context,connection_id),'/projects',params={
        'search':q,'simple':'true','order_by':'last_activity_at','per_page':30,'page':page})
    if not isinstance(rows,list):
        raise HTTPException(502,'GitLab 프로젝트 응답 형식을 확인해 주세요.')
    try:
        items = [{'id':row['id'],'name':row['path_with_namespace'],'default_branch':row.get('default_branch')} for row in rows]
    except (KeyError,TypeError):
        raise HTTPException(502,'GitLab 프로젝트 응답 형식을 확인해 주세요.') from None
    return {'items':items, 'has_more':len(rows)==30}


@router.get('/connections/{connection_id}/projects/{project_id}/tree')
async def tree(connection_id: UUID,project_id: int,context: Authenticated,db: Database,
               ref: str = Query('HEAD',min_length=1,max_length=200),path: str = Query('',max_length=600),
               page: int = Query(1,ge=1,le=100)):
    if project_id<1:
        raise HTTPException(422,'프로젝트 ID를 확인하세요.')
    if path:
        try:
            config.validate_path(path)
        except ValueError as error:
            raise HTTPException(422,str(error)) from None
    client = client_for(db,context,connection_id)
    await read(client,f'/projects/{project_id}')
    rows = await read(client,f'/projects/{project_id}/repository/tree',params={
        'ref':ref,'path':path,'per_page':100,'page':page,'recursive':'false'})
    if not isinstance(rows,list):
        raise HTTPException(502,'GitLab 파일 목록 응답 형식을 확인해 주세요.')
    try:
        items = [{'name':row['name'],'path':row['path'],'type':row['type'],
                  'selectable':config.source_file(row['path'])} for row in rows if row.get('type') in {'tree','blob'}]
    except (KeyError,TypeError,AttributeError):
        raise HTTPException(502,'GitLab 파일 목록 응답 형식을 확인해 주세요.') from None
    return {'items':items, 'has_more':len(rows)==100}


@router.get('/connections/{connection_id}/projects/{project_id}/branches')
async def branches(connection_id: UUID,project_id: int,context: Authenticated,db: Database,
                   q: str = Query('',max_length=100),page: int = Query(1,ge=1,le=100)):
    if project_id<1:
        raise HTTPException(422,'프로젝트 ID를 확인하세요.')
    client = client_for(db,context,connection_id)
    await read(client,f'/projects/{project_id}')
    rows = await read(client,f'/projects/{project_id}/repository/branches',params={'search':q,'per_page':100,'page':page})
    if not isinstance(rows,list):
        raise HTTPException(502,'GitLab 브랜치 응답 형식을 확인해 주세요.')
    try:
        items = [{'name':row['name']} for row in rows]
    except (KeyError,TypeError):
        raise HTTPException(502,'GitLab 브랜치 응답 형식을 확인해 주세요.') from None
    return {'items':items, 'has_more':len(rows)==100}
