"""Server-configured adapters. No request can supply an upstream URL or API key."""
import asyncio
from base64 import b64encode
from collections.abc import AsyncIterator, Sequence
import json
import secrets
import ssl
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError, model_validator

from app.core.config import Settings
from app.core.tls import assistant_tls
from . import usage

if TYPE_CHECKING:
    from app.modules.workflows.images import ImageData

# Providers report actual image token usage when available. This allowance is
# used for context planning and for quotas before the provider returns counts.
VISION_IMAGE_TOKENS = 1024


class ProviderError(Exception):
    pass


class ModelProfile(BaseModel):
    model_config = ConfigDict(extra='forbid')
    name: str = Field(min_length=1, max_length=200)
    context_window: int = Field(8192, ge=1024, le=1048576)
    max_output_tokens: int = Field(2048, ge=64, le=16384)
    max_concurrent: int = Field(2, ge=1, le=64)
    supports_tools: bool = False
    supports_vision: bool = False

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
    supports_vision: bool = False

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
            max_output_tokens=self.max_output_tokens, max_concurrent=self.max_concurrent,
            supports_tools=self.supports_tools, supports_vision=self.supports_vision)

    def public(self):
        return {'id': self.id, 'name': self.name, 'provider': self.provider,
                'default_model': self.model, 'is_test': self.provider == 'test',
                'models': [item.model_dump() for item in self.models],
                'context_window': self.context_window, 'max_output_tokens': self.max_output_tokens,
                'max_concurrent': self.max_concurrent, 'supports_tools': self.supports_tools,
                'supports_vision': self.supports_vision}


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
    try:
        verify = assistant_tls()
    except OSError:
        raise ProviderError('모델 서버의 CA 번들을 읽지 못했습니다. ASSISTANT_CA_BUNDLE을 확인해 주세요.') from None
    return httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
        headers={'Authorization': f'Bearer {key}'} if key else {},
        follow_redirects=False, trust_env=False, verify=verify)


def endpoint(connection: Connection, suffix: str) -> str:
    return f'{connection.base_url.rstrip("/")}/{suffix}'


def require_success(response, action, invalid_request):
    if response.is_success:
        return
    status = response.status_code
    if status in {401, 403}:
        detail = '모델 서버의 API 키와 접근 권한을 확인해 주세요.'
    elif status == 404:
        detail = '서버 기본 주소, API 경로와 등록한 모델 ID를 확인해 주세요.'
    elif status == 429:
        detail = '모델 서버의 동시 요청 한도와 부하를 확인해 주세요.'
    elif status >= 500:
        detail = '모델 서버의 준비 상태와 추론 로그를 확인해 주세요.'
    elif 300 <= status < 400:
        detail = '리디렉션을 거치지 않는 실제 API 주소를 등록해 주세요.'
    else:
        detail = invalid_request
    raise ProviderError(f'{action} (HTTP {status}) · {detail}')


def transport_error(error):
    if isinstance(error, httpx.ConnectTimeout):
        return ProviderError('모델 서버 접속 시간이 초과되었습니다. FastAPI 실행 환경의 주소·포트·방화벽을 확인해 주세요.')
    if isinstance(error, httpx.TimeoutException):
        return ProviderError('모델 서버 응답 시간이 초과되었습니다. 모델 로딩·서버 부하·시간 제한을 확인해 주세요.')
    cause = error.__cause__
    while cause:
        if isinstance(cause, ssl.SSLCertVerificationError):
            return ProviderError('모델 서버의 TLS 인증서를 확인할 수 없습니다. 호스트 이름과 ASSISTANT_CA_BUNDLE을 확인해 주세요.')
        cause = cause.__cause__
    if isinstance(error, httpx.ConnectError):
        return ProviderError('모델 서버에 연결할 수 없습니다. FastAPI 실행 환경에서 주소·포트·방화벽을 확인해 주세요.')
    return ProviderError('모델 서버와의 HTTP 통신이 중단되었습니다. 서버와 중간 프록시의 로그를 확인해 주세요.')


