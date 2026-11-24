"""사용자 작업 조회. 실행기·대기열을 추가하지 않고 기존 임대와 기록을 사용한다."""
import time

from sqlalchemy import func,or_,select

from app.modules.workspaces.models import ToolTrace
from . import scheduler,trace
from .models import Admission,Conversation,GenerationRun,Message

ACTIVE=frozenset({'queued','running','tool_waiting'})


def recover(db,conversation):
    """대화 열기와 작업 센터에서 같은 중단 복구를 적용한다. 호출자가 커밋한다."""
    now=int(time.time())
    if conversation.generation_until>now:return False
    pending=(Message.conversation_id==conversation.id,Message.status=='pending')
    if not conversation.generation_id and not db.scalar(select(Message.id).where(*pending).limit(1)):return False
    scheduler.lock(db);db.refresh(conversation)
    if conversation.generation_until>now:return False
    from app.modules.workflows.conversations import state_for
    for message in db.scalars(select(Message).where(*pending)):
        message.status='error'
        message.error='이전 응답의 연결 또는 서버 작업이 중단되었습니다. 저장된 부분 답변을 확인하고 재시도하세요.'
        trace.mark_interrupted(db,message.id)
        state_for(db,message).error_code='interrupted'
    if conversation.generation_id:
        scheduler.finish(db,conversation.generation_id,'error')
        run=db.get(GenerationRun,conversation.generation_id)
        if run and run.status in ACTIVE:
            run.status,run.error_code,run.finished_at='error','interrupted',now
            run.duration_ms=None
    conversation.generation_id=None;conversation.generation_until=0
    conversation.cancel_requested=False
    return True


def listing(db,user_id,*,view='active',limit=30,offset=0):
    now=int(time.time())
    scope=(GenerationRun.user_id==user_id,Conversation.user_id==user_id)
    base=select(GenerationRun,Conversation).join(Conversation,GenerationRun.conversation_id==Conversation.id).where(*scope)
    # 사용자의 실제 대화 작업만 복구한다. 제목·병렬 하위 호출·다른 사용자는 제외한다.
    stale=db.execute(base.where(GenerationRun.status.in_(ACTIVE),or_(
        Conversation.generation_until<=now,Conversation.generation_id.is_(None),
        Conversation.generation_id!=GenerationRun.id)).limit(100)).all()
    if stale:
        scheduler.lock(db)
        for run,conversation in stale:
            db.refresh(run);db.refresh(conversation)
            if run.status not in ACTIVE:continue
            if conversation.generation_id==run.id:
                recover(db,conversation)
            else:
                scheduler.finish(db,run.id,'error')
                run.status,run.error_code,run.finished_at='error','interrupted',now
                run.duration_ms=None
        db.commit()
    active_count=db.scalar(select(func.count(GenerationRun.id)).join(Conversation,
        GenerationRun.conversation_id==Conversation.id).where(*scope,GenerationRun.status.in_(ACTIVE))) or 0
    if view=='active':base=base.where(GenerationRun.status.in_(ACTIVE))
    elif view=='finished':base=base.where(GenerationRun.status.not_in(ACTIVE))
    rows=db.execute(base.order_by(GenerationRun.started_at.desc(),GenerationRun.id.desc()).offset(offset).limit(limit+1)).all()
    selected=rows[:limit]
    identifiers=[run.id for run,_ in selected]
    latest=select(trace.TraceActivity.request_id,func.max(trace.TraceActivity.sequence).label('sequence')).where(
        trace.TraceActivity.request_id.in_(identifiers)).group_by(trace.TraceActivity.request_id).subquery()
    activities={}
    for meta,tool in db.execute(select(trace.TraceActivity,ToolTrace).join(latest,
            (latest.c.request_id==trace.TraceActivity.request_id)&(latest.c.sequence==trace.TraceActivity.sequence))
            .join(ToolTrace,ToolTrace.id==trace.TraceActivity.trace_id)
            .join(trace.GenerationTrace,(trace.GenerationTrace.message_id==ToolTrace.message_id)&
                (trace.GenerationTrace.request_id==trace.TraceActivity.request_id))
            .where(trace.GenerationTrace.user_id==user_id)):
        activities[meta.request_id]={'name':tool.name,'status':tool.status,'duration_ms':meta.duration_ms}
    admissions={row.request_id:row for row in db.scalars(select(Admission).where(Admission.request_id.in_(identifiers)))}
    items=[]
    for run,conversation in selected:
        admission=admissions.get(run.id)
        position=0
        if run.status=='queued' and admission:
            position=db.scalar(select(func.count(Admission.id)).join(GenerationRun).where(
                Admission.state=='queued',Admission.id<=admission.id,Admission.expires_at>now,
                or_(GenerationRun.connection_id==run.connection_id,GenerationRun.user_id==user_id))) or 0
        elapsed=None if run.error_code=='interrupted' else (
            max(0,(now-run.started_at)*1000) if run.status in ACTIVE else run.duration_ms)
        current=conversation.generation_id==run.id
        items.append({'id':run.id,'conversation_id':conversation.id,'title':trace.safe_text(conversation.title,limit=120),
            'model':trace.safe_text(run.model,limit=200),'is_test':run.is_test,'status':run.status,
            'started_at':run.started_at,'finished_at':None if run.error_code=='interrupted' else run.finished_at,
            'interrupted_detected_at':run.finished_at if run.error_code=='interrupted' else None,'elapsed_ms':elapsed,
            'position':position,'cancel_requested':bool(current and conversation.cancel_requested),
            'can_cancel':bool(current and run.status in ACTIVE and not conversation.cancel_requested),
            'error_code':run.error_code,'activity':activities.get(run.id)})
    return {'items':items,'has_more':len(rows)>limit,'active_count':active_count}
