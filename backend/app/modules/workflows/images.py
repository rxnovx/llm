"""Call the configured image generator and keep the binary in the turn's file storage."""
import asyncio
import base64
import io
import json

import httpx
from fastapi import HTTPException
from PIL import Image

from app.core.config import Settings
from app.core.tls import assistant_tls
from . import artifacts

MAX_PROMPT_CHARS = 4_000
MAX_PIXELS = 16_000_000
MAX_BASE64_LENGTH = 4 * ((artifacts.MAX_RESULT_BYTES + 2) // 3)
MAX_RESPONSE_BYTES = MAX_BASE64_LENGTH + 16 * 1024
FORMATS = {'PNG': 'png', 'JPEG': 'jpg', 'WEBP': 'webp'}


def _client(settings: Settings) -> httpx.AsyncClient:
    timeout = settings.image_generation_timeout_seconds
    headers = {'Accept-Encoding': 'identity'}
    key = settings.image_generation_api_key.get_secret_value()
    if key:
        headers['Authorization'] = f'Bearer {key}'
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
        headers=headers, follow_redirects=False, trust_env=False, verify=assistant_tls(),
    )


def _store_response(bind, user_id, message_id, run_id, response):
    try:
        items = json.loads(response)['data']
        if not isinstance(items, list) or len(items) != 1:
            raise ValueError()
        encoded = items[0]['b64_json']
        if not isinstance(encoded, str) or not encoded:
            raise ValueError()
        if len(encoded) > MAX_BASE64_LENGTH:
            raise HTTPException(413, '생성 이미지가 10MiB를 초과합니다.')
        raw = base64.b64decode(encoded, validate=True)
    except (KeyError, TypeError, ValueError, RecursionError):
        raise HTTPException(502, '이미지 생성 서버의 b64_json 응답 형식을 확인하세요.') from None
    if len(raw) > artifacts.MAX_RESULT_BYTES:
        raise HTTPException(413, '생성 이미지가 10MiB를 초과합니다.')

    try:
        with Image.open(io.BytesIO(raw), formats=list(FORMATS)) as image:
            file_format = FORMATS.get(image.format)
            if file_format is None:
                raise ValueError()
            if image.width * image.height > MAX_PIXELS or getattr(image, 'n_frames', 1) != 1:
                raise HTTPException(413, '생성 이미지는 1,600만 픽셀 이하의 단일 이미지여야 합니다.')
            image.load()
    except Image.DecompressionBombError:
        raise HTTPException(413, '생성 이미지가 1,600만 픽셀을 초과합니다.') from None
    except (OSError, ValueError, SyntaxError):
        raise HTTPException(502, '생성 서버가 읽을 수 없는 이미지를 반환했습니다.') from None

    return artifacts.store(
        bind, user_id, message_id, run_id, name=f'생성 이미지.{file_format}', file_format=file_format,
        raw=raw, payload_json=json.dumps({'content': '', 'columns': [], 'rows': []}),
    )


async def generate(bind, user_id, message_id, run_id, prompt: str, settings: Settings) -> dict:
    prompt = prompt.strip()
    if not prompt or len(prompt) > MAX_PROMPT_CHARS:
        raise HTTPException(422, '이미지 생성 요청을 1자 이상 4,000자 이내로 작성하세요.')
    endpoint = settings.image_generation_url.strip()
    if not endpoint:
        raise HTTPException(503, '이미지 생성 서버가 설정되어 있지 않습니다.')
    try:
        url = httpx.URL(endpoint)
        if (url.scheme not in {'http', 'https'} or not url.host or url.userinfo or url.query or url.fragment):
            raise ValueError()
    except (httpx.InvalidURL, ValueError):
        raise HTTPException(503, '이미지 생성 서버의 HTTP(S) 엔드포인트 설정을 확인하세요.') from None

    payload = {'prompt': prompt, 'n': 1, 'response_format': 'b64_json'}
    model = settings.image_generation_model.strip()
    if model:
        payload['model'] = model

    response = bytearray()
    try:
        async with asyncio.timeout(settings.image_generation_timeout_seconds):
            async with _client(settings) as client:
                async with client.stream('POST', url, json=payload) as upstream:
                    if not upstream.is_success:
                        raise HTTPException(502, '이미지 생성 서버가 요청을 거절했습니다. 주소·모델·인증 설정을 확인하세요.')
                    if upstream.headers.get('content-encoding', 'identity').lower() != 'identity':
                        raise HTTPException(502, '이미지 생성 서버의 비압축 JSON 응답 설정을 확인하세요.')
                    async for chunk in upstream.aiter_bytes():
                        if len(response) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise HTTPException(413, '이미지 생성 서버의 응답 크기 한도를 초과했습니다.')
                        response.extend(chunk)
    except (httpx.TimeoutException, TimeoutError):
        raise HTTPException(504, '이미지 생성 서버 응답 시간이 초과되었습니다. 다시 시도해 주세요.') from None
    except (httpx.HTTPError, OSError, ValueError):
        raise HTTPException(502, '이미지 생성 서버에 연결할 수 없습니다. 주소와 인증서를 확인하세요.') from None

    return await asyncio.to_thread(_store_response, bind, user_id, message_id, run_id, response)