async def list_models(connection: Connection) -> list[str]:
    if connection.provider == 'test':
        return ['demo']
    path, key = ('models', 'data') if connection.provider == 'vllm' else ('api/tags', 'models')
    try:
        async with client(connection, 10) as session:
            response = await session.get(endpoint(connection, path))
            require_success(response, '모델 목록 요청 거부', '서버 종류와 모델 목록 API를 확인해 주세요.')
            payload = response.json()
        field = 'id' if connection.provider == 'vllm' else 'name'
        names = [item[field] for item in payload[key] if isinstance(item.get(field), str)]
        return list(dict.fromkeys(names))[:200]
    except httpx.HTTPError as error:
        raise transport_error(error) from None
    except (ValueError, KeyError, TypeError, AttributeError):
        raise ProviderError('모델 목록의 응답 형식이 올바르지 않습니다. 서버 종류와 API 경로를 확인해 주세요.') from None


DEMO_REPLIES = [
    '## Markdown 예시\n\n**핵심 내용**을 정리하고 ~~완료한 항목~~을 구분할 수 있어요.\n\n- [x] 대화 시작\n- [ ] 다음 질문 정리\n\n간단한 관계식은 $E=mc^2$처럼 표현할 수 있습니다.\n\n> 코드와 표, 수식도 같은 대화 안에서 읽을 수 있어요.\n\n추가 설명은 각주로 남겨둘게요.[^1]\n\n[^1]: 이것은 실제 모델 호출 없이 생성된 테스트 예시입니다.',
    '좋아요. 먼저 **목표와 조건**을 나눠 적어 보면 정리가 쉬워져요.\n\n1. 원하는 결과를 한 문장으로 적기\n2. 꼭 지켜야 할 조건 정리하기\n3. 가장 작은 단계부터 실행하기\n\n지금 가장 먼저 해결하고 싶은 부분은 무엇인가요?',
    '한 가지 방법만 고르기보다 두 가지 안을 비교해 보면 어떨까요?\n\n| 기준 | 간단하게 시작 | 충분히 준비 |\n| --- | --- | --- |\n| 장점 | 바로 확인할 수 있어요 | 방향을 미리 정할 수 있어요 |\n| 고려할 점 | 수정이 필요할 수 있어요 | 준비 시간이 필요해요 |\n\n어떤 쪽이 지금 상황에 더 잘 맞나요?',
    '조금 더 구체적으로 생각해 볼게요.\n\n- **현재 상황**: 이미 알고 있는 것과 모르는 것을 나눠 보세요.\n- **다음 행동**: 오늘 할 수 있는 일 하나를 정해 보세요.\n- **확인 방법**: 잘됐는지 판단할 기준을 적어 보세요.\n\n작게 시작한 뒤 결과를 보고 조정해도 충분해요.',
    '코드로 표현하면 이런 식으로 시작할 수 있어요.\n\n```python\ndef next_step(goal: str) -> str:\n    return f"먼저 {goal}의 가장 작은 단계를 정해 보세요."\n\nprint(next_step("아이디어 정리"))\n```\n\n실제로 다루고 싶은 코드나 입력 예시가 있다면 이어서 보내 주세요.',
    '그 부분은 배경을 조금 더 알면 더 잘 정리할 수 있을 것 같아요.\n\n어떤 결과를 원하고, 지금은 어디까지 진행했나요? 한두 문장으로 알려 주셔도 괜찮아요.',
]


