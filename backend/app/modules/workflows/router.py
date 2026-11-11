import asyncio
import hashlib
import json
import time
from typing import Annotated,Literal
from urllib.parse import quote
from uuid import UUID,uuid4
from fastapi import APIRouter,Depends,HTTPException,Query,Response
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import func,select,update
from sqlalchemy.orm import Session
from app.db.session import get_db
from app.modules.auth.dependencies import Authenticated
from app.modules.chat.models import Message
from app.modules.chat.scheduler import lock
from app.modules.knowledge.models import KnowledgeBase,Document
from app.modules.knowledge import service as knowledge
from . import conversations as service,artifacts
from .models import Project,ProjectConversation,MessageVersion,ConversationState,UploadAttempt,Feedback,Artifact

router=APIRouter(prefix='/workflows',tags=['workflows'])
Database=Annotated[Session,Depends(get_db)]


class ProjectInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name: str=Field(min_length=1,max_length=120)
    instructions: str=Field('',max_length=1800)
    revision: int=Field(0,ge=0)


class ProjectLink(BaseModel):
    model_config=ConfigDict(extra='forbid')
    project_id: UUID|None=None


class ForkInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    message_id: UUID
    content: str|None=Field(None,max_length=16000)


class SummaryInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    enabled: bool


class ExportInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    format: Literal['md','txt','csv','xlsx','pdf']
    name: str=Field('대화 결과',min_length=1,max_length=140)


class UploadInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    filename: str=Field(min_length=1,max_length=240)
    base_id: UUID|None=None
    conversation_id: UUID|None=None


class FeedbackInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    category: Literal['correctness','source','missing','other']
    detail: str=Field('',max_length=1500)


class FeedbackStatus(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status: Literal['open','resolved']


def project_data(row):
    return {k:getattr(row,k) for k in ['id','name','instructions','base_id','revision','created_at']}


def detail(db,user_id,item):
    from app.modules.chat.router import conversation_data
    messages=list(db.scalars(select(Message).where(Message.conversation_id==item.id).order_by(Message.position)))
    return {'conversation':conversation_data(item),'messages':[service.payload(m) for m in messages],'generation':None}


@router.get('/projects')
def projects(context: Authenticated,db: Database):
    rows=db.scalars(select(Project).where(Project.user_id==context.user.id).order_by(Project.created_at.desc()))
    return {'items':[project_data(r) for r in rows]}


@router.post('/projects',status_code=201)
def create_project(data: ProjectInput,context: Authenticated,db: Database):
    lock(db)
    if db.scalar(select(func.count()).select_from(Project).where(Project.user_id==context.user.id))>=20:raise HTTPException(422,'프로젝트는 20개까지 만들 수 있습니다.')
    if db.scalar(select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.owner_id==context.user.id))>=20:raise HTTPException(422,'지식베이스 한도입니다. 기존 자료를 정리하세요.')
    base=KnowledgeBase(owner_id=context.user.id,name=data.name[:100]+' · 프로젝트 자료');db.add(base);db.flush()
    row=Project(user_id=context.user.id,base_id=base.id,name=data.name,instructions=data.instructions);db.add(row);db.commit()
    return project_data(row)


@router.patch('/projects/{project_id}')
def edit_project(project_id: UUID,data: ProjectInput,context: Authenticated,db: Database):
    lock(db);row=service.project_owned(db,context.user.id,project_id)
    if data.revision!=row.revision:raise HTTPException(409,'다른 창에서 프로젝트를 변경했습니다. 다시 여세요.')
    row.name,row.instructions,row.revision=data.name,data.instructions,row.revision+1;db.commit();return project_data(row)


@router.delete('/projects/{project_id}')
def delete_project(project_id: UUID,context: Authenticated,db: Database):
    lock(db);row=service.project_owned(db,context.user.id,project_id)
    db.delete(row);db.commit();return {'deleted':True,'conversations_and_documents_preserved':True}


@router.patch('/conversations/{conversation_id}/project')
def move_conversation(conversation_id: UUID,data: ProjectLink,context: Authenticated,db: Database):
    lock(db);service.attach_project(db,context.user.id,str(conversation_id),data.project_id);db.commit()
    from app.modules.chat.router import conversation_data
    return conversation_data(service.owned(db,context.user.id,conversation_id))


@router.patch('/conversations/{conversation_id}/auto-summary')
def set_auto_summary(conversation_id: UUID,data: SummaryInput,context: Authenticated,db: Database):
    lock(db);service.owned(db,context.user.id,conversation_id,idle=True)
    row=db.get(ConversationState,str(conversation_id))
    if not row:row=ConversationState(conversation_id=str(conversation_id));db.add(row)
    row.auto_summary=data.enabled;db.commit();return {'enabled':row.auto_summary}


