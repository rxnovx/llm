"""Bounded, redirect-free HTTP to administrator-registered GitLab instances."""
import asyncio
import json
import ssl

import httpx

from app.core.config import get_settings


class GitlabError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


async def request(instance, method, path, *, token=None, params=None, form=None, raw=False, limit=2*1024*1024):
    settings = get_settings()
    headers = {'Accept': 'application/json', 'User-Agent': 'LLM-GitLab/1'}
    if token:
        headers['Authorization'] = 'Bearer ' + token
    try:
        verify = ssl.create_default_context(cafile=settings.gitlab_ca_bundle or None)
        async with asyncio.timeout(settings.gitlab_timeout_seconds), httpx.AsyncClient(
                timeout=settings.gitlab_timeout_seconds, verify=verify, follow_redirects=False, trust_env=False) as client:
            async with client.stream(method, instance.base_url + path, headers=headers, params=params, data=form) as response:
                if response.status_code >= 300:
                    messages = {401:'GitLab 인증이 만료되었습니다. 다시 연결해 주세요.',
                                403:'이 GitLab 자료에 접근할 권한이 없습니다.',
                                404:'GitLab 프로젝트·브랜치·파일을 찾을 수 없거나 접근 권한이 없습니다.',
                                429:'GitLab 요청 한도에 도달했습니다. 잠시 후 다시 시도하세요.'}
                    raise GitlabError(messages.get(response.status_code, 'GitLab 서버가 요청을 처리하지 못했습니다.'), response.status_code)
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > limit:
                        raise GitlabError('GitLab 자료가 조회 크기 제한을 초과했습니다.', 413)
        if raw:
            if b'\x00' in data:
                raise GitlabError('바이너리 파일은 대화에 첨부할 수 없습니다.', 422)
            return data.decode('utf-8')
        return json.loads(data)
    except GitlabError:
        raise
    except (httpx.HTTPError, TimeoutError, ValueError, UnicodeError, OSError):
        raise GitlabError('GitLab 서버 연결·인증서·응답 형식을 확인해 주세요.') from None
