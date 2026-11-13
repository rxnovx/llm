"""Document generation and bounded storage for generated files."""
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
from typing import Annotated, Literal, get_args
from xml.sax.saxutils import escape
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session
from app.modules.auth.models import User
from app.modules.chat.models import Conversation, Message
from app.modules.chat.scheduler import lock
from .models import Artifact, ArtifactScope, MessageState
from .conversations import artifact_public, message_owned, project_for
from .projects import project_access
from . import document_formats

MIME={'md':'text/markdown; charset=utf-8','txt':'text/plain; charset=utf-8','csv':'text/csv; charset=utf-8',
    'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','pdf':'application/pdf',
    'html':'text/html; charset=utf-8',
    'docx':'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'pptx':'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'zip':'application/zip','wav':'audio/wav',
    'png':'image/png','jpg':'image/jpeg','webp':'image/webp'}
MAX_RESULT_BYTES = 10 * 1024 * 1024
MAX_FILES_PER_RUN = 3
REVISION_DRAFT = 'revision_draft'
DRAFT_STATUSES = ('draft', REVISION_DRAFT)
DOCUMENT_TOOL = 'create_document'
AUDIO_TOOL = 'create_audio'
ARCHIVE_TOOL = 'create_archive'
FILE_TOOLS = {DOCUMENT_TOOL, AUDIO_TOOL, ARCHIVE_TOOL}
ExportFormat = Literal['md', 'txt', 'csv', 'xlsx', 'pdf', 'html', 'docx']
DocumentFormat = Literal['md', 'txt', 'csv', 'xlsx', 'pdf', 'html', 'docx', 'pptx']
DOCUMENT_FORMATS = get_args(DocumentFormat)


class SlideInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    title: str = Field(min_length=1, max_length=90)
    bullets: list[Annotated[str, Field(min_length=1, max_length=160)]] = Field(default_factory=list, max_length=5)
    notes: str = Field('', max_length=1800)


class DocumentInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str=Field(min_length=1,max_length=150)
    format: DocumentFormat
    content: str=Field('',max_length=200000, description='Full text, or a standalone HTML document with inline assets for html.')
    columns: list[str]=Field(default_factory=list,max_length=30)
    rows: list[list[str|int|float|bool|None]]=Field(default_factory=list,max_length=500)
    slides: list[SlideInput] = Field(default_factory=list, max_length=24, description='Ordered slides for pptx only.')

    @model_validator(mode='after')
    def bounded(self):
        if self.format == 'pptx':
            if not self.slides or self.content.strip() or self.columns or self.rows:
                raise ValueError('PPTX에는 슬라이드 목록을 지정하세요.')
        elif self.slides or not (self.content.strip() or self.rows):
            raise ValueError('문서 내용 또는 표가 필요합니다.')
        if self.rows and (not self.columns or any(len(r)!=len(self.columns) for r in self.rows)):raise ValueError('표의 열과 행 길이를 맞추세요.')
        if any(isinstance(v,float) and not math.isfinite(v) for r in self.rows for v in r):raise ValueError('표의 숫자는 유한한 값이어야 합니다.')
        if any(len(str(v))>4000 for r in [self.columns,*self.rows] for v in r):raise ValueError('표 셀은 최대 4,000자입니다.')
        if len(self.model_dump_json().encode())>250000:raise ValueError('결과 문서의 데이터가 큽니다.')
        return self


def requested_tools(question: str) -> set[str]:
    requested = set()
    creating = re.search(r'만들|생성|제작|작성|저장|변환|내보|읽어|낭독|압축|묶어|다운로드|(?:로|으로)\s*(?:줘|주세요|부탁)|'
                         r'\b(?:make|generate|create|write|build|save|export|convert|download|narrate|package|bundle|zip up)\b',
                         question, re.I)
    if creating:
        if re.search(r'(?<![a-z0-9])(?:wav|tts|audio|speech)(?![a-z0-9])|오디오|음성|낭독|더빙|음악|효과음|배경음|읽어\s*주는', question, re.I):
            requested.add(AUDIO_TOOL)
        if re.search(r'(?<![a-z0-9])zip(?![a-z0-9])|압축\s*파일|압축해|압축으로', question, re.I):
            requested.add(ARCHIVE_TOOL)
        if re.search(r'(?<![a-z0-9])(?:html|docx|pptx?|md|txt|markdown|powerpoint|slides?)(?![a-z0-9])|'
                     r'프레젠테이션|파워포인트|발표\s*(?:자료|슬라이드)|워드\s*문서|word document|마크다운', question, re.I):
            requested.add(DOCUMENT_TOOL)
    if re.search(r'엑셀|xlsx|csv|pdf|(?:문서|보고서|대본|원고).{0,15}(?:만들|생성|정리|내보)|spreadsheet', question, re.I):
        requested.add(DOCUMENT_TOOL)
    if not requested and re.search(r'다운로드|파일.{0,15}(?:만들|생성|정리|내보)|export', question, re.I):
        requested.add(DOCUMENT_TOOL)
    return requested