@router.post('/conversations/{conversation_id}/fork',status_code=201)
def fork(conversation_id: UUID,data: ForkInput,context: Authenticated,db: Database):
    child,carry=service.fork(db,context.user.id,str(conversation_id),str(data.message_id),content=data.content)
    db.commit();return detail(db,context.user.id,child)|carry


@router.post('/conversations/{conversation_id}/continue',status_code=201)
def continue_conversation(conversation_id: UUID,context: Authenticated,db: Database):
    parent=service.owned(db,context.user.id,conversation_id,idle=True)
    target=db.scalar(select(Message).where(Message.conversation_id==parent.id,Message.role=='assistant',Message.status=='complete').order_by(Message.position.desc()))
    if not target:raise HTTPException(422,'완료된 답변이 있어야 이어갈 수 있습니다.')
    child,carry=service.fork(db,context.user.id,parent.id,target.id,tail_only=True);db.commit()
    return detail(db,context.user.id,child)|carry


@router.get('/messages/{message_id}/versions')
def versions(message_id: UUID,context: Authenticated,db: Database):
    message,_=service.message_owned(db,context.user.id,message_id)
    return {'current':service.payload(message),'items':[{'id':v.id,'sequence':v.sequence,'model':v.model,'created_at':v.created_at,'message':json.loads(v.payload_json)} for v in message.versions]}


@router.post('/messages/{message_id}/versions/{version_id}/select')
def select_version(message_id: UUID,version_id: UUID,context: Authenticated,db: Database):
    lock(db);message,conversation=service.message_owned(db,context.user.id,message_id,idle=True)
    version=db.get(MessageVersion,str(version_id))
    if not version or version.message_id!=message.id:raise HTTPException(404,'답변 버전을 찾을 수 없습니다.')
    last=db.scalar(select(func.max(Message.position)).where(Message.conversation_id==conversation.id))
    from app.modules.chat.models import ContextNote
    note=db.get(ContextNote,conversation.id)
    if message.position!=last or note and note.summary and note.through_position>=message.position:
        child,carry=service.fork(db,context.user.id,conversation.id,message.id,version=version)
        db.commit();return detail(db,context.user.id,child)|carry|{'forked':True}
    if not message.versions:service.capture(db,message)
    data=json.loads(version.payload_json);service.apply_payload(db,message,data)
    state=service.state_for(db,message);state.active_version=version.id
    from .models import AutoSummary
    auto=db.get(AutoSummary,conversation.id)
    if auto and auto.through_position>=message.position:db.delete(auto)
    conversation.updated_at=int(time.time());db.commit()
    return detail(db,context.user.id,conversation)|{'forked':False}


@router.get('/formats')
def formats(context: Authenticated):return {'formats':artifacts.formats()}


@router.post('/messages/{message_id}/export',status_code=201)
async def export(message_id: UUID,data: ExportInput,context: Authenticated,db: Database):
    message,_=service.message_owned(db,context.user.id,message_id)
    if message.role!='assistant' or message.status!='complete':raise HTTPException(409,'완료된 답변을 내보낼 수 있습니다.')
    args=artifacts.export_input(data.name,data.format,message.content)
    content_hash=hashlib.sha256(message.content.encode()).hexdigest()
    bind,user_id=db.get_bind(),context.user.id;db.rollback()
    result=await asyncio.to_thread(artifacts.create,bind,user_id,str(message_id),str(uuid4()),args,ready=True,expected_content_hash=content_hash)
    message,_=service.message_owned(db,user_id,message_id)
    state=service.state_for(db,message);ids=json.loads(state.artifact_ids_json or '[]');ids.append(result['id']);state.artifact_ids_json=json.dumps(ids)
    db.expire(message,['generated_files'])
    if state.active_version:
        version=db.get(MessageVersion,state.active_version)
        if version:
            payload=json.loads(version.payload_json);payload['artifacts']=service.message_extra(message)['artifacts'];version.payload_json=json.dumps(payload,ensure_ascii=False)
    db.commit();return result


def owned_artifact(db,user_id,artifact_id):
    row=db.get(Artifact,str(artifact_id))
    if not row or row.owner_id!=user_id or row.status!='ready':raise HTTPException(404,'결과 파일을 찾을 수 없습니다.')
    return row


@router.get('/artifacts/{artifact_id}')
def preview(artifact_id: UUID,context: Authenticated,db: Database):
    row=owned_artifact(db,context.user.id,artifact_id);data=json.loads(row.payload_json)
    return service.artifact_public(row)|{'content':data['content'][:20000],'columns':data['columns'],'rows':data['rows'][:100],
        'preview_truncated':len(data['content'])>20000 or len(data['rows'])>100}


@router.get('/artifacts/{artifact_id}/download')
def download(artifact_id: UUID,context: Authenticated,db: Database):
    row=owned_artifact(db,context.user.id,artifact_id)
    return Response(row.data,media_type=artifacts.MIME[row.format],headers={
        'Content-Disposition':"attachment; filename=download."+row.format+"; filename*=UTF-8''"+quote(row.name,safe=''),
        'X-Content-Type-Options':'nosniff','Cache-Control':'no-store'})


