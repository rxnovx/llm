"""User-owned branches, response versions and project context."""
import json
import time
from uuid import uuid4
from fastapi import HTTPException
from sqlalchemy import delete,func,or_,select
from sqlalchemy.orm import object_session
from app.modules.chat.models import Conversation,Message,ContextNote
from app.modules.chat.scheduler import lock
from app.modules.assistant.models import TurnContext,TurnDecision
from app.modules.integrations.models import MessageContext
from app.modules.workspaces.models import ToolTrace
from app.modules.knowledge import service as knowledge
from app.modules.knowledge.models import Document
from .models import (Project,ProjectConversation,ConversationState,MessageVersion,MessageState,
    AttachmentScope,Artifact,ArtifactScope,AutoSummary,ToolEvidence)
from .projects import project_access, project_role


def owned(db,user_id,conversation_id,*,idle=False):
    row=db.get(Conversation,str(conversation_id))
    if not row or row.user_id!=user_id:raise HTTPException(404,'대화를 찾을 수 없습니다.')
    if idle and row.generation_id and row.generation_until>time.time():raise HTTPException(409,'진행 중인 응답을 중지한 뒤 변경하세요.')
    return row


def message_owned(db,user_id,message_id,*,idle=False):
    row=db.get(Message,str(message_id))
    if not row:raise HTTPException(404,'메시지를 찾을 수 없습니다.')
    return row,owned(db,user_id,row.conversation_id,idle=idle)


def project_owned(db,user_id,project_id):
    row=db.get(Project,str(project_id))
    if not row or row.user_id!=user_id:raise HTTPException(404,'프로젝트를 찾을 수 없습니다.')
    return row


def project_for(db,conversation_id):
    link=db.get(ProjectConversation,str(conversation_id))
    return db.get(Project,link.project_id) if link else None


def attach_project(db,user_id,conversation_id,project_id):
    owned(db,user_id,conversation_id,idle=True)
    link=db.get(ProjectConversation,str(conversation_id))
    if project_id:
        project, _ = project_access(db, user_id, project_id, write=True)
        if link:link.project_id=project.id
        else:db.add(ProjectConversation(conversation_id=str(conversation_id),project_id=project.id))
    elif link:db.delete(link)


def conversation_extra(item):
    db=object_session(item)
    if not db:return {}
    project=project_for(db,item.id);state=db.get(ConversationState,item.id)
    return {'project_id':project.id if project else None,'project_name':project.name if project else None,
        'parent_id':state.parent_id if state else None,'auto_summary':state.auto_summary if state else True}


def artifact_public(item):
    return {k:getattr(item,k) for k in ['id','name','format','size','sha256','created_at']}


def message_extra(item,run_id=None):
    state=item.workflow_state
    active=state.active_version if state else None
    ids=set(json.loads(state.artifact_ids_json or '[]')) if state else set()
    artifacts=list(item.generated_files)+[link.artifact for link in item.file_links if link.artifact]
    if run_id:artifacts=[a for a in artifacts if a.run_id==run_id]
    elif item.status=='complete':artifacts=[a for a in artifacts if a.id in ids]
    else:artifacts=[a for a in artifacts if state and a.run_id==state.last_run]
    artifacts=list({a.id:a for a in artifacts if a.status=='ready'}.values())
    options=json.loads(item.assistant_context.data_json) if item.assistant_context else {}
    apps=json.loads(item.source_record.apps_json) if item.source_record else []
    writes=bool(options.get('allow_writes') or any(a.get('mode')=='execute' for a in apps))
    recovery=None
    if item.role=='assistant' and (item.status in {'error','cancelled'} or state and state.error_code):
        code=state.error_code if state else item.status
        recovery={'code':code or item.status,'phase':state.phase if state else '',
            'preserved_previous':item.status=='complete','requires_confirmation':writes,'can_retry':True,'can_resume_execution':False,
            'actions':['retry','select_model','check_apps' if apps or options.get('mcp_ids') else 'check_documents']}
    return {'versions':[{'id':v.id,'sequence':v.sequence,'model':v.model,'created_at':v.created_at} for v in item.versions],
        'active_version':active,'artifacts':[artifact_public(a) for a in artifacts],
        'recovery':recovery,'feedback_count':len(item.answer_feedback)}