async def stream_reply(connection: Connection, model: str, history: list[dict], settings: Settings, *,
                       max_tokens=None, temperature=None, top_p=None, stop=None, metadata=None,
                       images: Sequence['ImageData'] = ()) -> AsyncIterator[str]:
    if connection.provider == 'test':
        if images:
            raise ProviderError('이미지 분석을 지원하는 실제 채팅 모델을 선택하세요.')
        reply = secrets.choice(DEMO_REPLIES)
        for index in range(0, len(reply), 9):
            await asyncio.sleep(0.025)
            yield reply[index:index + 9]
        return
    vllm = connection.provider == 'vllm'
    profile = connection.profile(model)
    output_limit = max_tokens or profile.max_output_tokens
    payload = {'model': model, 'messages': history, 'stream': True}
    options = {key: value for key, value in {'temperature': temperature, 'top_p': top_p, 'stop': stop}.items() if value is not None}
    if vllm:
        payload.update(options, max_tokens=output_limit)
        if usage.scope.get():
            payload['stream_options'] = {'include_usage': True}
    else:
        payload['options'] = options | {'num_predict': output_limit, 'num_ctx': profile.context_window}
    request_payload = with_images(payload, images, profile, vllm)
    complete = False
    with usage.measure(connection, model, payload, output_limit, settings.llm_timeout_seconds,
                       extra_input_tokens=len(images) * VISION_IMAGE_TOKENS) as meter:
        try:
            async with client(connection, settings.llm_timeout_seconds) as session:
                async with session.stream('POST', endpoint(connection, 'chat/completions' if vllm else 'api/chat'), json=request_payload) as response:
                    require_success(response, '모델 채팅 요청 거부',
                        '서빙 모델 ID, 채팅 템플릿, 문맥 한도와 이미지 입력 지원을 확인해 주세요.')
                    meter.accepted = True
                    async for line in response.aiter_lines():
                        if not line or (vllm and not line.startswith('data:')):
                            continue
                        data = line[5:].strip() if vllm else line
                        if data == '[DONE]':
                            complete = True
                            break
                        item = json.loads(data)
                        meter.observe(item, vllm)
                        if item.get('error'):
                            raise ProviderError('모델 서버에서 응답 생성 중 오류가 발생했습니다.')
                        if vllm:
                            choices = item.get('choices') or []
                            if not choices:
                                continue
                            delta = choices[0].get('delta', {})
                            text = delta.get('content', '')
                            meter.output(delta.get('reasoning_content'))
                            reason = choices[0].get('finish_reason')
                            if metadata is not None and reason in {'stop', 'length', 'content_filter'}:
                                metadata['finish_reason'] = reason
                            complete = complete or reason is not None
                        else:
                            text = item.get('message', {}).get('content', '')
                            complete = bool(item.get('done'))
                            if complete and metadata is not None and item.get('done_reason') in {'stop', 'length'}:
                                metadata['finish_reason'] = item['done_reason']
                        if text and isinstance(text, str):
                            meter.output(text)
                            yield text
                        if not vllm and complete:
                            break
            if not complete:
                raise ProviderError('모델 서버의 응답이 중간에 끊겼습니다. 다시 시도해 주세요.')
        except httpx.HTTPError as error:
            raise transport_error(error) from None
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError('모델 스트림의 응답 형식이 올바르지 않습니다. 서버 종류와 API 경로를 확인해 주세요.') from None


def with_images(payload, images, profile, vllm):
    if not images:
        return payload
    if not profile.supports_vision:
        raise ProviderError('선택한 모델에 이미지 입력 지원이 설정되어 있지 않습니다.')
    history = payload['messages']
    index = next((i for i in range(len(history) - 1, -1, -1) if history[i].get('role') == 'user'), None)
    if index is None or not isinstance(history[index].get('content'), str):
        raise ProviderError('이미지와 함께 보낼 사용자 메시지를 확인하세요.')
    latest = history[index]
    encoded = [b64encode(image.data).decode('ascii') for image in images]
    if vllm:
        parts = [{'type': 'text', 'text': latest['content']}]
        parts.extend({'type': 'image_url', 'image_url': {'url': f'data:{image.mime_type};base64,{value}'}}
                     for image, value in zip(images, encoded))
        latest = latest | {'content': parts}
    else:
        latest = latest | {'images': encoded}
    return payload | {'messages': [*history[:index], latest, *history[index + 1:]]}


