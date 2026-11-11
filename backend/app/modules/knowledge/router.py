import hashlib
from pathlib import Path
import time
from typing import Annotated
from uuid import UUID
from fastapi import APIRouter,Depends,File,Form,HTTPException,Query,UploadFile
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import func,or_,select,delete
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.db.session import get_db
from app.modules.auth.dependencies import Authenticated
from app.modules.auth.models import User
from app.modules.chat.models import Conversation
from app.modules.chat.scheduler import lock
from . import service,providers
from .extract import SUPPORTED
from . import ocr
from .models import Chunk,Document,KnowledgeBase,KnowledgeMember
from app.modules.workflows.models import UploadAttempt,AttachmentScope,Project
from app.modules.workflows.projects import available_projects

router=APIRouter(prefix='/knowledge',tags=['knowledge'])
Database=Annotated[Session,Depends(get_db)]

class NameInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    name: str=Field(min_length=1,max_length=120)
class ShareInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    username: str=Field(min_length=1,max_length=80)
class Selection(BaseModel):
    model_config=ConfigDict(extra='forbid')
    base_ids: list[UUID]=Field(default_factory=list,max_length=5)
    document_ids: list[UUID]=Field(default_factory=list,max_length=10)
class SearchInput(Selection):
    conversation_id: UUID
    query: str=Field(min_length=1,max_length=2000)

def supported_formats():
    if get_settings().knowledge_ocr_enabled and ocr.installed():
        return SUPPORTED | ocr.IMAGE_FORMATS
    return SUPPORTED


@router.get('/config')
def config(context: Authenticated):
    s=get_settings()
    return {'enabled':s.knowledge_enabled,'formats':sorted(supported_formats()),'max_upload_bytes':s.knowledge_max_upload_bytes,
        'search_mode':'lexical' if s.knowledge_embedding_provider=='disabled' else 'hybrid','vector_store':s.knowledge_vector_store,
        'reranking':bool(s.knowledge_rerank_url)}

@router.get('/bases')
def bases(context: Authenticated,db: Database):
    user_id = context.user.id
    project_bases = available_projects(user_id).with_only_columns(Project.base_id)
    rows=db.scalars(select(KnowledgeBase).join(User, User.id == KnowledgeBase.owner_id)
        .where(User.is_active.is_(True), User.registration_status == 'approved',
            or_(KnowledgeBase.owner_id == user_id, KnowledgeBase.id.in_(project_bases),
                KnowledgeBase.id.in_(select(KnowledgeMember.base_id).where(KnowledgeMember.user_id == user_id))))
        .order_by(KnowledgeBase.created_at))
    return {'items':[{'id':r.id,'name':r.name,'owned':r.owner_id==context.user.id} for r in rows]}

@router.post('/bases',status_code=201)
def create(data: NameInput,context: Authenticated,db: Database):
    lock(db)
    if db.scalar(select(func.count()).select_from(KnowledgeBase).where(KnowledgeBase.owner_id==context.user.id))>=20:
        raise HTTPException(422,'지식베이스는 최대 20개입니다.')
    row=KnowledgeBase(owner_id=context.user.id,name=data.name);db.add(row);db.commit();db.refresh(row)
    return {'id':row.id,'name':row.name,'owned':True}

@router.delete('/bases/{base_id}')
def remove_base(base_id: UUID,context: Authenticated,db: Database):
    lock(db);row=service.base_access(db,context.user.id,base_id,manage=True)
    for doc in db.scalars(select(Document).where(Document.base_id==row.id)):service.remove(db,doc)
    db.delete(row);db.commit();return {'deleted':True}

@router.get('/bases/{base_id}/members')
def members(base_id: UUID,context: Authenticated,db: Database):
    row=service.base_access(db,context.user.id,base_id,manage=True)
    users=db.scalars(select(User).join(KnowledgeMember,KnowledgeMember.user_id==User.id).where(KnowledgeMember.base_id==row.id))
    return {'items':[{'id':u.id,'username':u.username} for u in users]}

