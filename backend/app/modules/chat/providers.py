"""Server-configured adapters. No request can supply an upstream URL or API key."""
import asyncio
from collections.abc import AsyncIterator
import json
import secrets
from typing import Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from app.core.config import Settings
from app.core.tls import assistant_tls


class ProviderError(Exception):
    pass


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    context_window: int = Field(8192, ge=1024, le=1048576)
    max_output_tokens: int = Field(2048, ge=64, le=16384)
    max_concurrent: int = Field(2, ge=1, le=64)
    supports_tools: bool = False

    @model_validator(mode='after')
    def budget(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError('모델 ID를 입력하세요.')
        if self.max_output_tokens + 512 >= self.context_window:
            raise ValueError('문맥 한도는 최대 출력보다 512 이상 커야 합니다.')
        return self


class Connection(BaseModel):
    model_config = ConfigDict(extra='forbid')
    id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,64}$')
    name: str = Field(min_length=1, max_length=80)
    provider: Literal['vllm', 'ollama', 'test']
    base_url: str = ''
    model: str = Field('', max_length=200)
    api_key: SecretStr = SecretStr('')
    credential_ready: bool = True
    enabled: bool = True
    allowed_users: list[str] = Field(default_factory=list, max_length=500)
    models: list[ModelProfile] = Field(default_factory=list, max_length=100)
    context_window: int = Field(8192, ge=1024, le=1048576)
    max_output_tokens: int = Field(2048, ge=64, le=16384)
    max_concurrent: int = Field(2, ge=1, le=64)
    supports_tools: bool = False

    @model_validator(mode='after')
    def validate_url(self):
        self.allowed_users = sorted(set(name.strip().lower() for name in self.allowed_users if name.strip()))
        if self.provider != 'test':
            url = urlsplit(self.base_url)
            if (url.scheme not in {'http', 'https'} or not url.hostname or url.username
                    or url.password or url.query or url.fragment):
                raise ValueError('Use an HTTP(S) base URL without credentials, query or fragment')
            # Validate malformed / out-of-range ports as well.
            _ = url.port
        ModelProfile(name=self.model or 'default', context_window=self.context_window,
            max_output_tokens=self.max_output_tokens, max_concurrent=self.max_concurrent)
        if len({profile.name for profile in self.models}) != len(self.models):
            raise ValueError('모델 ID는 중복할 수 없습니다.')
        return self

    def profile(self, model: str) -> ModelProfile:
        for profile in self.models:
            if profile.name == model:
                return profile
        if self.models:
            raise ProviderError('이 연결에서 허용되지 않은 모델입니다.')
        return ModelProfile(name=model or self.model or 'demo', context_window=self.context_window,
            max_output_tokens=self.max_output_tokens, max_concurrent=self.max_concurrent,supports_tools=self.supports_tools)

    def public(self):
        return {'id': self.id, 'name': self.name, 'provider': self.provider,
                'default_model': self.model, 'is_test': self.provider == 'test',
                'models': [item.model_dump() for item in self.models],
                'context_window': self.context_window, 'max_output_tokens': self.max_output_tokens,
                'max_concurrent': self.max_concurrent,'supports_tools':self.supports_tools}


def connections(settings: Settings, db=None, user=None, *, include_disabled=False) -> list[Connection]:
    try:
        values = json.loads(settings.llm_connections_json)
        if not isinstance(values, list) or len(values) > 20:
            raise ValueError()
        configured = [Connection.model_validate({'max_output_tokens': settings.llm_max_tokens,
            'context_window': max(8192, settings.llm_max_tokens + 1024), **value}) for value in values]
        ids = [item.id for item in configured]
        if len(ids) != len(set(ids)) or 'demo' in ids or any(item.provider == 'test' for item in configured):
            raise ValueError()
    except (ValueError, TypeError, ValidationError):
        # Never include configuration values: they may contain an API key.
        raise ProviderError('서버의 LLM_CONNECTIONS_JSON 설정을 확인해 주세요.') from None
    demo = [Connection(id='demo', name='테스트 모델', provider='test', model='demo')] if settings.llm_test_mode else []
    if db is not None:
        from app.modules.chat.registry import overlay
        configured = overlay(settings, configured, db)
    return [item for item in demo + configured if (include_disabled or (item.enabled and item.credential_ready))
        and (user is None or user.is_global_admin or not item.allowed_users or user.username in item.allowed_users)]


def resolve(settings: Settings, connection_id: str, db=None, user=None) -> Connection:
    for item in connections(settings, db, user):
        if item.id == connection_id:
            return item
    raise ProviderError('선택한 연결을 사용할 수 없습니다. 연결 설정을 확인해 주세요.')


def client(connection: Connection, timeout: float) -> httpx.AsyncClient:
    key = connection.api_key.get_secret_value()
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
        headers={'Authorization': f'Bearer {key}'} if key else {},
        follow_redirects=False, trust_env=False,verify=assistant_tls())


