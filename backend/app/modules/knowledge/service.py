import asyncio
import base64
from collections import Counter
import hashlib
import json
import math
import os
import re
import sys
import time
import uuid

from fastapi import HTTPException
from sqlalchemy import delete,func,or_,select,update
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.modules.auth.models import User
from app.modules.chat.models import Conversation
from .models import Chunk,Document,KnowledgeBase,KnowledgeMember,VectorGarbage
from . import providers

def base_access(db,user_id,base_id,*,write=False):
    row=db.get(KnowledgeBase,str(base_id))
    owner=db.get(User,row.owner_id) if row else None
    if not owner or not owner.is_active or owner.registration_status!='approved':raise HTTPException(404,'접근할 수 있는 지식베이스가 아닙니다.')
    if not row or row.owner_id!=user_id and (write or not db.get(KnowledgeMember,(row.id,user_id))):
        raise HTTPException(404,'접근할 수 있는 지식베이스가 아닙니다.')
    return row

def document_access(db,user_id,document_id,*,conversation_id=None,write=False):
    row=db.get(Document,str(document_id))
    if not row:raise HTTPException(404,'문서를 찾을 수 없습니다.')
    if row.base_id:base_access(db,user_id,row.base_id,write=write)
    elif row.owner_id!=user_id or row.expires_at and row.expires_at<=time.time():
        raise HTTPException(404,'대화 첨부 문서를 찾을 수 없습니다.')
    if not row.base_id and conversation_id and row.conversation_id!=conversation_id:
        from app.modules.workflows.models import AttachmentScope
        grant=db.get(AttachmentScope,(row.id,str(conversation_id)))
        if not grant or grant.user_id!=user_id:raise HTTPException(404,'대화 첨부 문서를 찾을 수 없습니다.')
    return row

def public(row):
    return {k:getattr(row,k) for k in ['id','base_id','conversation_id','filename','sha256','status','error','revision','created_at','chunk_count','expires_at']}|{
        'search_mode':'lexical' if row.index_profile==providers.profile() and get_settings().knowledge_embedding_provider=='disabled' else 'hybrid',
        'needs_reindex':row.status=='ready' and row.index_profile!=providers.profile()}

def remove(db,row):
    if row.index_profile and row.vector_backend=='qdrant':db.add(VectorGarbage(document_id=row.id,owner_id=row.owner_id,index_profile=row.index_profile))
    db.delete(row)

