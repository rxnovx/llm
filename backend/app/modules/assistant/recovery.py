"""읽기 전용 도구의 일시적 전송 실패만 한 번 복구한다."""
import asyncio

import httpx
from fastapi import HTTPException

from app.modules.chat import trace
from app.modules.integrations.transport import GitlabError


def classify(error):
    code = error.status_code if isinstance(error, HTTPException) else error.status if isinstance(error, GitlabError) else None
    if code in {401, 403}:
        return 'authorization'
    if code == 409:
        return 'conflict'
    if code in {400, 404, 413, 422}:
        return 'invalid_input'
    if isinstance(error, TimeoutError) or code == 504:
        return 'timeout'
    if isinstance(error, httpx.HTTPError) or code in {429, 502, 503}:
        return 'transport'
    return 'execution'


async def call(operation, *, name, readonly, cancelled, record):
    retried = False

    async def notice(status, detail):
        item = record('tool_retry', status, detail)
        recorder = trace.current.get()
        if recorder:
            await recorder.emit('activity', item)

    for attempt in range(2):
        if cancelled():
            from .engine import AnalysisCancelled
            raise AnalysisCancelled()
        try:
            result = await operation()
        except (HTTPException, GitlabError, httpx.HTTPError, TimeoutError) as error:
            category = classify(error)
            if not readonly or attempt or category not in {'timeout', 'transport'}:
                if retried:
                    await notice('error', f'{name} 재조회에 실패했습니다. 오류 유형: {category}')
                raise
            retried = True
            await notice('running', f'{name}에서 일시적 연결 오류가 발생했습니다. 읽기 요청을 한 번 다시 시도합니다. 오류 유형: {category}')
            await asyncio.sleep(.15)
        else:
            if retried:
                await notice('complete', f'{name} 재조회를 완료했습니다.')
            return result
