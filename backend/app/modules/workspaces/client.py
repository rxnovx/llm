"""Private MCP host client. No model-controlled server URL, identity, ticket or credential."""
from contextlib import asynccontextmanager
import json
from urllib.parse import urlsplit

from fastapi import HTTPException

from app.core.config import get_settings
from terminal_mcp.auth import issue

MODEL_TOOLS={'repo_map','code_search','symbol_lookup','find_references','workspace_read_file','workspace_diff','terminal_jobs',
             'workspace_write_file','terminal_exec','terminal_read','terminal_input','terminal_cancel'}
EXECUTION_TOOLS={'workspace_write_file','terminal_exec','terminal_input'}
# The host waits for jobs and handles cancellation. Advertising polling/stdin
# controls to the same synchronous model loop wastes context and cannot unblock
# a command waiting for input; those controls remain available in the modal/API.
HOST_CONTROLS={'terminal_read','terminal_jobs','terminal_input','terminal_cancel'}


def enabled():
    settings=get_settings()
    return bool(settings.terminal_mcp_url and len(settings.terminal_shared_secret.get_secret_value())>=32)


@asynccontextmanager
async def connected(workspace,*,provision=False,execute=False):
    settings=get_settings()
    url=urlsplit(settings.terminal_mcp_url)
    if not enabled() or url.scheme not in {'http','https'} or not url.hostname or url.username or url.password or url.query or url.fragment:
        raise HTTPException(503,'Terminal MCP 서버 주소와 인증 설정이 필요합니다.')
    try:
        from mcp import Client
        from mcp.client.streamable_http import streamable_http_client
        import httpx2
    except ImportError:
        raise HTTPException(503,'백엔드의 requirements-mcp-client.txt 의존성을 설치하세요.') from None
    token=issue(settings.terminal_shared_secret.get_secret_value(),user=workspace.user_id,conversation=workspace.conversation_id,
        workspace=workspace.id,execute=execute,provision=provision,seconds=180)
    try:
        async with httpx2.AsyncClient(headers={'Authorization':'Bearer '+token},timeout=150,follow_redirects=False,trust_env=False) as http:
            transport=streamable_http_client(settings.terminal_mcp_url,http_client=http)
            async with Client(transport,read_timeout_seconds=150) as client:
                yield client
    except HTTPException:raise
    except Exception:
        raise HTTPException(502,'Terminal MCP 연결·응답을 확인해 주세요.') from None


async def call(workspace,name,args,*,provision=False):
    async with connected(workspace,provision=provision,execute=workspace.allow_execution and get_settings().terminal_execution_enabled) as client:
        result=await client.call_tool(name,args)
        if result.is_error:
            message=' '.join(getattr(part,'text','') for part in result.content)
            raise HTTPException(422,message[:800] or '실행 도구를 완료하지 못했습니다.')
        if result.structured_content is not None:return result.structured_content
        try:return json.loads(''.join(getattr(part,'text','') for part in result.content))
        except ValueError:raise HTTPException(502,'MCP 도구 결과의 JSON 형식을 확인하세요.') from None


async def manifest(workspace):
    async with connected(workspace,execute=workspace.allow_execution and get_settings().terminal_execution_enabled) as client:
        result=await client.list_tools()
        tools=[]
        for item in result.tools:
            value=item.model_dump(by_alias=True)
            name=value['name']
            if name not in MODEL_TOOLS or name in HOST_CONTROLS or name in EXECUTION_TOOLS and not workspace.allow_execution:continue
            if name.startswith('terminal_') and not workspace.allow_execution:continue
            schema=dict(value.get('inputSchema') or value.get('input_schema'))
            schema['properties']={k:v for k,v in schema.get('properties',{}).items() if k!='workspace_id'}
            schema['required']=[k for k in schema.get('required',[]) if k!='workspace_id']
            tools.append({'type':'function','function':{'name':name,'description':value.get('description',''),
                'parameters':schema}})
        return tools
