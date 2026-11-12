"""Generate WAV files through an operator-configured speech or sound provider."""
import asyncio
from io import BytesIO
import json
from typing import Literal
import wave

from fastapi import HTTPException
import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.config import Settings
from app.core.tls import assistant_tls
from . import artifacts


class AudioInput(BaseModel):
    model_config = ConfigDict(extra='forbid', str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=150)
    kind: Literal['speech', 'sound']
    input: str = Field(min_length=1, max_length=4000, description='Exact narration for speech, or a music/sound prompt for sound.')
    duration_seconds: float | None = Field(None, ge=0.25, le=120, description='Optional duration for sound generation only.')

    @model_validator(mode='after')
    def validate_duration(self):
        if self.kind == 'speech' and self.duration_seconds is not None:
            raise ValueError('낭독 길이는 원고 분량으로 조절하세요.')
        return self


def _client(settings: Settings, key: str):
    timeout = settings.audio_generation_timeout_seconds
    headers = {'Accept': 'audio/wav', 'Accept-Encoding': 'identity'}
    if key:
        headers['Authorization'] = f'Bearer {key}'
    return httpx.AsyncClient(
        timeout=httpx.Timeout(timeout, connect=min(10, timeout)),
        headers=headers, follow_redirects=False, trust_env=False, verify=assistant_tls(),
    )


def validate_wav(raw: bytes):
    try:
        if raw[:4] != b'RIFF' or raw[8:12] != b'WAVE' or int.from_bytes(raw[4:8], 'little') != len(raw) - 8:
            raise ValueError()
        with wave.open(BytesIO(raw), 'rb') as sound:
            frame_count = sound.getnframes()
            frame_size = sound.getnchannels() * sound.getsampwidth()
            rate = sound.getframerate()
            if not (frame_count > 0 and 1 <= sound.getnchannels() <= 2 and 8000 <= rate <= 192000):
                raise ValueError()
            if len(sound.readframes(frame_count + 1)) != frame_count * frame_size:
                raise ValueError()
    except (EOFError, wave.Error, ValueError):
        raise HTTPException(502, '오디오 서버가 재생 가능한 PCM WAV 파일을 반환하지 않았습니다.') from None


async def _request(settings: Settings, data: AudioInput) -> bytes:
    speech = data.kind == 'speech'
    label = '음성 합성' if speech else '음악·효과음 생성'
    endpoint = (settings.audio_speech_url if speech else settings.audio_generation_url).strip()
    if not endpoint:
        variable = 'AUDIO_SPEECH_URL' if speech else 'AUDIO_GENERATION_URL'
        raise HTTPException(503, f'{label} 서버가 설정되어 있지 않습니다. 운영자가 {variable}을 설정해야 합니다.')
    try:
        url = httpx.URL(endpoint)
        if url.scheme not in {'http', 'https'} or not url.host or url.userinfo or url.query or url.fragment:
            raise ValueError()
    except (httpx.InvalidURL, ValueError):
        raise HTTPException(503, f'{label} 서버의 HTTP(S) 엔드포인트를 확인하세요.') from None

    payload = {'input': data.input, 'response_format': 'wav'}
    model = (settings.audio_speech_model if speech else settings.audio_generation_model).strip()
    secret = settings.audio_speech_api_key if speech else settings.audio_generation_api_key
    if model:
        payload['model'] = model
    if speech and settings.audio_speech_voice.strip():
        payload['voice'] = settings.audio_speech_voice.strip()
    if data.duration_seconds is not None:
        payload['audio_length'] = data.duration_seconds

    raw = bytearray()
    try:
        async with asyncio.timeout(settings.audio_generation_timeout_seconds):
            async with _client(settings, secret.get_secret_value()) as client:
                async with client.stream('POST', url, json=payload) as response:
                    if response.status_code in {401, 403}:
                        raise HTTPException(502, f'{label} 서버 인증에 실패했습니다. API 키를 확인하세요.')
                    if response.status_code in {404, 405}:
                        raise HTTPException(502, f'{label} 서버의 API 경로를 확인하세요.')
                    if not response.is_success:
                        raise HTTPException(502, f'{label} 서버가 요청을 처리하지 못했습니다. 모델과 서버 상태를 확인하세요.')
                    if response.headers.get('content-encoding', 'identity').lower() != 'identity':
                        raise HTTPException(502, '오디오 서버에서 비압축 HTTP 응답으로 WAV를 반환해야 합니다.')
                    async for chunk in response.aiter_bytes():
                        if len(raw) + len(chunk) > artifacts.MAX_RESULT_BYTES:
                            raise HTTPException(413, 'WAV가 10MiB를 초과합니다. 더 짧은 오디오를 요청하세요.')
                        raw.extend(chunk)
    except (httpx.TimeoutException, TimeoutError):
        raise HTTPException(504, f'{label} 서버 응답 시간이 초과되었습니다. 다시 시도해 주세요.') from None
    except (httpx.HTTPError, OSError, ValueError):
        raise HTTPException(502, f'{label} 서버에 연결할 수 없습니다. 주소와 인증서를 확인하세요.') from None
    return bytes(raw)


async def create(bind, user_id, message_id, run_id, args, settings: Settings):
    try:
        data = AudioInput.model_validate(args)
    except ValueError:
        raise HTTPException(422, '오디오 파일 이름, 요청 종류와 원고 또는 설명을 확인하세요.') from None
    raw = await _request(settings, data)
    await asyncio.to_thread(validate_wav, raw)
    return await asyncio.to_thread(
        artifacts.store, bind, user_id, message_id, run_id,
        name=artifacts.file_name(data.name, 'wav'), file_format='wav', raw=raw,
        payload_json=json.dumps({'content': data.input, 'columns': [], 'rows': []}, ensure_ascii=False),
    )


def tool(settings: Settings):
    available = [kind for kind, url in (
        ('speech', settings.audio_speech_url), ('sound', settings.audio_generation_url),
    ) if url.strip()]
    return {'type': 'function', 'function': {
        'name': artifacts.AUDIO_TOOL,
        'description': 'Create a downloadable WAV. Use speech for reading or narration and sound for music or sound effects. '
        f'Configured kinds: {", ".join(available) or "none"}. Report setup errors; do not substitute a different kind.',
        'parameters': AudioInput.model_json_schema(),
    }}
