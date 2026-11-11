"""Persist bounded MCP excerpts with current server and message authorization."""
import hashlib,json
from fastapi import HTTPException
from sqlalchemy.orm import Session
from app.modules.assistant import mcp
from .models import ToolEvidence
from .conversations import message_owned


def save(bind,user_id,message_id,run_id,server,tool,value,index):
    content=json.dumps(value,ensure_ascii=False)
    truncated=len(content.encode())>10000
    content=content.encode()[:10000].decode(errors='ignore')
    with Session(bind) as db:
        message_owned(db,user_id,message_id)
        row=ToolEvidence(owner_id=user_id,message_id=message_id,run_id=run_id,server_id=server.id,
            fingerprint=server.fingerprint(),label=server.name,tool=tool,content=content,sha256=hashlib.sha256(content.encode()).hexdigest())
        db.add(row);db.flush()
        result={'id':f'MS{index}','app':'mcp','evidence_id':row.id,'server_id':server.id,'server':server.name,'tool':tool,'sha256':row.sha256,'truncated':truncated}
        db.commit();return result


def read(db,user_id,evidence_id,*,conversation_id=None):
    row=db.get(ToolEvidence,str(evidence_id))
    if not row or row.owner_id!=user_id:raise HTTPException(404,'앱 조회 근거를 찾을 수 없습니다.')
    _,conversation=message_owned(db,user_id,row.message_id)
    if conversation_id and conversation.id!=conversation_id:raise HTTPException(404,'이 대화의 앱 근거가 아닙니다.')
    server=mcp.allowed(db,user_id,row.server_id)
    if server.fingerprint()!=row.fingerprint:raise HTTPException(409,'앱 연결 설정이 변경되었습니다. 다시 조회하세요.')
    return row


def verify(bind,user_id,conversation_id,sources):
    with Session(bind) as db:
        for source in sources:
            if source.get('app')!='mcp':continue
            row=read(db,user_id,source['evidence_id'],conversation_id=conversation_id)
            if source['sha256']!=row.sha256:raise HTTPException(409,'앱 근거가 변경되었습니다.')
