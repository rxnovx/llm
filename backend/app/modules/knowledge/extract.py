"""Bounded text extraction. Invoked in a disposable subprocess, never inside the API process."""
import csv
import io
import json
from pathlib import Path
import re
import sys
import time

from . import ocr
import zipfile

SUPPORTED={'.txt','.md','.markdown','.csv','.tsv','.json','.html','.htm','.pdf','.docx','.xlsx'}
MAX_TEXT=2_000_000

def archive(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        entries=z.infolist()
        if len(entries)>5000 or sum(e.file_size for e in entries)>40*1024*1024:
            raise ValueError('압축 문서의 내용이 너무 큽니다.')

def extract(raw, filename, *, ocr_languages=None):
    suffix=Path(filename).suffix.lower()
    if suffix not in SUPPORTED and suffix not in ocr.IMAGE_FORMATS:
        raise ValueError('지원하지 않는 파일 형식입니다.')
    parts = []
    if suffix in ocr.IMAGE_FORMATS:
        if not ocr_languages:
            raise ValueError('이미지 OCR을 활성화하고 Tesseract를 설치하세요.')
        parts.append(('이미지 · OCR', ocr.image_text(raw, ocr_languages, time.monotonic() + 18)))
    elif suffix=='.pdf':
        from pypdf import PdfReader
        reader=PdfReader(io.BytesIO(raw),strict=False)
        if reader.is_encrypted:raise ValueError('암호가 설정된 PDF는 지원하지 않습니다.')
        if len(reader.pages)>500:raise ValueError('PDF는 최대 500페이지까지 지원합니다.')
        for i,page in enumerate(reader.pages):
            parts.append((f'{i+1}쪽',page.extract_text() or ''))
            if sum(len(t) for _,t in parts)>MAX_TEXT:raise ValueError('추출된 문서가 너무 큽니다.')
        if ocr_languages and ocr.installed():
            scanned_pages = [i + 1 for i, (_, value) in enumerate(parts) if not value.strip() and reader.pages[i].images]
            for page, text in ocr.pdf_text(raw, scanned_pages, ocr_languages) if scanned_pages else ():
                parts[page - 1] = (f'{page}쪽 · OCR', text)

    elif suffix=='.docx':
        archive(raw)
        from docx import Document
        doc=Document(io.BytesIO(raw))
        parts=[('문단', '\n'.join(p.text for p in doc.paragraphs))]
        parts.extend((f'표 {i+1}','\n'.join('\t'.join(c.text for c in row.cells) for row in table.rows)) for i,table in enumerate(doc.tables))
    elif suffix=='.xlsx':
        archive(raw)
        from openpyxl import load_workbook
        book=load_workbook(io.BytesIO(raw),read_only=True,data_only=True,keep_links=False)
        try:
            count=0
            for sheet in book:
                lines=[]
                for i,row in enumerate(sheet.iter_rows(values_only=True)):
                    count+=1
                    if count>50000:raise ValueError('시트의 행 수 제한을 초과했습니다.')
                    line=f'{i+1}행: '+' | '.join('' if value is None else str(value) for value in row[:100])
                    lines.append(line)
                    if sum(map(len,lines))>MAX_TEXT:raise ValueError('추출된 시트가 너무 큽니다.')
                parts.append((sheet.title,'\n'.join(lines)))
        finally:book.close()
    else:
        text=raw.decode('utf-8-sig')
        if '\x00' in text:raise ValueError('텍스트 파일을 확인하세요.')
        if suffix in {'.html','.htm'}:
            from html.parser import HTMLParser
            class Text(HTMLParser):
                def __init__(self):super().__init__();self.parts=[];self.hidden=0
                def handle_starttag(self,tag,attrs):
                    if tag in {'script','style'}:self.hidden+=1
                def handle_endtag(self,tag):
                    if tag in {'script','style'}:self.hidden=max(0,self.hidden-1)
                def handle_data(self,data):
                    if not self.hidden:self.parts.append(data)
            parser=Text();parser.feed(text);text='\n'.join(parser.parts)
        parts=[('본문',text)]
    if sum(len(t) for _,t in parts)>MAX_TEXT:raise ValueError('추출된 내용은 최대 200만 자입니다.')
    chunks=[]
    for location,text in parts:
        text=re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f]','',text)
        lines=text.splitlines();buffer=[];start=1
        for n,line in enumerate(lines,1):
            if buffer and sum(len(t) for t in buffer)+len(line)>1400:
                chunks.append({'location':f'{location} · {start}–{n-1}줄','content':'\n'.join(buffer)})
                buffer=buffer[-2:];start=max(1,n-len(buffer))
            # Split exceptionally long lines too; result size stays bounded.
            for offset in range(0,max(1,len(line)),1400):
                buffer.append(line[offset:offset+1400])
                if len(buffer)==1:start=n
                if sum(len(t) for t in buffer)>=1400:
                    chunks.append({'location':f'{location} · {start}–{n}줄','content':'\n'.join(buffer)})
                    buffer=[];start=n+1
        if buffer and '\n'.join(buffer).strip():chunks.append({'location':f'{location} · {start}–{len(lines)}줄','content':'\n'.join(buffer)})
    chunks=[c for c in chunks if c['content'].strip()]
    if not chunks:raise ValueError('읽을 수 있는 텍스트가 없습니다. 스캔 문서는 OCR 처리 후 등록하세요.')
    if len(chunks)>1500:raise ValueError('문서 분할 수 제한을 초과했습니다.')
    return chunks

if __name__=='__main__':
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS,(768*1024*1024,768*1024*1024))
        resource.setrlimit(resource.RLIMIT_CPU,(20,20))
    except (ImportError,OSError,ValueError):pass
    try:
        payload=json.loads(sys.stdin.buffer.read(30*1024*1024))
        import base64
        value=extract(base64.b64decode(payload['data']),payload['filename'],ocr_languages=payload.get('ocr_languages'))
        print(json.dumps({'chunks':value},ensure_ascii=False))
    except ImportError:print(json.dumps({'error':'문서 추출 의존성이 없습니다. requirements.txt를 설치하세요.'}))
    except Exception as error:print(json.dumps({'error':str(error)[:250] if isinstance(error,ValueError) else '문서를 추출하지 못했습니다. 파일 형식과 내용을 확인하세요.'}))
