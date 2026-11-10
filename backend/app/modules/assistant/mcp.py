"""Multiple private MCP providers. Endpoints and tool permission lists are operator-owned."""
import hashlib
import json
import re
from urllib.parse import urlsplit
from fastapi import HTTPException
from pydantic import BaseModel,ConfigDict,Field,ValidationError,model_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.core.config import get_settings
from app.core.tls import assistant_tls
from app.modules.auth.models import User
from .models import McpSetting

class ServerInput(BaseModel):
    model_config=ConfigDict(extra='forbid',str_strip_whitespace=True)
    id: str=Field(pattern=r'^[a-zA-Z0-9_-]{1,40}$')
    name: str=Field(min_length=1,max_length=80)
    url: str=Field(max_length=500)
    secret_ref: str=Field('',max_length=80,pattern=r'^[a-zA-Z0-9_-]*$')
    enabled: bool=True
    read_tools: list[str]=Field(default_factory=list,max_length=20)
    write_tools: list[str]=Field(default_factory=list,max_length=20)
    allowed_users: list[str]=Field(default_factory=list,max_length=200)
    timeout_seconds: int=Field(30,ge=1,le=120)
    revision: int=Field(0,ge=0)

    @model_validator(mode='after')
    def valid(self):
        u=urlsplit(self.url)
        if u.scheme not in {'http','https'} or not u.hostname or u.username or u.password or u.query or u.fragment:
            raise ValueError('인증 정보가 없는 HTTP(S) MCP 주소를 지정하세요.')
        if set(self.read_tools)&set(self.write_tools):raise ValueError('읽기·쓰기 도구 이름이 겹칩니다.')
        if any(not re.fullmatch(r'[A-Za-z0-9_.:-]{1,100}',t) for t in self.read_tools+self.write_tools):raise ValueError('도구 이름 형식을 확인하세요.')
        if len(set(self.read_tools+self.write_tools))!=len(self.read_tools+self.write_tools):raise ValueError('중복된 도구 이름입니다.')
        self.allowed_users=sorted(set(u.strip().lower() for u in self.allowed_users))
        return self

    def fingerprint(self):return hashlib.sha256(self.model_dump_json().encode()).hexdigest()

def servers(db):
    try:
        raw=json.loads(get_settings().mcp_servers_json)
        if not isinstance(raw,list) or len(raw)>30:raise ValueError()
        rows={v.id:v for v in [ServerInput.model_validate(item) for item in raw]}
        if len(rows)!=len(raw):raise ValueError()
        for row in db.scalars(select(McpSetting)):rows[row.id]=ServerInput.model_validate(json.loads(row.data_json)|{'revision':row.revision})
        return list(rows.values())
    except (ValueError,ValidationError,TypeError):raise HTTPException(503,'MCP 서버 등록 설정을 확인하세요.') from None

def allowed(db,user_id,server_id):
    user=db.get(User,user_id)
    if not user or not user.is_active or user.registration_status!='approved':raise HTTPException(403,'계정 사용 권한이 없습니다.')
    server=next((s for s in servers(db) if s.id==server_id),None)
    if not server or not server.enabled or server.allowed_users and user.username not in server.allowed_users and not user.is_global_admin:
        raise HTTPException(403,'이 MCP 서버를 사용할 수 없습니다.')
    return server

def key(server):
    try:
        values=json.loads(get_settings().mcp_secrets_json.get_secret_value())
        value=values.get(server.secret_ref,'') if server.secret_ref else ''
        if not isinstance(value,str) or '\r' in value or '\n' in value or server.secret_ref and not value:raise ValueError()
        return value
    except (ValueError,TypeError,AttributeError):raise HTTPException(503,'MCP 인증 키 참조를 확인하세요.') from None

async def operation(server,callback):
    try:
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        import httpx2
    except ImportError:raise HTTPException(503,'requirements-mcp-client.txt를 설치하세요.') from None
    import asyncio
    token=key(server)
    try:
        async with asyncio.timeout(server.timeout_seconds):
            async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+token} if token else {},trust_env=False,follow_redirects=False,timeout=server.timeout_seconds,verify=assistant_tls()) as http:
                async with Client(streamable_http_client(server.url,http_client=http),read_timeout_seconds=server.timeout_seconds) as client:
                    return await callback(client)
    except HTTPException:raise
    except Exception:raise HTTPException(502,f'{server.name} MCP 연결 또는 도구 응답을 확인하세요.') from None

async def manifest(bind,user_id,ids,allow_writes=False):
    tools=[];mapping={}
    for server_id in ids:
        with Session(bind) as db:server=allowed(db,user_id,server_id)
        result=await operation(server,lambda client:client.list_tools())
        permitted=set(server.read_tools+(server.write_tools if allow_writes else []))
        for item in result.tools:
            if item.name not in permitted:continue
            schema=item.input_schema
            if not isinstance(schema,dict) or len(json.dumps(schema))>10000:raise HTTPException(422,'MCP 도구 입력 규격이 너무 큽니다.')
            name='mcp_'+hashlib.sha256((server.id+'\0'+item.name).encode()).hexdigest()[:20]
            tools.append({'type':'function','function':{'name':name,'description':f'{server.name}: {item.name}. '+(item.description or '')[:400],
                'parameters':schema}})
            mapping[name]=(server,item.name,schema)
            if len(tools)>20:raise HTTPException(422,'선택한 MCP 도구가 많습니다. 서버 또는 운영 도구 허용 목록을 줄이세요.')
    return tools,mapping

async def call(bind,user_id,mapping,name,args,allow_writes=False):
    if name not in mapping:raise HTTPException(403,'허용되지 않은 MCP 도구입니다.')
    original,tool,schema=mapping[name]
    with Session(bind) as db:server=allowed(db,user_id,original.id)
    if server.fingerprint()!=original.fingerprint():raise HTTPException(409,'MCP 서버 등록이 변경되었습니다. 다시 요청하세요.')
    if tool not in server.read_tools and not (allow_writes and tool in server.write_tools):raise HTTPException(403,'쓰기 도구 사용을 허용해야 합니다.')
    # External $ref URLs are forbidden: schema validation must never fetch the network.
    def refs(value):
        if isinstance(value,dict):
            for k,v in value.items():
                if k in {'$ref','$dynamicRef','$recursiveRef'} and (not isinstance(v,str) or not v.startswith('#')):raise HTTPException(422,'외부 참조가 있는 도구 규격은 지원하지 않습니다.')
                refs(v)
        elif isinstance(value,list):
            for v in value:refs(v)
    refs(schema)
    try:
        import jsonschema
        jsonschema.validate(args,schema)
    except (ValueError,jsonschema.ValidationError,jsonschema.SchemaError):raise HTTPException(422,'MCP 도구 입력이 규격과 맞지 않습니다.') from None
    result=await operation(server,lambda client:client.call_tool(tool,args))
    with Session(bind) as db:
        if allowed(db,user_id,server.id).fingerprint()!=server.fingerprint():raise HTTPException(409,'MCP 연결이 변경되었습니다.')
    if result.is_error:raise HTTPException(422,f'{server.name}의 {tool} 도구가 실패했습니다.')
    value=result.structured_content
    if value is None:value={'text':'\n'.join(getattr(part,'text','') for part in result.content)}
    return {'server':server.name,'tool':tool,'result':value}
