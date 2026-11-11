"""Call configured image providers and store validated results in the turn's file storage."""
import asyncio
import base64
from dataclasses import dataclass
import io
import json
from typing import Literal

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
Mode = Literal['generate', 'edit']


class ImageError(HTTPException):
    def __init__(self, status_code: int, code: str, detail: str):
        super().__init__(status_code, detail)
        self.code = code


@dataclass(frozen=True)
class ImageData:
    data: bytes
    format: str

    @property
    def mime_type(self):
        return artifacts.MIME[self.format]


def _client(settings: Settings, *, mode: Mode = 'generate') -> httpx.AsyncClient:
    timeout = settings.image_generation_timeout_seconds
    headers = {'Accept-Encoding': 'identity'}
    secret = settings.image_edit_api_key if mode == 'edit' else settings.image_generation_api_key
    key = secret.get_secret_value()
    if key:
        headers['Authorization'] = f'Bearer {key}'
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
        headers=headers, follow_redirects=False, trust_env=False, verify=assistant_tls(),
    )


def validate_raster(raw: bytes) -> ImageData:
    if len(raw) > artifacts.MAX_RESULT_BYTES:
        raise ImageError(413, 'size', '이미지가 10MiB를 초과합니다.')
    try:
        with Image.open(io.BytesIO(raw), formats=list(FORMATS)) as image:
            file_format = FORMATS.get(image.format)
            if file_format is None:
                raise ValueError()
            if image.width * image.height > MAX_PIXELS or getattr(image, 'n_frames', 1) != 1:
                raise ImageError(413, 'size', '이미지는 1,600만 픽셀 이하의 단일 이미지여야 합니다.')
            image.load()
    except (Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ImageError(413, 'size', '이미지가 1,600만 픽셀을 초과합니다.') from None
    except (OSError, ValueError, SyntaxError):
        raise ImageError(422, 'response_format', '이미지 형식이 올바르지 않습니다. PNG·JPEG·WebP 파일을 확인하세요.') from None
    return ImageData(raw, file_format)


def _decode_response(response):
    try:
        items = json.loads(response)['data']
        if not isinstance(items, list) or len(items) != 1:
            raise ValueError()
        encoded = items[0]['b64_json']
        if not isinstance(encoded, str) or not encoded:
            raise ValueError()
        if len(encoded) > MAX_BASE64_LENGTH:
            raise ImageError(413, 'size', '생성 이미지가 10MiB를 초과합니다.')
        raw = base64.b64decode(encoded, validate=True)
    except (KeyError, TypeError, ValueError, RecursionError):
        raise ImageError(502, 'response_format', '이미지 서버의 b64_json 응답 형식을 확인하세요.') from None

    try:
        return validate_raster(raw)
    except ImageError as error:
        if error.code == 'response_format':
            raise ImageError(502, 'response_format', '이미지 서버가 읽을 수 없는 이미지를 반환했습니다.') from None
        raise


def _store_response(bind, user_id, message_id, run_id, response, name):
    image = _decode_response(response)
    return artifacts.store(
        bind, user_id, message_id, run_id, name=f'{name}.{image.format}', file_format=image.format,
        raw=image.data, payload_json=json.dumps({'content': '', 'columns': [], 'rows': []}),
    )


async def _request(settings: Settings, prompt: str, source: ImageData | None = None):
    prompt = prompt.strip()
    if not prompt or len(prompt) > MAX_PROMPT_CHARS:
        raise HTTPException(422, '이미지 요청을 1자 이상 4,000자 이내로 작성하세요.')
    mode = 'generate' if source is None else 'edit'
    endpoint = (settings.image_edit_url if mode == 'edit' else settings.image_generation_url).strip()
    if not endpoint:
        raise ImageError(503, 'disabled', '이미지 편집 서버가 설정되어 있지 않습니다.' if mode == 'edit'
                         else '이미지 생성 서버가 설정되어 있지 않습니다.')
    try:
        url = httpx.URL(endpoint)
        if (url.scheme not in {'http', 'https'} or not url.host or url.userinfo or url.query or url.fragment):
            raise ValueError()
    except (httpx.InvalidURL, ValueError):
        raise ImageError(503, 'configuration', '이미지 서버의 HTTP(S) 엔드포인트 설정을 확인하세요.') from None

    payload = {'prompt': prompt, 'n': 1, 'response_format': 'b64_json'}
    model = (settings.image_edit_model if mode == 'edit' else settings.image_generation_model).strip()
    if model:
        payload['model'] = model
    request_data = {'json': payload} if source is None else {
        'data': payload, 'files': {'image': (f'input.{source.format}', source.data, source.mime_type)},
    }

    response = bytearray()
    try:
        async with asyncio.timeout(settings.image_generation_timeout_seconds):
            async with (_client(settings) if source is None else _client(settings, mode='edit')) as client:
                async with client.stream('POST', url, **request_data) as upstream:
                    if upstream.status_code in {401, 403}:
                        raise ImageError(502, 'authentication', '이미지 서버 인증에 실패했습니다. API 키를 확인하세요.')
                    if upstream.status_code in {404, 405}:
                        raise ImageError(502, 'configuration', '이미지 서버에서 생성·편집 API 경로를 확인하세요.')
                    if not upstream.is_success:
                        raise ImageError(502, 'upstream', '이미지 서버가 요청을 처리하지 못했습니다. 모델과 서버 상태를 확인하세요.')
                    if upstream.headers.get('content-encoding', 'identity').lower() != 'identity':
                        raise ImageError(502, 'response_format', '이미지 서버의 비압축 JSON 응답 설정을 확인하세요.')
                    async for chunk in upstream.aiter_bytes():
                        if len(response) + len(chunk) > MAX_RESPONSE_BYTES:
                            raise ImageError(413, 'size', '이미지 서버의 응답 크기 한도를 초과했습니다.')
                        response.extend(chunk)
    except (httpx.TimeoutException, TimeoutError):
        raise ImageError(504, 'timeout', '이미지 서버 응답 시간이 초과되었습니다. 다시 시도해 주세요.') from None
    except (httpx.HTTPError, OSError, ValueError):
        raise ImageError(502, 'connection', '이미지 서버에 연결할 수 없습니다. 주소와 인증서를 확인하세요.') from None
    return response


async def generate(bind, user_id, message_id, run_id, prompt: str, settings: Settings) -> dict:
    response = await _request(settings, prompt)
    return await asyncio.to_thread(_store_response, bind, user_id, message_id, run_id, response, '생성 이미지')


async def edit(bind, user_id, message_id, run_id, prompt: str, source: ImageData, settings: Settings) -> dict:
    response = await _request(settings, prompt, source)
    return await asyncio.to_thread(_store_response, bind, user_id, message_id, run_id, response, '편집 이미지')


async def probe(settings: Settings, mode: Mode) -> dict:
    if mode == 'edit':
        with Image.new('RGB', (512, 512), '#3a5275') as sample, io.BytesIO() as output:
            sample.save(output, format='PNG')
            source = ImageData(output.getvalue(), 'png')
        response = await _request(settings, 'Change the blue background to pale green.', source)
    else:
        response = await _request(settings, 'A simple blue circle on a light gray background.')
    image = await asyncio.to_thread(_decode_response, response)
    return {'status': 'ok', 'format': image.format, 'size': len(image.data)}
