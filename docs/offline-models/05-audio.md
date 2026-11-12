# 한국어 낭독과 음악·효과음

[목차](README.md) · [공통 실행 명령](01-offline-transfer.md#공통-실행-명령)

낭독과 음악·효과음은 서로 다른 API로 연결합니다. 두 서버 모두 `response_format`에 `wav`를 요청하면 응답 본문으로 PCM WAV 파일을 보내야 합니다. 앱은 파일당 10MiB까지 저장하고, 음악·효과음에서 길이를 요청한 경우 `audio_length`를 전달합니다. [음성 합성 API](https://docs.vllm.ai/projects/vllm-omni/en/stable/serving/speech_api/) · [오디오 생성 API](https://docs.vllm.ai/projects/vllm-omni/en/stable/serving/audio_generate_api/)

## VoxCPM2 한국어 낭독

`openbmb/VoxCPM2`는 한국어를 지원하는 Apache 2.0 모델입니다. 기본 음성 `default`로 먼저 연결합니다. 모델 폴더에는 체크포인트와 토크나이저 등 스냅샷의 보조 파일도 함께 반입합니다. [VoxCPM2 모델 카드](https://huggingface.co/openbmb/VoxCPM2) · [VoxCPM2 공식 vLLM-Omni 연결](https://voxcpm.readthedocs.io/en/latest/deployment/vllm_omni.html)

```bash
speech_gpu=3
serve_model llm-speech "$speech_gpu" 50307 "$OMNI_IMAGE" /models/voxcpm2 --omni

model_curl "http://$MODEL_BIND:50307/v1/audio/speech" \
  -H 'Content-Type: application/json' -d '{
    "input":"안녕하세요. 지금부터 사내 업무 절차를 안내하겠습니다.",
    "voice":"default","response_format":"wav"
  }' -o offline-speech.wav
```

앱에는 `AUDIO_SPEECH_URL`과 `AUDIO_SPEECH_VOICE=default`를 설정합니다. 장문 낭독은 모델의 실제 샘플레이트를 확인합니다. 예를 들어 48kHz 모노 16비트 PCM은 약 109초부터 10MiB에 도달하므로, 3분 발표 원고는 짧은 WAV 여러 개로 나누어 요청합니다.

## Stable Audio Open 짧은 음악·효과음

`stabilityai/stable-audio-open-1.0`은 영어 설명으로 효과음과 짧은 배경음을 생성합니다. 모델 카드의 길이는 최대 약 47초입니다. 가중치 다운로드는 이용 조건 확인이 필요하며, 사내 사용에 적용되는 조건은 모델 카드와 Stability AI 라이선스에서 확인합니다. 필요한 권한을 확인한 뒤 반입합니다. [Stable Audio Open 모델 카드](https://huggingface.co/stabilityai/stable-audio-open-1.0) · [Stability AI 라이선스](https://stability.ai/license)

```bash
# GPU 3을 TTS가 이미 쓰고 있다면 여유 GPU를 지정하거나 별도 시간에 단독 시험합니다.
sound_gpu=3
serve_model llm-sound "$sound_gpu" 50308 "$OMNI_IMAGE" /models/stable-audio-open-1 \
  --omni --enforce-eager --trust-remote-code

model_curl "http://$MODEL_BIND:50308/v1/audio/generate" \
  -H 'Content-Type: application/json' -d '{
    "input":"Gentle waves on a quiet beach, no voices",
    "audio_length":5,"response_format":"wav"
  }' -o offline-sound.wav
```

`--trust-remote-code`는 서빙 예제와 같은 옵션으로, 반입 검토를 마친 고정 리비전의 로컬 모델 폴더에만 적용합니다. 앱의 `AUDIO_GENERATION_URL`에는 `/v1/audio/generate`까지 입력합니다. [vLLM-Omni Stable Audio 실행 예제](https://docs.vllm.ai/projects/vllm-omni/en/stable/serving/audio_generate_api/)

## WAV 응답 확인

설치한 서비스의 출력 파일명만 인자로 넘깁니다. 표준 라이브러리로 헤더와 프레임을 확인하고 재생해 한국어 발음과 오디오 내용을 점검합니다.

```bash
python3 - offline-speech.wav offline-sound.wav <<'PY'
from pathlib import Path
import sys
import wave

for filename in sys.argv[1:]:
    path = Path(filename)
    assert 44 < path.stat().st_size <= 10 * 1024 * 1024, filename
    with wave.open(str(path), "rb") as wav:
        assert wav.getnchannels() in (1, 2), filename
        assert wav.getnframes() > 0 and wav.getcomptype() == "NONE", filename
        assert len(wav.readframes(wav.getnframes())) == (
            wav.getnframes() * wav.getsampwidth() * wav.getnchannels()
        ), filename
        print(filename, wav.getframerate(), "Hz", round(wav.getnframes() / wav.getframerate(), 2), "초")
PY
```

HTTP 응답이 성공해도 JSON이나 MP3를 돌려주는 서버는 앱의 WAV 검사에서 실패합니다. 파일이 10MiB를 넘으면 요청을 짧게 나누고, 생성 시간이 길면 [앱 시간 제한](06-app-connection.md#동작-확인)을 함께 점검합니다.