async def tool_reply(connection,model,history,tools,settings,*,images=()):
    """One structured agent step; tools are executed exclusively by the application host."""
    if connection.provider=='test':
        names={t['function']['name'] for t in tools}
        if 'parallel_analyze' in names:
            result = next((item for item in history if item.get('tool_name') == 'parallel_analyze'), None)
            if not result:
                return {'role': 'assistant', 'content': '', 'tool_calls': [{
                    'id': 'demo-parallel', 'type': 'function', 'function': {
                        'name': 'parallel_analyze', 'arguments': {'tasks': [
                            {'name': '핵심 내용', 'question': '질문의 핵심 내용을 정리하세요.'},
                            {'name': '확인할 사항', 'question': '추가로 확인할 사항을 정리하세요.'},
                        ]},
                    },
                }]}
            results = json.loads(result['content']).get('tasks', [])
            labels = ', '.join(item['name'] for item in results if item['status'] == 'complete')
            detail = f'완료 항목은 {labels}입니다.' if labels else '완료한 항목을 확인하지 못했습니다.'
            return {'role': 'assistant', 'content': '테스트 모드에서 병렬 분석 작업 흐름을 실행했습니다. ' + detail,
                    'tool_calls': []}
        from app.modules.workflows import artifacts
        if names & artifacts.FILE_TOOLS:
            result = next((m for m in reversed(history) if m.get('tool_name') in artifacts.FILE_TOOLS), None)
            if result:
                detail = ('테스트 파일을 생성하지 못했습니다. 작업 기록에서 원인을 확인하세요.'
                          if 'error' in json.loads(result['content']) else '테스트 모드에서 다운로드할 수 있는 예시 파일을 만들었습니다.')
                return {'role': 'assistant', 'content': detail, 'tool_calls': []}
            question = next((m['content'].lower() for m in reversed(history) if m['role'] == 'user'), '')
            sample = '테스트 모드에서 생성한 예시 문서입니다.'
            if artifacts.ARCHIVE_TOOL in names:
                name = artifacts.ARCHIVE_TOOL
                arguments = {'name': '테스트 결과', 'entries': [{'path': 'README.txt', 'content': sample}]}
            elif artifacts.DOCUMENT_TOOL in names:
                name = artifacts.DOCUMENT_TOOL
                file_format = next((fmt for fmt in ['html', 'docx', 'pptx', 'xlsx', 'csv', 'pdf', 'txt', 'md'] if fmt in question),
                                   'xlsx' if '엑셀' in question else 'md')
                arguments = {'name': '테스트 결과', 'format': file_format}
                if file_format == 'pptx':
                    arguments['slides'] = [{'title': '테스트 발표자료', 'bullets': [sample]}]
                elif file_format == 'html':
                    arguments['content'] = f'<main><h1>테스트 발표자료</h1><p>{sample}</p></main>'
                else:
                    arguments.update(content=sample, columns=['항목', '내용'],
                                     rows=[['실행', '파일 생성 검증'], ['모드', '테스트']])
            else:
                return {'role': 'assistant', 'content': 'WAV를 만들려면 도구 호출을 지원하는 실제 채팅 모델을 연결하세요.', 'tool_calls': []}
            return {'role': 'assistant', 'content': '', 'tool_calls': [
                {'id': 'demo-file', 'type': 'function', 'function': {'name': name, 'arguments': arguments}},
            ]}
        if 'repo_map' not in names:
            return {'role':'assistant','content':'테스트 모드에서 선택한 자료와 앱 연결 흐름을 확인했습니다. '+('[KS1]' if any('[KS1]' in m.get('content','') for m in history) else '')+'\n\n실제 모델이 작성한 분석은 아닙니다. 실제 연결을 선택하면 자료 검색과 도구 호출을 사용해 답변합니다.','tool_calls':[]}
        if any('[GL1]' in m.get('content','') for m in history):
            return {'role':'assistant','content':'테스트 모드에서 저장소 색인과 실제 소스 구간 조회를 완료했습니다. [GL1]\n\n실제 모델의 코드 분석 답변은 아닙니다. 실제 모델 연결로 분석 품질을 확인하세요.','tool_calls':[]}
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
    request_payload = with_images(payload, images, profile, vllm)
    with usage.measure(connection, model, payload, profile.max_output_tokens, settings.llm_timeout_seconds,
                       extra_input_tokens=len(images) * VISION_IMAGE_TOKENS) as meter:
        try:
            async with client(connection,settings.llm_timeout_seconds) as session:
                async with session.stream('POST',endpoint(connection,'chat/completions' if vllm else 'api/chat'),json=request_payload) as response:
                    require_success(response, '모델 도구 호출 요청 거부', '모델의 도구 지원과 서버 파서 설정을 확인해 주세요.')
                    meter.accepted = True
                    raw=bytearray()
                    async for part in response.aiter_bytes():
                        raw.extend(part)
                        if len(raw)>256000:raise ProviderError('모델의 도구 응답이 크기 제한을 초과했습니다.')
            data=json.loads(raw)
            meter.observe(data, vllm)
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
            meter.output(content)
            meter.output(json.dumps(normalized, ensure_ascii=False))
            return {'role':'assistant','content':content,'tool_calls':normalized}
        except httpx.HTTPError as error:
            raise transport_error(error) from None
        except (ValueError,KeyError,IndexError,TypeError,AttributeError):
            raise ProviderError('모델 도구 응답을 처리하지 못했습니다. 도구 호출을 지원하는 모델과 parser 설정을 확인하세요.') from None