def payload(item,run_id=None):
    record=item.source_record
    traces = item.tool_traces[-80:]
    if len(item.tool_traces) > len(traces):
        pinned = [trace for trace in item.tool_traces[:-80]
                  if trace.name.startswith('gitlab_mr_') or trace.name == 'gitlab_publication'][-12:]
        traces = pinned + traces[len(pinned):]
    return {k:getattr(item,k) for k in ['id','role','content','status','model','is_test','error','created_at']}|{
        'apps':json.loads(record.apps_json) if record else [],'sources':json.loads(record.sources_json) if record else [],
        'assistant':json.loads(item.assistant_context.data_json) if item.assistant_context else {},
        'routing':json.loads(item.decision_record.data_json) if item.decision_record else {},
        'tools':[{'name':t.name,'step':t.step,'status':t.status,'detail':t.detail} for t in traces],
        **message_extra(item,run_id)}


def shared_payload(item, base_documents):
    record = item.source_record
    sources = json.loads(record.sources_json) if record else []
    return {key: getattr(item, key) for key in ('id', 'role', 'content', 'status', 'model', 'is_test', 'created_at')} | {
        'error': None,
        'sources': [source for source in sources if source.get('app') == 'knowledge' and source.get('document_id') in base_documents],
    }


def state_for(db,message):
    if not message.workflow_state:message.workflow_state=MessageState()
    return message.workflow_state


def capture(db,message,version_id=None):
    if message.role!='assistant' or message.status!='complete':return None
    state=state_for(db,message)
    if version_id and db.get(MessageVersion,str(version_id)):return db.get(MessageVersion,str(version_id))
    data=payload(message,version_id)
    version=MessageVersion(id=str(version_id or uuid4()),message_id=message.id,sequence=max([v.sequence for v in message.versions] or [0])+1,
        model=message.model,payload_json=json.dumps(data,ensure_ascii=False))
    message.versions.append(version);state.active_version=version.id
    state.artifact_ids_json=json.dumps([a['id'] for a in data['artifacts']]);db.flush()
    return version


def before_retry(db,message):
    if len(message.versions)>=20:raise HTTPException(422,'이 답변의 버전이 20개입니다. 대화를 분기해 이어가세요.')
    if message.status=='complete' and not message.versions:capture(db,message)


def apply_payload(db,message,data,*,branch=False):
    for key in ['content','status','model','is_test','error']:setattr(message,key,data[key])
    apps=[dict(a) for a in data.get('apps',[])]
    if branch:
        for app in apps:
            app.pop('workspace_id',None)
            if app.get('mode')=='execute':app['mode']='workspace'
    if not message.source_record:message.source_record=MessageContext()
    message.source_record.apps_json=json.dumps(apps)
    message.source_record.sources_json=json.dumps(data.get('sources',[]),ensure_ascii=False)
    options=dict(data.get('assistant',{}))
    if branch:options['allow_writes']=False
    if not message.assistant_context:message.assistant_context=TurnContext()
    message.assistant_context.data_json=json.dumps(options)
    if not message.decision_record:message.decision_record=TurnDecision()
    message.decision_record.data_json=json.dumps(data.get('routing',{}),ensure_ascii=False)
    message.tool_traces=[ToolTrace(name=t['name'],status=t['status'],step=t['step'],detail=t['detail']) for t in data.get('tools',[])[:80]]
    state=state_for(db,message);state.artifact_ids_json=json.dumps([a['id'] for a in data.get('artifacts',[])])
    return options


def grant_attachments(db,user_id,parent_id,child_id,ids):
    available=set()
    for document_id in ids:
        try:doc=knowledge.document_access(db,user_id,document_id,conversation_id=parent_id)
        except HTTPException:continue
        if not doc.base_id and doc.owner_id==user_id:
            if not db.get(AttachmentScope,(doc.id,child_id)):db.add(AttachmentScope(document_id=doc.id,conversation_id=child_id,user_id=user_id))
            available.add(doc.id)
        elif doc.base_id:available.add(doc.id)
    return available