def planning_hint(question: str) -> bool:
    """Find requests for a deliverable even when a file extension is omitted."""
    return bool(re.search(
        r'(?:발표|프레젠테이션|보고서|스프레드시트|표|문서|파일)(?:용)?(?:으?로|을|를)?'
        r'[^\n.!?]{0,48}(?:정리|만들|보내|작성|달라|줘)|'
        r'\b(?:turn|prepare|put|convert)\b[^\n.!?]{0,72}\b(?:presentation|deck|slides|report|downloadable)\b',
        question, re.I))


def formats():
    result=['md','txt','csv','html']
    try:import openpyxl;result.append('xlsx')
    except ImportError:pass
    try:import reportlab;result.append('pdf')
    except ImportError:pass
    try:import docx;result.append('docx')
    except ImportError:pass
    try:import pptx;result.append('pptx')
    except ImportError:pass
    return result


def safe_cell(value):
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):return "'"+value
    return value


def build(data):
    table=[data.columns,*data.rows] if data.rows else []
    if data.format == 'html':
        return document_formats.build_html(data.name, data.content, table)
    if data.format == 'docx':
        return document_formats.build_docx(data.name, data.content, table)
    if data.format == 'pptx':
        return document_formats.build_pptx(data.slides)
    if data.format in {'md','txt'}:
        content=data.content
        if table:
            if data.format=='md':
                content+='\n\n'+'\n'.join('| '+' | '.join(str(v if v is not None else '').replace('|','\\|').replace('\n',' ') for v in row)+' |' for row in [data.columns,['---']*len(data.columns),*data.rows])
            else:content+='\n\n'+'\n'.join('\t'.join(str(v if v is not None else '') for v in row) for row in table)
        return content.encode()
    if data.format=='csv':
        out=io.StringIO(newline='');writer=csv.writer(out)
        for row in table or [[line] for line in data.content.splitlines()]:writer.writerow([safe_cell(v) for v in row])
        return out.getvalue().encode('utf-8-sig')
    if data.format=='xlsx':
        from openpyxl import Workbook
        from openpyxl.styles import Font,PatternFill,Alignment
        book=Workbook();sheet=book.active;sheet.title='결과'
        for row in table or [[line] for line in data.content.splitlines()]:sheet.append([safe_cell(v) for v in row])
        sheet.freeze_panes='A2' if table else None
        if table:
            for cell in sheet[1]:cell.font=Font(bold=True,color='FFFFFF');cell.fill=PatternFill('solid',fgColor='303C4B')
        for cells in sheet.columns:
            sheet.column_dimensions[cells[0].column_letter].width=min(60,max(16,max(len(str(c.value or '')) for c in cells)+2))
        for row in sheet:
            for cell in row:cell.alignment=Alignment(vertical='top',wrap_text=True)
        out=io.BytesIO();book.save(out);book.close();return out.getvalue()
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.cidfonts import UnicodeCIDFont
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.pagesizes import A4
    from reportlab.platypus import SimpleDocTemplate,Paragraph,Spacer,Table,TableStyle
    font='HYSMyeongJo-Medium'
    if font not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(UnicodeCIDFont(font))
    from app.core.config import get_settings
    path=get_settings().artifact_pdf_font_path
    if path:
        if not Path(path).is_file():raise ValueError('PDF용 글꼴 경로를 확인하세요.')
        font='AssistantEmbedded'
        if font not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont(font,path))
    style=ParagraphStyle('Body',fontName=font,fontSize=10,leading=16,wordWrap='CJK',spaceAfter=8)
    title=ParagraphStyle('Heading',parent=style,fontSize=17,leading=25,spaceAfter=16)
    out=io.BytesIO();story=[Paragraph(escape(Path(data.name).stem),title)]
    for line in data.content.splitlines():story.append(Paragraph(escape(line) or ' ',style))
    if table:
        count=len(data.columns)
        # Bound wide tables to a readable page width, retaining all values.
        rendered=[[Paragraph(escape(str(v if v is not None else '')),style) for v in row] for row in table]
        element=Table(rendered,colWidths=[(A4[0]-80)/count]*count,repeatRows=1,hAlign='LEFT',splitInRow=1)
        element.setStyle(TableStyle([('BACKGROUND',(0,0),(-1,0),'#e8edf3'),('VALIGN',(0,0),(-1,-1),'TOP'),('BOX',(0,0),(-1,-1),.4,'#a8b3bf'),('INNERGRID',(0,0),(-1,-1),.25,'#d8dee6')]))
        story.extend([Spacer(1,12),element])
    from reportlab.platypus.doctemplate import LayoutError
    try:SimpleDocTemplate(out,pagesize=A4,leftMargin=40,rightMargin=40,topMargin=40,bottomMargin=40).build(story)
    except LayoutError:raise ValueError('PDF 표가 페이지를 초과합니다. 열 수나 셀 길이를 줄이세요.') from None
    return out.getvalue()