@router.post('/bases/{base_id}/members')
def share(base_id: UUID,data: ShareInput,context: Authenticated,db: Database):
    row=service.base_access(db,context.user.id,base_id,manage=True)
    user=db.scalar(select(User).where(User.username==data.username.strip().lower(),User.is_active.is_(True)))
    if not user:raise HTTPException(404,'사용자를 찾을 수 없습니다.')
    if user.id!=row.owner_id and not db.get(KnowledgeMember,(row.id,user.id)):
        db.add(KnowledgeMember(base_id=row.id,user_id=user.id));db.commit()
    return {'shared':True}

@router.delete('/bases/{base_id}/members/{user_id}')
def unshare(base_id: UUID,user_id: UUID,context: Authenticated,db: Database):
    row=service.base_access(db,context.user.id,base_id,manage=True)
    db.execute(delete(KnowledgeMember).where(KnowledgeMember.base_id==row.id,KnowledgeMember.user_id==str(user_id)));db.commit()
    return {'revoked':True}

@router.get('/documents')
def documents(context: Authenticated,db: Database,base_id: UUID|None=None,conversation_id: UUID|None=None):
    if base_id:
        service.base_access(db,context.user.id,base_id)
        query=select(Document).where(Document.base_id==str(base_id))
    elif conversation_id:
        conversation=db.get(Conversation,str(conversation_id))
        if not conversation or conversation.user_id!=context.user.id:raise HTTPException(404,'대화를 찾을 수 없습니다.')
        grants=select(AttachmentScope.document_id).where(AttachmentScope.conversation_id==str(conversation_id),AttachmentScope.user_id==context.user.id)
        query=select(Document).where(or_(Document.conversation_id==str(conversation_id),Document.id.in_(grants)),Document.owner_id==context.user.id,
            Document.expires_at>int(time.time()))
    else:raise HTTPException(422,'지식베이스 또는 대화를 지정하세요.')
    return {'items':[service.public(d) for d in db.scalars(query.order_by(Document.created_at.desc()).limit(200))]}

@router.post('/documents',status_code=201)
async def upload(context: Authenticated,db: Database,file: UploadFile=File(),base_id: str|None=Form(None),conversation_id: str|None=Form(None),upload_id: str|None=Form(None)):
    s=get_settings()
    if not s.knowledge_enabled:raise HTTPException(503,'지식 기능이 비활성화되어 있습니다.')
    if bool(base_id)==bool(conversation_id):raise HTTPException(422,'지식베이스 또는 대화 중 하나를 지정하세요.')
    if base_id:service.base_access(db,context.user.id,base_id,write=True)
    else:
        row=db.get(Conversation,conversation_id)
        if not row or row.user_id!=context.user.id:raise HTTPException(404,'대화를 찾을 수 없습니다.')
    filename=Path((file.filename or 'file').replace('\\','/')).name[:240]
    if Path(filename).suffix.lower() not in supported_formats():raise HTTPException(422,'지원하지 않는 파일 형식입니다.')
    raw=bytearray()
    while block:=await file.read(65536):
        raw.extend(block)
        if len(raw)>s.knowledge_max_upload_bytes:raise HTTPException(413,'업로드 크기 제한을 초과했습니다.')
    await file.close()
    if not raw:raise HTTPException(422,'빈 파일입니다.')
    lock(db)
    if base_id:service.base_access(db,context.user.id,base_id,write=True)
    attempt=db.get(UploadAttempt,upload_id) if upload_id else None
    if upload_id and (not attempt or attempt.owner_id!=context.user.id or attempt.status!='pending' or attempt.expires_at<time.time() or attempt.base_id!=base_id or attempt.conversation_id!=conversation_id or Path(attempt.filename.replace('\\','/')).name[:240]!=filename):
        raise HTTPException(409,'취소되었거나 만료된 첨부 요청입니다.')
    digest=hashlib.sha256(raw).hexdigest()
    existing=db.scalar(select(Document).where(Document.owner_id==context.user.id,Document.base_id==base_id,
        Document.conversation_id==conversation_id,Document.filename==filename,
        or_(Document.expires_at.is_(None),Document.expires_at>int(time.time()))))
    if existing and existing.sha256==digest:
        if attempt:attempt.status='complete';attempt.document_id=existing.id;db.commit()
        return service.public(existing)
    if existing and existing.status=='indexing' and existing.lease_until>time.time():raise HTTPException(409,'기존 문서의 색인이 완료된 뒤 교체하세요.')
    count,size=db.execute(select(func.count(Document.id),func.coalesce(func.sum(func.length(Document.raw)),0)).where(Document.owner_id==context.user.id)).one()
    if (not existing and count>=s.knowledge_max_documents) or size+len(raw)-(len(existing.raw) if existing else 0)>s.knowledge_max_user_bytes:raise HTTPException(422,'개인 자료 저장 한도에 도달했습니다.')
    # Recheck scope after reading an upload; sharing/deletion can change meanwhile.
    if conversation_id:
        scope=db.get(Conversation,conversation_id)
        if not scope or scope.user_id!=context.user.id:raise HTTPException(404,'대화를 찾을 수 없습니다.')
    if attempt and existing:
        raise HTTPException(409,'같은 이름의 다른 파일이 있습니다. 이름을 변경하거나 기존 문서를 삭제한 뒤 첨부하세요.')
    if existing:
        row=existing;row.raw=bytes(raw);row.sha256=digest;row.revision+=1;row.status='queued';row.error=None
    else:
        row=Document(owner_id=context.user.id,base_id=base_id,conversation_id=conversation_id,filename=filename,
            raw=bytes(raw),sha256=digest,expires_at=int(time.time())+86400 if conversation_id else None)
    db.add(row);db.flush()
    if attempt:attempt.document_id=row.id;attempt.status='complete';attempt.created_document=existing is None
    db.commit();db.refresh(row)
    return service.public(row)