def fork(db,user_id,conversation_id,message_id,*,content=None,version=None,tail_only=False):
    from . import image_inputs

    lock(db);parent=owned(db,user_id,conversation_id,idle=True)
    target=db.get(Message,str(message_id))
    if not target or target.conversation_id!=parent.id:raise HTTPException(404,'분기할 메시지를 찾을 수 없습니다.')
    if content is not None and (target.role!='user' or not content.strip() or len(content)>16000):raise HTTPException(422,'수정할 질문을 확인하세요.')
    end=target.position-(1 if content is not None else 0)
    rows=list(db.scalars(select(Message).where(Message.conversation_id==parent.id,Message.position<=end).order_by(Message.position)))
    if any(m.status=='pending' for m in rows):raise HTTPException(409,'진행 중인 메시지는 분기할 수 없습니다.')
    if tail_only:
        omitted=rows[:-12];rows=rows[-12:]
    else:omitted=[]
    child=Conversation(user_id=user_id,connection_id=parent.connection_id,model=parent.model,title=(parent.title[:105]+' · 이어가기' if tail_only else parent.title[:105]+' · 분기'))
    db.add(child);db.flush();db.add(ConversationState(conversation_id=child.id,parent_id=parent.id,parent_message_id=target.id))
    project=project_for(db,parent.id)
    if project_role(db, project, user_id) in {'owner', 'editor'}:
        db.add(ProjectConversation(conversation_id=child.id,project_id=project.id))
    all_documents=set();target_options={};target_apps=[]
    for position,original in enumerate(rows):
        data=json.loads(version.payload_json) if version and original.id==target.id else payload(original)
        copied=Message(conversation_id=child.id,position=position,role=original.role,content=data['content'],status=data['status'],
            model=data['model'],is_test=data['is_test'],error=data['error'])
        db.add(copied);db.flush()
        for source in data.get('sources',[]):
            if source.get('app')!='mcp':continue
            evidence=db.get(ToolEvidence,source['evidence_id'])
            if evidence and evidence.owner_id==user_id:
                cloned=ToolEvidence(owner_id=user_id,message_id=copied.id,run_id=evidence.run_id,server_id=evidence.server_id,fingerprint=evidence.fingerprint,label=evidence.label,tool=evidence.tool,content=evidence.content,sha256=evidence.sha256)
                db.add(cloned);db.flush();source['evidence_id']=cloned.id
        opts=apply_payload(db,copied,data,branch=True)
        all_documents.update(opts.get('document_ids',[]));all_documents.update(image_inputs.attachment_ids(opts))
        all_documents.update(s['document_id'] for s in data.get('sources',[]) if s.get('app')=='knowledge')
        for artifact in data.get('artifacts',[]):
            row=db.get(Artifact,artifact['id'])
            if row and row.owner_id==user_id:db.add(ArtifactScope(artifact_id=row.id,message_id=copied.id))
        if copied.role=='assistant' and copied.status=='complete':
            db.flush();db.expire(copied,['file_links']);capture(db,copied)
        target_options=opts;target_apps=json.loads(copied.source_record.apps_json)
    # The edited question inherits its own original app/document choices, not unrelated later turns.
    if content is not None:
        answer=db.scalar(select(Message).where(Message.conversation_id==parent.id,Message.position==target.position+1,Message.role=='assistant'))
        if answer:
            data=payload(answer);target_options=dict(data.get('assistant',{}));target_options['allow_writes']=False
            target_apps=[dict(a) for a in data.get('apps',[])]
            for app in target_apps:
                app.pop('workspace_id',None)
                if app.get('mode')=='execute':app['mode']='workspace'
        all_documents.update(target_options.get('document_ids',[]));all_documents.update(image_inputs.attachment_ids(target_options))
    allowed=grant_attachments(db,user_id,parent.id,child.id,all_documents)
    target_options['document_ids']=[i for i in target_options.get('document_ids',[]) if i in allowed]
    if 'image_document_ids' in target_options:
        target_options['image_document_ids']=[i for i in target_options['image_document_ids'] if i in allowed]
    if not tail_only:
        for model in [ContextNote,AutoSummary]:
            note=db.get(model,parent.id)
            if note and note.summary and note.through_position<=end and (not version or note.through_position<target.position):
                db.add(model(conversation_id=child.id,summary=note.summary,through_position=note.through_position,revision=1))
    # Auto summaries are scoped and copied as historical data, never as current tool permission.
    if tail_only:
        previous=db.get(AutoSummary,parent.id);note=db.get(ContextNote,parent.id)
        texts=[n.summary for n in [note,previous] if n and n.summary]
        if omitted:
            texts.append('이전 대화 발췌 (길이 제한으로 일부 생략):\n'+'\n'.join(m.role+': '+m.content[:350] for m in omitted[-16:]))
        if texts:db.add(AutoSummary(conversation_id=child.id,summary='\n\n'.join(t[:2500] for t in texts)[:6000],through_position=-1))
    db.flush()
    return child,{'draft':content or '','assistant':target_options,'apps':target_apps,'excluded_attachments':len(all_documents-allowed)}