async def parse(raw,filename):
    env={name:os.environ[name] for name in ['PATH','PREFIX','TMPDIR','LD_LIBRARY_PATH','LD_PRELOAD','SYSTEMROOT'] if name in os.environ}
    process=await asyncio.create_subprocess_exec(sys.executable,'-m','app.modules.knowledge.extract',
        env=env,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
    try:
        async with asyncio.timeout(30):
            out,_=await process.communicate(json.dumps({'filename':filename,'data':base64.b64encode(raw).decode()}).encode())
        if len(out)>12*1024*1024:raise ValueError('문서 추출 결과가 너무 큽니다.')
        result=json.loads(out)
        if 'error' in result:raise ValueError(result['error'])
        return result['chunks']
    except (json.JSONDecodeError,TimeoutError):raise ValueError('문서 추출 시간이 초과되었거나 형식을 읽을 수 없습니다.') from None
    finally:
        if process.returncode is None:process.kill();await process.wait()

async def index_one(bind):
    now=int(time.time())
    with Session(bind,expire_on_commit=False) as db:
        expired=list(db.scalars(select(Document).where(Document.expires_at<=now)))
        for row in expired:remove(db,row)
        db.commit()
        row=db.scalar(select(Document).where(or_(Document.status=='queued',(Document.status=='indexing')&(Document.lease_until<=now))).order_by(Document.created_at))
        if not row:return False
        revision=row.revision
        claimed=db.execute(update(Document).where(Document.id==row.id,Document.revision==revision,
            or_(Document.status=='queued',(Document.status=='indexing')&(Document.lease_until<=now))).values(status='indexing',lease_until=now+180,error=None))
        if claimed.rowcount!=1:db.rollback();return True
        db.commit();db.refresh(row);_ = row.raw;db.expunge(row)
    try:
        async with asyncio.timeout(150):
            chunks=await parse(row.raw,row.filename)
            if get_settings().knowledge_vector_store=='qdrant' and row.index_profile and row.vector_backend=='qdrant':
                await providers.purge(row.id,row.owner_id,row.index_profile)
            vectors=[]
            for start in range(0,len(chunks),16):
                vectors.extend(await providers.embed([c['content'] for c in chunks[start:start+16]]))
            row.index_profile=providers.profile()
            for i,c in enumerate(chunks):c['id']=str(uuid.uuid5(uuid.UUID(row.id),f'{revision}:{i}:{row.index_profile}'))
            for start in range(0,len(chunks),32):await providers.upsert(row,chunks[start:start+32],vectors[start:start+32])
            with Session(bind) as db:
                current=db.get(Document,row.id)
                if not current or current.revision!=revision:
                    if get_settings().knowledge_vector_store=='qdrant':db.add(VectorGarbage(document_id=row.id,owner_id=row.owner_id,index_profile=row.index_profile,revision=revision))
                    db.commit();return True
                if current.status!='indexing' or current.lease_until!=now+180:return True
                db.execute(delete(Chunk).where(Chunk.document_id==row.id))
                for i,(c,v) in enumerate(zip(chunks,vectors)):
                    db.add(Chunk(id=c['id'],document_id=row.id,ordinal=i,location=c['location'],content=c['content'],vector_json=json.dumps(v)))
                current.status,current.chunk_count,current.index_profile='ready',len(chunks),row.index_profile
                current.vector_backend=get_settings().knowledge_vector_store
                current.lease_until=0;db.commit()
    except asyncio.CancelledError:raise
    except Exception as error:
        with Session(bind) as db:
            db.execute(update(Document).where(Document.id==row.id,Document.revision==revision,Document.lease_until==now+180,Document.status=='indexing').values(status='error',lease_until=0,
                error=str(error)[:400] if isinstance(error,ValueError) else '색인을 완료하지 못했습니다. 검색 서버와 문서를 확인하세요.'));db.commit()
    return True

async def collect_vectors(bind):
    if not get_settings().knowledge_qdrant_url:return
    with Session(bind,expire_on_commit=False) as db:rows=list(db.scalars(select(VectorGarbage).limit(20)))
    for row in rows:
        try:await providers.purge(row.document_id,row.owner_id,row.index_profile,row.revision)
        except ValueError:continue
        with Session(bind) as db:db.execute(delete(VectorGarbage).where(VectorGarbage.id==row.id));db.commit()

async def worker(bind,stop):
    while not stop.is_set():
        try:
            await collect_vectors(bind)
            if await index_one(bind):continue
        except Exception:pass  # Durable queued/leased rows can be retried; do not log document content/secrets.
        try:await asyncio.wait_for(stop.wait(),1)
        except TimeoutError:pass

def selected_documents(db,user_id,selection,conversation_id):
    if selection.get('_auto_scope'):return automatic_documents(db,user_id,conversation_id)
    bases=list(dict.fromkeys(selection.get('base_ids',[])))
    ids=list(dict.fromkeys(selection.get('document_ids',[])))
    if len(bases)>5 or len(ids)>10:raise HTTPException(422,'지식베이스 5개, 첨부 10개까지 선택할 수 있습니다.')
    for base in bases:base_access(db,user_id,base)
    for doc in ids:document_access(db,user_id,doc,conversation_id=conversation_id)
    query=select(Document).where(or_(Document.base_id.in_(bases),Document.id.in_(ids)))
    documents=list(db.scalars(query)) if bases or ids else []
    if len(documents)>200:raise HTTPException(422,'검색 범위가 큽니다. 지식베이스를 나눠 선택하세요.')
    if any(d.status!='ready' for d in documents):raise HTTPException(409,'선택한 자료의 색인이 완료되지 않았습니다. 지식 관리에서 상태를 확인하세요.')
    if any(d.index_profile!=providers.profile() for d in documents):raise HTTPException(409,'검색 모델 설정이 변경되었습니다. 자료를 다시 색인하세요.')
    return documents

def terms(text):
    words=re.findall(r'[\w]+',text.casefold())[:2000]
    # Korean particles vary; character bigrams supplement exact word matching.
    return words+[w[i:i+2] for w in words if re.search('[가-힣]',w) for i in range(len(w)-1)]

def lexical_ranks(chunks,query):
    query_terms=set(terms(query));tokens=[Counter(terms(c.content)) for c in chunks]
    avg=sum(sum(t.values()) for t in tokens)/len(tokens)
    frequencies={t:sum(t in counts for counts in tokens) for t in query_terms}
    scores=[]
    for i,count in enumerate(tokens):
        size=sum(count.values());score=0
        for term in query_terms:
            n=count[term]
            if n:score+=math.log(1+(len(chunks)-frequencies[term]+.5)/(frequencies[term]+.5))*n*2.2/(n+1.2*(.25+.75*size/max(1,avg)))
        if score>0:scores.append((i,score))
    return [chunks[i].id for i,_ in sorted(scores,key=lambda s:s[1],reverse=True)[:30]]

def dense_ranks(chunks,vector):
    values=[]
    for c in chunks:
        v=json.loads(c.vector_json)
        if len(v)!=len(vector):raise HTTPException(409,'임베딩 차원이 변경되었습니다. 자료를 다시 색인하세요.')
        values.append((c.id,sum(a*b for a,b in zip(v,vector))))
    return [i for i,_ in sorted(values,key=lambda p:p[1],reverse=True)[:30]]

async def search(bind,user_id,selection,conversation_id,query,limit=8):
    if not get_settings().knowledge_enabled:raise HTTPException(503,'지식 검색이 비활성화되어 있습니다.')
    if not isinstance(query,str) or not query.strip() or len(query)>2000:raise HTTPException(422,'검색어를 확인하세요.')
    with Session(bind,expire_on_commit=False) as db:
        docs=selected_documents(db,user_id,selection,conversation_id)
        documents={d.id:d for d in docs}
        chunks=list(db.scalars(select(Chunk).where(Chunk.document_id.in_(documents)).limit(15001))) if documents else []
    if len(chunks)>15000:raise HTTPException(422,'검색 범위가 큽니다. 자료를 나눠 선택하세요.')
    if not chunks:return {'matches':[],'mode':'lexical','notice':'검색할 자료가 없습니다.'}
    lexical=await asyncio.to_thread(lexical_ranks,chunks,query)
    dense=[];s=get_settings()
    if s.knowledge_embedding_provider!='disabled':
        vector=(await providers.embed([query]))[0]
        if s.knowledge_vector_store=='qdrant':dense=await providers.nearest(vector,list(documents),providers.profile())
        else:
            dense=await asyncio.to_thread(dense_ranks,chunks,vector)
    fused={}
    for ranked in [lexical,dense]:
        for rank,cid in enumerate(ranked):fused[cid]=fused.get(cid,0)+1/(60+rank+1)
    by_id={c.id:c for c in chunks};candidates=[]
    for cid in sorted(fused,key=fused.get,reverse=True)[:30]:
        if cid not in by_id:continue  # Always intersect vector results with current SQL ACL/revision.
        c=by_id[cid];d=documents[c.document_id]
        candidates.append({'chunk_id':c.id,'document_id':d.id,'filename':d.filename,'location':c.location,
            'content':c.content,'sha256':d.sha256,'revision':d.revision})
    candidates=await providers.rerank(query,candidates)
    # Membership/deletion may change while external embedding/reranking is in flight.
    with Session(bind) as db:
        current={d.id:(d.revision,d.sha256) for d in selected_documents(db,user_id,selection,conversation_id)}
    candidates=[c for c in candidates if current.get(c['document_id'])==(c['revision'],c['sha256'])]
    return {'matches':candidates[:max(1,min(10,limit))],'mode':'hybrid' if dense else 'lexical','reranked':bool(s.knowledge_rerank_url)}

def automatic_query(user_id,conversation_id):
    shared=select(KnowledgeMember.base_id).where(KnowledgeMember.user_id==user_id)
    bases=select(KnowledgeBase.id).join(User,User.id==KnowledgeBase.owner_id).where(
        User.is_active.is_(True),User.registration_status=='approved',
        or_(KnowledgeBase.owner_id==user_id,KnowledgeBase.id.in_(shared)))
    from app.modules.workflows.models import Project,ProjectConversation,AttachmentScope
    project_base=select(Project.base_id).join(ProjectConversation,Project.id==ProjectConversation.project_id).where(ProjectConversation.conversation_id==conversation_id,Project.user_id==user_id)
    project_exists=select(ProjectConversation.conversation_id).where(ProjectConversation.conversation_id==conversation_id).exists()
    project_bases=select(Project.base_id).where(Project.user_id==user_id,Project.base_id.is_not(None))
    bases=bases.where(or_((~project_exists)&(~KnowledgeBase.id.in_(project_bases)),KnowledgeBase.id.in_(project_base)))
    grants=select(AttachmentScope.document_id).where(AttachmentScope.user_id==user_id,AttachmentScope.conversation_id==conversation_id)
    return select(Document).where(Document.status=='ready',Document.index_profile==providers.profile(),or_(
        Document.base_id.in_(bases),
        (Document.base_id.is_(None))&(Document.owner_id==user_id)&(or_(Document.conversation_id==conversation_id,Document.id.in_(grants)))&(Document.expires_at>int(time.time()))))

def automatic_available(db,user_id,conversation_id):
    if not get_settings().knowledge_enabled:return False
    return db.scalar(automatic_query(user_id,conversation_id).with_only_columns(Document.id).limit(1)) is not None

def automatic_documents(db,user_id,conversation_id):
    user=db.get(User,user_id)
    if not user or not user.is_active or user.registration_status!='approved':raise HTTPException(403,'자료 검색 권한을 확인하세요.')
    documents=list(db.scalars(automatic_query(user_id,conversation_id).order_by(Document.id).limit(201)))
    if len(documents)>200:raise HTTPException(422,'자동 검색 범위가 큽니다. 사용할 지식베이스를 직접 선택하세요.')
    return documents

def verify_sources(bind,user_id,conversation_id,sources):
    with Session(bind) as db:
        user=db.get(User,user_id)
        if not user or not user.is_active or user.registration_status!='approved':raise HTTPException(403,'자료 검색 권한이 변경되었습니다.')
        for source in sources:
            if source.get('app')!='knowledge':continue
            doc=document_access(db,user_id,source['document_id'],conversation_id=conversation_id)
            chunk=db.get(Chunk,source['chunk_id'])
            if (not chunk or chunk.document_id!=doc.id or doc.status!='ready' or doc.index_profile!=providers.profile()
                    or doc.revision!=source['revision'] or doc.sha256!=source['sha256']):
                raise HTTPException(409,'답변 중 자료 또는 색인이 변경되었습니다. 다시 질문하세요.')