def endpoint(connection: Connection, suffix: str) -> str:
    return f'{connection.base_url.rstrip("/")}/{suffix}'


async def list_models(connection: Connection) -> list[str]:
    if connection.provider == 'test':
        return ['demo']
    path, key = ('models', 'data') if connection.provider == 'vllm' else ('api/tags', 'models')
    try:
        async with client(connection, 10) as session:
            response = await session.get(endpoint(connection, path))
            response.raise_for_status()
            payload = response.json()
        field = 'id' if connection.provider == 'vllm' else 'name'
        names = [item[field] for item in payload[key] if isinstance(item.get(field), str)]
        return list(dict.fromkeys(names))[:200]
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise ProviderError('모델 목록을 가져오지 못했습니다. 서버 주소와 인증 설정을 확인해 주세요.') from None


DEMO_REPLIES = [
    '## Markdown 예시\n\n**핵심 내용**을 정리하고 ~~완료한 항목~~을 구분할 수 있어요.\n\n- [x] 대화 시작\n- [ ] 다음 질문 정리\n\n간단한 관계식은 $E=mc^2$처럼 표현할 수 있습니다.\n\n> 코드와 표, 수식도 같은 대화 안에서 읽을 수 있어요.\n\n추가 설명은 각주로 남겨둘게요.[^1]\n\n[^1]: 이것은 실제 모델 호출 없이 생성된 테스트 예시입니다.',
    '좋아요. 먼저 **목표와 조건**을 나눠 적어 보면 정리가 쉬워져요.\n\n1. 원하는 결과를 한 문장으로 적기\n2. 꼭 지켜야 할 조건 정리하기\n3. 가장 작은 단계부터 실행하기\n\n지금 가장 먼저 해결하고 싶은 부분은 무엇인가요?',
    '한 가지 방법만 고르기보다 두 가지 안을 비교해 보면 어떨까요?\n\n| 기준 | 간단하게 시작 | 충분히 준비 |\n| --- | --- | --- |\n| 장점 | 바로 확인할 수 있어요 | 방향을 미리 정할 수 있어요 |\n| 고려할 점 | 수정이 필요할 수 있어요 | 준비 시간이 필요해요 |\n\n어떤 쪽이 지금 상황에 더 잘 맞나요?',
    '조금 더 구체적으로 생각해 볼게요.\n\n- **현재 상황**: 이미 알고 있는 것과 모르는 것을 나눠 보세요.\n- **다음 행동**: 오늘 할 수 있는 일 하나를 정해 보세요.\n- **확인 방법**: 잘됐는지 판단할 기준을 적어 보세요.\n\n작게 시작한 뒤 결과를 보고 조정해도 충분해요.',
    '코드로 표현하면 이런 식으로 시작할 수 있어요.\n\n```python\ndef next_step(goal: str) -> str:\n    return f"먼저 {goal}의 가장 작은 단계를 정해 보세요."\n\nprint(next_step("아이디어 정리"))\n```\n\n실제로 다루고 싶은 코드나 입력 예시가 있다면 이어서 보내 주세요.',
    '그 부분은 배경을 조금 더 알면 더 잘 정리할 수 있을 것 같아요.\n\n어떤 결과를 원하고, 지금은 어디까지 진행했나요? 한두 문장으로 알려 주셔도 괜찮아요.',
]


async def stream_reply(connection: Connection, model: str, history: list[dict], settings: Settings) -> AsyncIterator[str]:
    if connection.provider == 'test':
        reply = secrets.choice(DEMO_REPLIES)
        for index in range(0, len(reply), 9):
            await asyncio.sleep(0.025)
            yield reply[index:index + 9]
        return
    vllm = connection.provider == 'vllm'
    profile = connection.profile(model)
    payload = {'model': model, 'messages': history, 'stream': True}
    payload.update({'max_tokens': profile.max_output_tokens} if vllm else {
        'options': {'num_predict': profile.max_output_tokens, 'num_ctx': profile.context_window}})
    complete = False
    try:
        async with client(connection, settings.llm_timeout_seconds) as session:
            async with session.stream('POST', endpoint(connection, 'chat/completions' if vllm else 'api/chat'), json=payload) as response:
                if not response.is_success:
                    raise ProviderError('모델 서버가 요청을 거부했습니다. 모델 이름과 서버 인증을 확인해 주세요.')
                async for line in response.aiter_lines():
                    if not line or (vllm and not line.startswith('data:')):
                        continue
                    data = line[5:].strip() if vllm else line
                    if data == '[DONE]':
                        complete = True
                        break
                    item = json.loads(data)
                    if item.get('error'):
                        raise ProviderError('모델 서버에서 응답 생성 중 오류가 발생했습니다.')
                    if vllm:
                        choices = item.get('choices') or []
                        if not choices:
                            continue
                        text = choices[0].get('delta', {}).get('content', '')
                        complete = complete or choices[0].get('finish_reason') is not None
                    else:
                        text = item.get('message', {}).get('content', '')
                        complete = bool(item.get('done'))
                    if text and isinstance(text, str):
                        yield text
                    if not vllm and complete:
                        break
        if not complete:
            raise ProviderError('모델 서버의 응답이 중간에 끊겼습니다. 다시 시도해 주세요.')
    except httpx.TimeoutException:
        raise ProviderError('모델 서버 응답 시간이 초과되었습니다. 다시 시도해 주세요.') from None
    except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
        raise ProviderError('모델 서버에 연결할 수 없거나 응답 형식이 올바르지 않습니다.') from None