def create(bind,user_id,message_id,run_id,args,*,ready=False,expected_content_hash=None):
    try:data=DocumentInput.model_validate(args)
    except ValueError:raise HTTPException(422,'파일 이름·형식·본문 또는 표 데이터를 확인하세요.') from None
    raw = render(data)
    return store(bind, user_id, message_id, run_id, name=file_name(data.name, data.format), file_format=data.format, raw=raw,
                 payload_json=data.model_dump_json(), ready=ready, expected_content_hash=expected_content_hash)


def render(data: DocumentInput) -> bytes:
    if data.format not in formats():raise HTTPException(503,'이 파일 형식의 문서 생성 의존성을 설치해야 합니다.')
    try:return build(data)
    except ImportError:raise HTTPException(503,'문서 생성 의존성을 확인하세요.') from None
    except (ValueError,KeyError):raise HTTPException(422,'문서를 생성하지 못했습니다. 내용·표·글꼴을 확인하세요.') from None


def file_name(name: str, file_format: str) -> str:
    stem = re.sub(r'[\\/\x00-\x1f:*?"<>|]', '_', name).strip('. ')[:140].rstrip('. ') or '결과'
    return str(Path(stem).with_suffix('.' + file_format))


def before_position(conversation_id, message_id):
    return select(Message.position).where(
        Message.id == str(message_id), Message.conversation_id == str(conversation_id),
    ).correlate(None).scalar_subquery()


def visible_results(db, user_id, conversation_id, formats, *, before_message_id=None, artifact_id=None,
                    names=(), limit=1, for_update=False):
    """Ready files on active answers in this conversation, including forked file links."""
    linked = select(ArtifactScope.artifact_id).where(
        ArtifactScope.artifact_id == Artifact.id, ArtifactScope.message_id == Message.id,
    ).exists()
    candidates = (
        select(Artifact, Message.position, MessageState.artifact_ids_json)
        .select_from(Message)
        .join(Conversation, Conversation.id == Message.conversation_id)
        .join(User, User.id == Conversation.user_id)
        .join(MessageState, MessageState.message_id == Message.id)
        .join(Artifact, or_(Artifact.message_id == Message.id, linked))
        .where(
            Conversation.id == str(conversation_id), Conversation.user_id == user_id,
            User.is_active.is_(True), User.registration_status == 'approved',
            Message.role == 'assistant', Message.status == 'complete',
            Artifact.owner_id == user_id, Artifact.status == 'ready',
            Artifact.format.in_(formats),
        )
    )
    if before_message_id is not None:
        candidates = candidates.where(Message.position < before_position(conversation_id, before_message_id))
    if artifact_id is not None:
        candidates = candidates.where(Artifact.id == str(artifact_id))
    if names:
        candidates = candidates.where(func.lower(Artifact.name).in_(names))
    if for_update:
        candidates = candidates.with_for_update(of=Artifact)
    results = {}
    for item, position, active_ids in db.execute(candidates.order_by(
        Message.position.desc(), Artifact.created_at.desc(), Artifact.id.desc(),
    )):
        if item.id not in results and item.id in json.loads(active_ids or '[]'):
            results[item.id] = (item, position)
            if len(results) == limit:
                break
    return list(results.values())


def verify_revision_source(db, user_id, message, conversation, file_format, source_id, source_hash):
    project = project_for(db, conversation.id)
    if project:
        project_access(db, user_id, project.id, write=True)
    matches = visible_results(db, user_id, conversation.id, (file_format,), before_message_id=message.id,
        artifact_id=source_id, for_update=True)
    if not matches or matches[0][0].sha256 != source_hash:
        raise HTTPException(409, '원본 결과 파일이 바뀌었거나 삭제되었습니다. 현재 파일을 다시 확인하세요.')