async def structured_reply(connection,model,messages,schema,settings,*,images=()):
    """Bounded JSON decision; never executes tools or accepts a server URL from the model."""
    profile=connection.profile(model)
    vllm=connection.provider=='vllm'
    payload={'model':model,'messages':messages,'stream':False}
    if vllm:
        payload.update(max_tokens=min(1024,profile.max_output_tokens),temperature=0,
            response_format={'type':'json_schema','json_schema':{'name':'assistant_decision','schema':schema,'strict':True}})
    else:
        payload.update(format=schema,options={'temperature':0,'num_predict':min(1024,profile.max_output_tokens),'num_ctx':profile.context_window})
    request_payload = with_images(payload, images, profile, vllm)
    with usage.measure(connection, model, payload, min(1024, profile.max_output_tokens), settings.llm_timeout_seconds,
                       extra_input_tokens=len(images) * VISION_IMAGE_TOKENS) as meter:
        try:
            async with client(connection,settings.llm_timeout_seconds) as session:
                async with session.stream('POST',endpoint(connection,'chat/completions' if vllm else 'api/chat'),json=request_payload) as response:
                    require_success(response, '모델 구조화 요청 거부', '모델 서버의 JSON 스키마 출력 지원을 확인해 주세요.')
                    meter.accepted = True
                    raw=bytearray()
                    async for part in response.aiter_bytes():
                        raw.extend(part)
                        if len(raw)>65536:raise ProviderError('자동 판단 응답이 너무 큽니다.')
            value=json.loads(raw)
            meter.observe(value, vllm)
            message=value['choices'][0]['message'] if vllm else value['message']
            content=message.get('content','')
            if not isinstance(content,str):raise ValueError()
            meter.output(content)
            result=json.loads(content)
            if not isinstance(result,dict):raise ValueError()
            return result
        except httpx.HTTPError as error:
            raise transport_error(error) from None
        except (ValueError,KeyError,IndexError,TypeError,AttributeError):
            raise ProviderError('자동 판단 응답을 처리하지 못했습니다. 구조화 출력 설정을 확인하세요.') from None