@router.post('/documents/{document_id}/reindex')
def reindex(document_id: UUID,context: Authenticated,db: Database):
    lock(db);row=service.document_access(db,context.user.id,document_id,write=True)
    if row.status=='indexing' and row.lease_until>time.time():raise HTTPException(409,'색인이 진행 중입니다.')
    row.revision+=1;row.status='queued';row.error=None;db.commit()
    return service.public(row)

@router.delete('/documents/{document_id}')
def remove_document(document_id: UUID,context: Authenticated,db: Database):
    lock(db);row=service.document_access(db,context.user.id,document_id,write=True);service.remove(db,row);db.commit()
    return {'deleted':True}

@router.get('/documents/{document_id}/chunks')
def chunks(document_id: UUID,context: Authenticated,db: Database,offset: int=Query(0,ge=0)):
    row=service.document_access(db,context.user.id,document_id)
    if row.status!='ready':raise HTTPException(409,'색인이 준비되지 않았습니다.')
    rows=db.scalars(select(Chunk).where(Chunk.document_id==row.id).order_by(Chunk.ordinal).offset(offset).limit(10))
    return {'document':service.public(row),'items':[{'id':c.id,'location':c.location,'content':c.content} for c in rows]}

@router.get('/documents/{document_id}/chunks/{chunk_id}')
def chunk(document_id: UUID,chunk_id: UUID,context: Authenticated,db: Database):
    row=service.document_access(db,context.user.id,document_id)
    if row.status!='ready':raise HTTPException(409,'문서 색인이 준비되지 않았습니다.')
    item=db.get(Chunk,str(chunk_id))
    if not item or item.document_id!=row.id:raise HTTPException(404,'이 문서 버전의 출처를 찾을 수 없습니다.')
    return {'id':item.id,'content':item.content,'location':item.location,'revision':row.revision,'sha256':row.sha256}

@router.post('/search')
async def search(data: SearchInput,context: Authenticated,db: Database):
    conversation=db.get(Conversation,str(data.conversation_id))
    if not conversation or conversation.user_id!=context.user.id:raise HTTPException(404,'대화를 찾을 수 없습니다.')
    bind,user_id=db.get_bind(),context.user.id;db.rollback()
    try:return await service.search(bind,user_id,data.model_dump(mode='json'),str(data.conversation_id),data.query)
    except ValueError as error:raise HTTPException(502,str(error)) from None