def preserve_shared_files(db,conversation_id):
    """Deleting a parent chat does not destroy a surviving branch's attachment/file."""
    for doc in db.scalars(select(Document).where(Document.conversation_id==conversation_id)):
        scope=db.scalar(select(AttachmentScope).where(AttachmentScope.document_id==doc.id,AttachmentScope.conversation_id!=conversation_id))
        if scope:doc.conversation_id=scope.conversation_id
        else:knowledge.remove(db,doc)
    message_ids=select(Message.id).where(Message.conversation_id==conversation_id)
    linked_ids = select(ArtifactScope.artifact_id).where(ArtifactScope.message_id.in_(message_ids))
    candidates = select(Artifact).where(or_(Artifact.message_id.in_(message_ids),
        Artifact.message_id.is_(None) & Artifact.id.in_(linked_ids)))
    for artifact in db.scalars(candidates):
        other=db.scalar(select(ArtifactScope).join(Message,Message.id==ArtifactScope.message_id).where(ArtifactScope.artifact_id==artifact.id,Message.conversation_id!=conversation_id))
        if not other:db.delete(artifact)
    db.flush()


def search(db,user_id,query='',project_id=None,limit=50,offset=0):
    statement = select(Conversation)
    if project_id:
        project_access(db, user_id, project_id)
        statement=statement.join(ProjectConversation,ProjectConversation.conversation_id==Conversation.id).where(ProjectConversation.project_id==str(project_id))
    else:
        statement = statement.where(Conversation.user_id == user_id)
    pattern='%'+query.replace('\\','\\\\').replace('%','\\%').replace('_','\\_')+'%'
    if query:
        visible = or_(Conversation.user_id == user_id, Message.status == 'complete')
        matches=select(Message.id).where(Message.conversation_id==Conversation.id,visible,Message.content.ilike(pattern,escape='\\')).exists()
        statement=statement.where(or_(Conversation.title.ilike(pattern,escape='\\'),matches))
    rows=list(db.scalars(statement.order_by(Conversation.updated_at.desc(),Conversation.id.desc()).offset(offset).limit(limit+1)))
    result=[]
    from app.modules.chat.router import conversation_data
    for row in rows[:limit]:
        shared = row.user_id != user_id
        data=conversation_data(row, shared=shared)
        if query:
            matching = select(Message).where(Message.conversation_id==row.id,Message.content.ilike(pattern,escape='\\'))
            if shared:
                matching = matching.where(Message.status == 'complete')
            hit=db.scalar(matching.order_by(Message.position.desc()).limit(1))
            if hit:
                at=hit.content.casefold().find(query.casefold());start=max(0,at-60)
                data['match']={'message_id':hit.id,'role':hit.role,'excerpt':hit.content[start:start+220]}
        result.append(data)
    return {'items':result,'has_more':len(rows)>limit}