@router.delete('/artifacts/{artifact_id}')
def delete_artifact(artifact_id: UUID,context: Authenticated,db: Database):
    row=owned_artifact(db,context.user.id,artifact_id);db.delete(row);db.commit();return {'deleted':True}


@router.post('/uploads',status_code=201)
def upload_attempt(data: UploadInput,context: Authenticated,db: Database):
    lock(db)
    from sqlalchemy import delete
    db.execute(delete(UploadAttempt).where(UploadAttempt.expires_at<int(time.time())))
    if db.scalar(select(func.count()).select_from(UploadAttempt).where(UploadAttempt.owner_id==context.user.id,UploadAttempt.status=='pending'))>=20:raise HTTPException(422,'진행 중인 첨부 요청이 많습니다. 기존 요청을 취소하세요.')
    if bool(data.base_id)==bool(data.conversation_id):raise HTTPException(422,'자료 위치를 지정하세요.')
    if data.base_id:knowledge.base_access(db,context.user.id,data.base_id,write=True)
    else:service.owned(db,context.user.id,data.conversation_id)
    row=UploadAttempt(owner_id=context.user.id,filename=data.filename,base_id=str(data.base_id) if data.base_id else None,
        conversation_id=str(data.conversation_id) if data.conversation_id else None)
    db.add(row);db.commit();return {'id':row.id,'status':row.status}


@router.post('/uploads/{upload_id}/cancel')
def cancel_upload(upload_id: UUID,context: Authenticated,db: Database):
    lock(db);row=db.get(UploadAttempt,str(upload_id))
    if not row or row.owner_id!=context.user.id:raise HTTPException(404,'첨부 요청을 찾을 수 없습니다.')
    row.status='cancelled'
    if row.created_document and row.document_id:
        doc=db.get(Document,row.document_id)
        if doc and doc.owner_id==context.user.id:knowledge.remove(db,doc)
    db.commit();return {'cancelled':True}


@router.post('/messages/{message_id}/feedback',status_code=201)
def report(message_id: UUID,data: FeedbackInput,context: Authenticated,db: Database):
    lock(db);message,_=service.message_owned(db,context.user.id,message_id)
    if message.role!='assistant':raise HTTPException(422,'답변에 대한 의견을 남겨 주세요.')
    if db.scalar(select(func.count()).select_from(Feedback).where(Feedback.owner_id==context.user.id,Feedback.message_id==message.id))>=5:raise HTTPException(422,'이 답변의 신고 한도입니다.')
    state=message.workflow_state
    if message.status=='complete' and (not state or not state.active_version):service.capture(db,message);state=message.workflow_state
    snapshot=service.payload(message)
    row=Feedback(owner_id=context.user.id,message_id=message.id,version_id=state.active_version if state else None,category=data.category,detail=data.detail,payload_json=json.dumps({'content':message.content,'sources':snapshot['sources']},ensure_ascii=False))
    db.add(row);db.commit();return {'id':row.id,'status':row.status}


@router.get('/feedback')
def feedback(context: Authenticated,db: Database,offset: int=Query(0,ge=0)):
    query=select(Feedback)
    if not context.user.is_global_admin:query=query.where(Feedback.owner_id==context.user.id)
    rows=db.scalars(query.order_by(Feedback.created_at.desc(),Feedback.id).offset(offset).limit(51)).all()
    values=[]
    for row in rows[:50]:
        version=db.get(MessageVersion,row.version_id) if row.version_id else None
        message=db.get(Message,row.message_id)
        snapshot=json.loads(row.payload_json or '{}')
        answer=snapshot.get('content')
        if answer is None:answer=json.loads(version.payload_json).get('content','') if version else message.content if message else ''
        values.append({k:getattr(row,k) for k in ['id','message_id','version_id','category','detail','status','created_at']}|{'answer':answer[:20000],'answer_truncated':len(answer)>20000,'sources':snapshot.get('sources',[])})
    return {'items':values,'has_more':len(rows)>50}


@router.patch('/feedback/{feedback_id}')
def feedback_status(feedback_id: UUID,data: FeedbackStatus,context: Authenticated,db: Database):
    if not context.user.is_global_admin:raise HTTPException(403,'운영자만 처리할 수 있습니다.')
    row=db.get(Feedback,str(feedback_id))
    if not row:raise HTTPException(404,'신고를 찾을 수 없습니다.')
    row.status=data.status;db.commit();return {'id':row.id,'status':row.status}


@router.get('/evidence/{evidence_id}')
def app_evidence(evidence_id: UUID,context: Authenticated,db: Database):
    from .evidence import read
    row=read(db,context.user.id,evidence_id)
    return {'id':row.id,'server':row.label,'tool':row.tool,'content':row.content,'sha256':row.sha256,'created_at':row.created_at}
