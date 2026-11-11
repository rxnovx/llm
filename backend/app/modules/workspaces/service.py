import json
import secrets
import time
from urllib.parse import quote

from fastapi import HTTPException
from sqlalchemy import delete,select,update
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.modules.chat.models import Conversation
from app.modules.auth.models import User
from app.modules.integrations.config import digest,validate_path
from app.modules.integrations.models import AppConnection
from app.modules.integrations.service import GitlabClient,credentials
from app.modules.integrations.transport import GitlabError
from . import client
from .models import CheckoutTicket,Workspace


def public(row):
    return {'id':row.id,'conversation_id':row.conversation_id,'status':row.status,'allow_execution':row.allow_execution,
        'source':json.loads(row.source_json),'project_id':row.project_id,'ref':row.ref,'created_at':row.created_at}


def owned(db,user_id,workspace_id,conversation_id=None):
    row=db.scalar(select(Workspace).where(Workspace.id==str(workspace_id),Workspace.user_id==user_id))
    if not row or conversation_id and row.conversation_id!=conversation_id:
        raise HTTPException(404,'내 작업공간을 찾을 수 없습니다.')
    return row


def authorize(db,row):
    account=db.get(AppConnection,row.connection_id)
    conversation=db.get(Conversation,row.conversation_id)
    user=db.get(User,row.user_id)
    if (not account or account.user_id!=row.user_id or account.status!='connected'
            or account.generation!=row.connection_generation or not conversation or conversation.user_id!=row.user_id
            or not user or not user.is_active or user.registration_status!='approved'):
        raise HTTPException(409,'GitLab 연결이나 대화가 변경되었습니다. 작업공간을 다시 연결하세요.')
    from app.modules.integrations.config import instance
    if instance(account.provider_id).granted_scopes(account.provider_fingerprint) is None:
        raise HTTPException(409,'GitLab 서버 등록이 변경되었습니다. 계정을 다시 연결하세요.')
    return account


async def permission(bind,row):
    with Session(bind) as db:authorize(db,row)
    upstream=GitlabClient(bind,row.user_id,row.connection_id)
    try:await upstream.get(f'/projects/{row.project_id}')
    except GitlabError as error:raise HTTPException(403,str(error)) from None