def store(bind, user_id, message_id, run_id, *, name, file_format, raw, payload_json,
          ready=False, expected_content_hash=None, revision_sources=()):
    if len(raw) > MAX_RESULT_BYTES:
        raise HTTPException(413, '결과 파일이 10MiB를 초과합니다.')
    status = 'ready' if ready else 'draft'
    if revision_sources:
        payload_json = json.dumps(json.loads(payload_json) | {'revision_sources': revision_sources}, ensure_ascii=False)
        status = REVISION_DRAFT
    with Session(bind,expire_on_commit=False) as db:
        lock(db);message,conversation=message_owned(db,user_id,message_id,idle=ready)
        if ready and (message.status!='complete' or expected_content_hash and hashlib.sha256(message.content.encode()).hexdigest()!=expected_content_hash):raise HTTPException(409,'답변이 변경되었습니다. 현재 답변에서 다시 내보내세요.')
        if not ready and (message.status!='pending' or conversation.generation_id!=str(run_id) or conversation.cancel_requested):raise HTTPException(409,'취소되었거나 완료된 요청의 파일은 저장하지 않습니다.')
        for source in revision_sources:
            verify_revision_source(db, user_id, message, conversation, source['format'], source['id'], source['sha256'])
        count,size=db.execute(select(func.count(Artifact.id),func.coalesce(func.sum(Artifact.size),0)).where(Artifact.owner_id==user_id)).one()
        if count>=200 or size+len(raw)>100*1024*1024:raise HTTPException(422,'결과 파일 저장 한도입니다. 필요 없는 파일을 삭제하세요.')
        if db.scalar(select(func.count()).select_from(Artifact).where(Artifact.run_id==str(run_id)))>=MAX_FILES_PER_RUN:raise HTTPException(422,'한 번에 결과 파일은 3개까지 만들 수 있습니다.')
        row=Artifact(owner_id=user_id,message_id=message.id,run_id=str(run_id),name=name,format=file_format,status=status,
            data=raw,size=len(raw),sha256=hashlib.sha256(raw).hexdigest(),payload_json=payload_json)
        db.add(row);db.commit();return artifact_public(row)


def finalize(bind,user_id,run_id,ids,*,approve):
    with Session(bind) as db:
        settle_drafts(db,user_id,run_id,ids if approve else ())
        db.commit()


def settle_drafts(db,user_id,run_id,approved_ids):
    query=select(Artifact).where(Artifact.owner_id==user_id,Artifact.run_id==str(run_id),
        Artifact.status.in_(DRAFT_STATUSES))
    for row in db.scalars(query):
        if row.id not in approved_ids:
            db.delete(row)
            continue
        if row.status == REVISION_DRAFT:
            lock(db)
            message, conversation = message_owned(db, user_id, row.message_id)
            if message.status != 'pending' or conversation.generation_id != str(run_id) or conversation.cancel_requested:
                raise HTTPException(409, '취소되었거나 완료된 요청의 파일은 공개하지 않습니다.')
            for source in json.loads(row.payload_json)['revision_sources']:
                verify_revision_source(db, user_id, message, conversation, source['format'], source['id'], source['sha256'])
        row.status='ready'


def export_input(name,format,content):
    columns=[];rows=[];lines=content.splitlines()
    if format in {'csv','xlsx'}:
        for index,line in enumerate(lines[:-1]):
            if '|' in line and re.fullmatch(r'[\s|:\-]+',lines[index+1]) and '-' in lines[index+1]:
                columns=[c.strip() for c in line.strip().strip('|').split('|')]
                for row in lines[index+2:]:
                    if '|' not in row:break
                    cells=[c.strip() for c in row.strip().strip('|').split('|')]
                    if len(cells)==len(columns):rows.append(cells)
                break
        if not rows:columns=['내용'];rows=[[line] for line in lines]
    return {'name':name,'format':format,'content':content if not rows else '', 'columns':columns,'rows':rows}


def tool():
    available = formats()
    schema = DocumentInput.model_json_schema()
    schema['properties']['format']['enum'] = available
    return {'type':'function','function':{'name':DOCUMENT_TOOL,
        'description':'Create a downloadable document. '
        'Use full standalone HTML with inline CSS/JS for HTML and ordered slides with short bullets and optional speaker notes for PPTX.',
        'parameters':schema}}
