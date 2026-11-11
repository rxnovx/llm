"""Bound document multipart bodies before Starlette spools/parses uploaded files."""
from starlette.responses import JSONResponse
from .config import get_settings

class KnowledgeBodyLimit:
    def __init__(self,app):self.app=app
    async def __call__(self,scope,receive,send):
        path = scope.get('path')
        if scope['type']!='http' or scope.get('method')!='POST' or path not in {'/knowledge/documents', '/v1/chat/completions'}:
            return await self.app(scope,receive,send)
        limit = get_settings().knowledge_max_upload_bytes + 256 * 1024 if path == '/knowledge/documents' else 256 * 1024
        headers=dict(scope.get('headers',[]))
        try:declared=int(headers.get(b'content-length',b'0'))
        except ValueError:declared=limit+1
        async def reject():await JSONResponse({'detail':'요청 크기 제한을 초과했습니다.'},status_code=413)(scope,receive,send)
        if declared>limit:return await reject()
        chunks=[];size=0
        while True:
            message=await receive()
            if message['type']=='http.disconnect':return
            block=message.get('body',b'');size+=len(block)
            if size>limit:return await reject()
            chunks.append(block)
            if not message.get('more_body',False):break
        content=b''.join(chunks);replayed=False
        async def replay():
            nonlocal replayed
            if replayed:return await receive()
            replayed=True;return {'type':'http.request','body':content,'more_body':False}
        await self.app(scope,replay,send)
