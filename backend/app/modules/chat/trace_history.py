"""답변 버전과 대화 분기에도 해당 답변을 만든 실행 기록을 보존한다."""
import json
import time
from uuid import uuid4

from .trace import GenerationTrace, TraceActivity, safe_text, snapshot_for_message


def public_trace(data):
    if not isinstance(data,dict):return None
    return {**data,'reasoning':safe_text(data.get('reasoning',''),limit=64_000),
        'steps':[{**item,'detail':safe_text(item.get('detail',''))} for item in data.get('steps',[])[:240]]}


def public_message(data):
    return {**data,'tools':[{**item,'detail':safe_text(item.get('detail',''))} for item in data.get('tools',[])[:240]],
        'generation_trace':public_trace(data.get('generation_trace'))}


def payload(db, message, run_id=None):
    state = message.workflow_state
    if not run_id and message.status == 'complete' and state and state.active_version:
        version = next((item for item in message.versions if item.id == state.active_version), None)
        if version:
            saved = json.loads(version.payload_json)
            if 'generation_trace' in saved:
                return public_trace(saved['generation_trace'])
    return public_trace(snapshot_for_message(db, message.id)) if db else None


def restore(db, message, data):
    if not data or message.role != 'assistant':
        row = db.get(GenerationTrace, message.id)
        if row:
            db.delete(row)
        return
    # 메시지의 작업 기록은 호출자가 이미 복제했다. 이전 ID를 재사용하면
    # 원본 대화의 실행 기록을 변경할 수 있으므로 보조 메타데이터도 새 ID에 연결한다.
    db.flush()
    steps = data.get('steps', [])[:240]
    timestamps = [step.get('started_at_ms') for step in steps if type(step.get('started_at_ms')) is int]
    now = int(time.time() * 1000)
    row = db.get(GenerationTrace, message.id)
    if not row:
        from .models import Conversation
        conversation = db.get(Conversation, message.conversation_id)
        row = GenerationTrace(message_id=message.id, user_id=conversation.user_id,
            request_id=str(data.get('request_id') or uuid4()), started_at_ms=min(timestamps, default=now))
        db.add(row)
    row.status = data.get('status', 'complete')
    row.reasoning = data.get('reasoning', '')[:64_000]
    row.truncated = bool(data.get('reasoning_truncated'))
    row.metrics_json = json.dumps(data.get('metrics', {}), ensure_ascii=False)
    row.updated_at_ms = now
    for sequence, (tool, step) in enumerate(zip(message.tool_traces, steps)):
        previous = db.get(TraceActivity, tool.id)
        if previous:
            db.delete(previous)
            db.flush()
        db.add(TraceActivity(trace_id=tool.id, request_id=row.request_id,
            sequence=sequence,
            started_at_ms=step.get('started_at_ms') or now,
            finished_at_ms=step.get('finished_at_ms'), duration_ms=step.get('duration_ms')))