async def tool_reply(connection,model,history,tools,settings):
    """One structured agent step; tools are executed exclusively by the application host."""
    if connection.provider=='test':
        names={t['function']['name'] for t in tools}
        if 'repo_map' not in names:
            return {'role':'assistant','content':'테스트 모드에서 선택한 자료와 앱 연결 흐름을 확인했습니다. '+('[KS1]' if any('[KS1]' in m.get('content','') for m in history) else '')+'\n\n실제 모델이 작성한 분석은 아닙니다. 실제 연결을 선택하면 자료 검색과 도구 호출을 사용해 답변합니다.','tool_calls':[]}
        results=[item for item in history if item['role']=='tool']
        if not results:
            return {'role':'assistant','content':'','tool_calls':[{'id':'demo-map','type':'function','function':{'name':'repo_map','arguments':{'limit':20}}}]}
        if len(results)==1:
            value=json.loads(results[0]['content'])
            path=next((f['path'] for f in value.get('files',[]) if f['path'].endswith(('.py','.ts','.tsx','.js','.md'))),None)
            if path:return {'role':'assistant','content':'','tool_calls':[{'id':'demo-read','type':'function','function':{'name':'workspace_read_file','arguments':{'path':path,'start_line':1,'end_line':80}}}]}
        return {'role':'assistant','content':'테스트 모드에서 저장소 탐색과 코드 조회를 완료했습니다. [GL1]\n\n실제 모델의 코드 분석 답변은 아닙니다. 모델 연결을 선택하면 조회한 근거를 바탕으로 분석합니다.','tool_calls':[]}
    if not connection.profile(model).supports_tools:
        raise ProviderError('이 모델은 도구 호출 사용이 설정되지 않았습니다. 운영자가 모델의 supports_tools를 확인해야 합니다.')
    vllm=connection.provider=='vllm'
    profile=connection.profile(model)
    # Ollama uses object arguments; OpenAI-compatible providers use serialized arguments.
    outgoing=[]
    for item in history:
        entry=dict(item)
        if entry.get('tool_calls'):
            entry['tool_calls']=[dict(call,function=dict(call['function'],arguments=(
                json.dumps(call['function']['arguments']) if vllm and isinstance(call['function']['arguments'],dict) else call['function']['arguments']))) for call in entry['tool_calls']]
        outgoing.append(entry)
    payload={'model':model,'messages':outgoing,'tools':tools,'stream':False}
    payload.update({'max_tokens':profile.max_output_tokens,'tool_choice':'auto'} if vllm else {'options':{'num_predict':profile.max_output_tokens,'num_ctx':profile.context_window}})
    try:
        async with client(connection,settings.llm_timeout_seconds) as session:
            async with session.stream('POST',endpoint(connection,'chat/completions' if vllm else 'api/chat'),json=payload) as response:
                if not response.is_success:raise ProviderError('모델의 도구 호출 요청이 거절되었습니다. 도구 지원과 서버 parser 설정을 확인하세요.')
                raw=bytearray()
                async for part in response.aiter_bytes():
                    raw.extend(part)
                    if len(raw)>256000:raise ProviderError('모델의 도구 응답이 크기 제한을 초과했습니다.')
        data=json.loads(raw)
        message=data['choices'][0]['message'] if vllm else data['message']
        content=message.get('content') or ''
        calls=message.get('tool_calls') or []
        if not isinstance(content,str) or not isinstance(calls,list) or len(calls)>4:raise ValueError()
        normalized=[]
        for index,call in enumerate(calls):
            function=call['function']
            arguments=function['arguments']
            if isinstance(arguments,str):arguments=json.loads(arguments)
            if not isinstance(arguments,dict) or not isinstance(function['name'],str):raise ValueError()
            normalized.append({'id':str(call.get('id') or f'call-{index}-{secrets.token_hex(4)}'),'type':'function',
                'function':{'name':function['name'],'arguments':arguments}})
        return {'role':'assistant','content':content,'tool_calls':normalized}
    except ProviderError:raise
    except (httpx.HTTPError,ValueError,KeyError,IndexError,TypeError):
        raise ProviderError('모델 도구 응답을 처리하지 못했습니다. 도구 호출을 지원하는 모델과 parser 설정을 확인하세요.') from None
