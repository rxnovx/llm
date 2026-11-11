"""Bounded document generation from data, never from model-supplied executable code."""
import csv
import hashlib
import io
import json
import math
from pathlib import Path
import re
from typing import Literal
from xml.sax.saxutils import escape
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,model_validator
from sqlalchemy import func,select,delete,update
from sqlalchemy.orm import Session
from app.modules.chat.scheduler import lock
from .models import Artifact
from .conversations import artifact_public,message_owned

MIME={'md':'text/markdown; charset=utf-8','txt':'text/plain; charset=utf-8','csv':'text/csv; charset=utf-8',
    'xlsx':'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet','pdf':'application/pdf'}


class DocumentInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    name: str=Field(min_length=1,max_length=150)
    format: Literal['md','txt','csv','xlsx','pdf']
    content: str=Field('',max_length=200000)
    columns: list[str]=Field(default_factory=list,max_length=30)
    rows: list[list[str|int|float|bool|None]]=Field(default_factory=list,max_length=500)

    @model_validator(mode='after')
    def bounded(self):
        if not self.content.strip() and not self.rows:raise ValueError('문서 내용 또는 표가 필요합니다.')
        if self.rows and (not self.columns or any(len(r)!=len(self.columns) for r in self.rows)):raise ValueError('표의 열과 행 길이를 맞추세요.')
        if any(isinstance(v,float) and not math.isfinite(v) for r in self.rows for v in r):raise ValueError('표의 숫자는 유한한 값이어야 합니다.')
        if any(len(str(v))>4000 for r in [self.columns,*self.rows] for v in r):raise ValueError('표 셀은 최대 4,000자입니다.')
        if len(self.model_dump_json().encode())>250000:raise ValueError('결과 문서의 데이터가 큽니다.')
        return self


def hint(question):
    return bool(re.search(r'엑셀|xlsx|csv|pdf|다운로드|(?:문서|보고서|파일).{0,15}(?:만들|생성|정리|내보)|export|spreadsheet',question,re.I))


def formats():
    result=['md','txt','csv']
    try:import openpyxl;result.append('xlsx')
    except ImportError:pass
    try:import reportlab;result.append('pdf')
    except ImportError:pass
    return result


def safe_cell(value):
    if isinstance(value,str) and value.lstrip().startswith(('=','+','-','@')):return "'"+value
    return value


def build(data):
    table=[data.columns,*data.rows] if data.rows else []
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
    if data.format not in formats():raise HTTPException(503,'이 파일 형식의 문서 생성 의존성을 설치해야 합니다.')
    try:raw=build(data)
    except ImportError:raise HTTPException(503,'문서 생성 의존성을 확인하세요.') from None
    except (ValueError,KeyError):raise HTTPException(422,'문서를 생성하지 못했습니다. 내용·표·글꼴을 확인하세요.') from None
    if len(raw)>10*1024*1024:raise HTTPException(413,'결과 파일이 10MiB를 초과합니다.')
    name=re.sub(r'[\\/\x00-\x1f:*?"<>|]','_',data.name).strip('. ')[:140] or '결과'
    name=str(Path(name).with_suffix('.'+data.format))
    with Session(bind,expire_on_commit=False) as db:
        lock(db);message,conversation=message_owned(db,user_id,message_id,idle=ready)
        if ready and (message.status!='complete' or expected_content_hash and hashlib.sha256(message.content.encode()).hexdigest()!=expected_content_hash):raise HTTPException(409,'답변이 변경되었습니다. 현재 답변에서 다시 내보내세요.')
        if not ready and (message.status!='pending' or conversation.generation_id!=str(run_id) or conversation.cancel_requested):raise HTTPException(409,'취소되었거나 완료된 요청의 파일은 저장하지 않습니다.')
        count,size=db.execute(select(func.count(Artifact.id),func.coalesce(func.sum(Artifact.size),0)).where(Artifact.owner_id==user_id)).one()
        if count>=200 or size+len(raw)>100*1024*1024:raise HTTPException(422,'결과 파일 저장 한도입니다. 필요 없는 파일을 삭제하세요.')
        if db.scalar(select(func.count()).select_from(Artifact).where(Artifact.run_id==str(run_id)))>=3:raise HTTPException(422,'한 번에 결과 파일은 3개까지 만들 수 있습니다.')
        row=Artifact(owner_id=user_id,message_id=message.id,run_id=str(run_id),name=name,format=data.format,status='ready' if ready else 'draft',
            data=raw,size=len(raw),sha256=hashlib.sha256(raw).hexdigest(),payload_json=data.model_dump_json())
        db.add(row);db.commit();return artifact_public(row)


def finalize(bind,user_id,run_id,ids,*,approve):
    with Session(bind) as db:
        query=select(Artifact).where(Artifact.owner_id==user_id,Artifact.run_id==str(run_id),Artifact.status=='draft')
        for row in db.scalars(query):
            if approve and row.id in ids:row.status='ready'
            else:db.delete(row)
        db.commit()


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
    return {'type':'function','function':{'name':'create_document','description':'Create a real downloadable md/txt/csv/xlsx/pdf from supplied text or table data. No code execution. Never invent file links.',
        'parameters':DocumentInput.model_json_schema()}}