async def ensure(bind,user_id,conversation_id,selection,*,expected_commit=None,expected_generation=None):
    if selection.mode not in {'workspace', 'execute'} or selection.merge_request and not expected_commit:
        raise HTTPException(422, 'MR 작업공간은 채팅에서 선택하세요.' if selection.merge_request else '프로젝트 분석 또는 실행 모드를 선택하세요.')
    if not client.enabled():raise HTTPException(503,'운영자가 Terminal MCP 서버를 연결해야 합니다.')
    execute=selection.mode=='execute'
    if execute and not get_settings().terminal_execution_enabled:
        raise HTTPException(403,'이 앱에서는 터미널 실행을 허용하지 않습니다.')
    with Session(bind,expire_on_commit=False) as db:
        conversation=db.get(Conversation,conversation_id)
        if not conversation or conversation.user_id!=user_id:raise HTTPException(404,'대화를 찾을 수 없습니다.')
        if selection.workspace_id:
            row=owned(db,user_id,selection.workspace_id,conversation_id)
            if (row.connection_id!=str(selection.connection_id) or row.project_id!=selection.project_id or row.ref!=selection.ref
                    or row.allow_execution!=execute or row.status!='ready'):
                raise HTTPException(409,'선택한 프로젝트·실행 모드와 작업공간이 일치하지 않습니다.')
        else:
            row=db.scalar(select(Workspace).where(Workspace.user_id==user_id,Workspace.conversation_id==conversation_id,
                Workspace.connection_id==str(selection.connection_id),Workspace.project_id==selection.project_id,
                Workspace.ref==selection.ref,Workspace.allow_execution==execute,Workspace.status=='ready').order_by(Workspace.created_at.desc()))
        if row:authorize(db,row)
        if row and expected_commit and json.loads(row.source_json).get('commit') != expected_commit:
            row = None  # Keep the previous copy, but check out the current MR head.
        if row and expected_generation is not None and row.connection_generation != expected_generation:
            raise HTTPException(409, 'GitLab 계정 연결이 변경되었습니다. MR을 다시 선택하세요.')
    if row:
        await permission(bind,row)
        try:
            info=await client.call(row,'workspace_inspect',{'workspace_id':row.id})
            if execute and not info.get('can_execute'):
                raise HTTPException(403,'Terminal MCP 서버의 실행 워커를 먼저 설정하세요.')
            return row
        except HTTPException as error:
            if error.status_code!=422:raise
            with Session(bind) as db:
                db.execute(update(Workspace).where(Workspace.id==row.id).values(status='closed'));db.commit()
            selection.workspace_id=None
            return await ensure(bind,user_id,conversation_id,selection,
                expected_commit=expected_commit,expected_generation=expected_generation)
    upstream=GitlabClient(bind,user_id,selection.connection_id)
    upstream.generation=expected_generation
    try:
        path=f'/projects/{selection.project_id}'
        project=await upstream.get(path)
        ref=quote(selection.ref,safe='')
        if expected_commit:
            branch=await upstream.get(path + f'/repository/branches/{ref}')
            if branch.get('name') != selection.ref:
                raise HTTPException(409, 'MR의 소스 브랜치를 확인할 수 없습니다.')
            commit=branch['commit']
        else:
            commit=await upstream.get(path + f'/repository/commits/{ref}')
        namespace=validate_path(project['path_with_namespace'])
        import re
        if not re.fullmatch('[a-fA-F0-9]{40,64}',commit['id']):raise ValueError()
    except GitlabError as error:raise HTTPException(502,str(error)) from None
    except (ValueError,KeyError,TypeError,AttributeError):raise HTTPException(502,'GitLab 저장소 응답 형식을 확인하세요.') from None
    if expected_commit and (project.get('id') != selection.project_id or commit['id'] != expected_commit):
        raise HTTPException(409, 'MR 소스 브랜치가 변경되었습니다. 최신 MR에서 다시 실행하세요.')
    source={'project':namespace,'commit':commit['id'],'base_url':upstream.provider.base_url,'project_id':selection.project_id}
    with Session(bind,expire_on_commit=False) as db:
        row=Workspace(user_id=user_id,conversation_id=conversation_id,connection_id=str(selection.connection_id),
            connection_generation=upstream.generation,project_id=selection.project_id,ref=selection.ref,
            allow_execution=execute,source_json=json.dumps(source))
        db.add(row);db.flush()
        token=secrets.token_urlsafe(32)
        db.execute(delete(CheckoutTicket).where(CheckoutTicket.expires_at<=int(time.time())))
        db.add(CheckoutTicket(hash=digest(token),workspace_id=row.id,expires_at=int(time.time())+180))
        db.commit()
    try:
        info=await client.call(row,'workspace_create',{},provision=True)
        if execute and not info.get('can_execute'):
            raise HTTPException(403,'Terminal MCP 서버의 실행 워커를 먼저 설정하세요.')
        await client.call(row,'gitlab_checkout',{'workspace_id':row.id,'ticket':token},provision=True)
        await permission(bind,row)
        with Session(bind) as db:
            db.execute(update(Workspace).where(Workspace.id==row.id).values(status='ready'));db.commit()
        row.status='ready'
        return row
    except BaseException:
        with Session(bind) as db:
            db.execute(update(Workspace).where(Workspace.id==row.id).values(status='error'));db.commit()
        # A partially fetched repository must not become an orphaned executable workspace.
        try:await client.call(row,'workspace_close',{'workspace_id':row.id},provision=True)
        except Exception:pass
        raise


async def invoke(bind,row,name,args):
    if row.status!='ready':raise HTTPException(409,'작업공간을 사용할 수 없습니다.')
    if name in {'terminal_read','terminal_jobs'}:
        with Session(bind) as db:authorize(db,row)
    elif name!='terminal_cancel':await permission(bind,row)
    if name not in client.MODEL_TOOLS:raise HTTPException(403,'허용되지 않은 도구입니다.')
    if name in client.EXECUTION_TOOLS and (not row.allow_execution or not get_settings().terminal_execution_enabled):
        raise HTTPException(403,'분석 전용 작업공간에서는 실행·수정할 수 없습니다.')
    args=dict(args)
    if name in {'terminal_read','terminal_input','terminal_cancel'}:
        # The MCP server additionally checks the job belongs to this exact workspace and owner.
        pass
    else:
        if args.get('workspace_id',row.id)!=row.id:raise HTTPException(403,'다른 작업공간을 사용할 수 없습니다.')
        args['workspace_id']=row.id
    result=await client.call(row,name,args)
    if name not in {'terminal_read','terminal_jobs','terminal_cancel'}:await permission(bind,row)
    return result


async def close_copies(bind,user_id,*,conversation_id=None,connection_id=None):
    import asyncio
    with Session(bind,expire_on_commit=False) as db:
        query=select(Workspace).where(Workspace.user_id==user_id,Workspace.status.in_(['ready','creating','error']))
        if conversation_id:query=query.where(Workspace.conversation_id==conversation_id)
        if connection_id:query=query.where(Workspace.connection_id==connection_id)
        rows=list(db.scalars(query))
        for row in rows:row.status='closed'
        db.commit()
    async def close(row):
        try:
            async with asyncio.timeout(5):await client.call(row,'workspace_close',{'workspace_id':row.id},provision=True)
        except Exception:pass  # Remote outage: local access is revoked; worker TTL/deadline still applies.
    await asyncio.gather(*(close(row) for row in rows))
