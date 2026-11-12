# 앱 연결과 동작 확인

[목차](README.md)

## 같은 호스트의 Docker Compose

모델 컨테이너는 공통 실행 명령으로 `llm-models` 네트워크에 들어갑니다. 저장소 루트에서 아래 Compose 오버라이드를 작성합니다. API 컨테이너는 기존 앱 네트워크와 모델 네트워크에 함께 연결됩니다. 앱에서 모델 주소는 `http://llm-chat:8000/v1`처럼 **컨테이너 이름과 내부 포트 8000**을 사용합니다.

```bash
model_network_override="$MODEL_DEPLOY_DIR/compose.models.network.yaml"
cat > "$model_network_override" <<'YAML'
services:
  api:
    networks:
      - default
      - llm-models
networks:
  llm-models:
    name: llm-models
    external: true
YAML
```

앱 이미지도 새로 반입해야 한다면 인터넷이 되는 **x86-64** 빌드 호스트의 같은 저장소 리비전에서 `python3 scripts/init_env.py`와 `docker compose build api web`을 실행합니다. `nginxinc/nginx-unprivileged:1.28-alpine`도 내려받고 `docker image save -o llm-app-images.tar llm-api:latest llm-web:latest nginxinc/nginx-unprivileged:1.28-alpine`으로 내보냅니다. SHA-256 확인 후 폐쇄망에서 `docker image load -i llm-app-images.tar`로 가져옵니다. 배포 소스와 nginx 설정은 같은 리비전을 쓰고 실제 `.env`는 폐쇄망에서 설정합니다. [앱 Compose 구성](../../compose.yaml) · [기본 Docker 배포](../../README.md)

## 서로 다른 호스트에서 연결

백엔드가 다른 서버 또는 Termux에 있다면 모델 서버의 `MODEL_BIND`를 접근을 허용할 **사설 인터페이스 IP**로 설정한 뒤 모델 컨테이너를 시작합니다. 앱 연결 주소에 그 IP와 호스트 포트 `50301` 등을 사용합니다. 예를 들어 H100 서버가 `192.168.0.20`이면 Gemma의 `base_url`은 `http://192.168.0.20:50301/v1`입니다. Docker 컨테이너의 `127.0.0.1`은 그 컨테이너 자신을 가리킵니다.

모델 호스트의 방화벽 또는 인증 프록시에서 백엔드의 접근을 허용하고 모델 포트를 외부에 개방하지 않습니다. vLLM 키는 일부 API 경로를 인증합니다. 네트워크 접근 제어로 나머지 경로도 보호합니다. 사내 HTTPS 프록시의 인증서가 사설 CA에서 발급됐다면 백엔드가 읽을 수 있게 마운트하고 `ASSISTANT_CA_BUNDLE`에 PEM 경로를 지정합니다. [vLLM 보안 범위](https://docs.vllm.ai/en/v0.28.0/usage/security/) · [Docker 포트 바인딩](https://docs.docker.com/engine/network/port-publishing/)

## 앱 설정

Compose는 저장소 루트 `.env`, 개별 실행은 `backend/.env`를 사용합니다. `REPLACE_MODEL_KEY`에는 모델 호스트의 `vllm-api.env`에 저장한 키를 입력합니다. 아래 주소는 같은 호스트의 Docker Compose 예시입니다.

```dotenv
# 실제 모델 연결을 사용합니다.
LLM_TEST_MODE=false
LLM_CONNECTIONS_JSON='[{"id":"offline-gemma","name":"내부 Gemma 4 31B","provider":"vllm","base_url":"http://llm-chat:8000/v1","model":"gemma-4-31b-it","api_key":"REPLACE_MODEL_KEY","context_window":16384,"max_output_tokens":2048,"max_concurrent":2,"supports_tools":true,"supports_vision":true}]'
```

선택 기능도 실행한 서비스만 설정합니다. 임베딩이 없으면 `KNOWLEDGE_EMBEDDING_PROVIDER=disabled`로 두고, 리랭커·이미지·오디오 기능을 쓰지 않으면 해당 URL은 비워 둡니다.

```dotenv
# 임베딩과 재정렬은 /v1까지 입력합니다. 앱이 /embeddings와 /rerank를 붙입니다.
KNOWLEDGE_EMBEDDING_PROVIDER=vllm
KNOWLEDGE_EMBEDDING_URL=http://llm-embedding:8000/v1
KNOWLEDGE_EMBEDDING_MODEL=bge-m3
KNOWLEDGE_EMBEDDING_KEY=REPLACE_MODEL_KEY
KNOWLEDGE_VECTOR_STORE=sqlite
KNOWLEDGE_RERANK_URL=http://llm-rerank:8000/v1
KNOWLEDGE_RERANK_MODEL=bge-reranker-v2-m3
KNOWLEDGE_RERANK_KEY=REPLACE_MODEL_KEY

# 이미지와 WAV는 전체 API 경로를 입력합니다.
IMAGE_GENERATION_URL=http://llm-image:8000/v1/images/generations
IMAGE_GENERATION_MODEL=
IMAGE_GENERATION_API_KEY=REPLACE_MODEL_KEY
# Klein 하나로 생성·편집을 하면 편집 호스트도 llm-image로 지정합니다.
IMAGE_EDIT_URL=http://llm-image-edit:8000/v1/images/edits
IMAGE_EDIT_MODEL=
IMAGE_EDIT_API_KEY=REPLACE_MODEL_KEY
AUDIO_SPEECH_URL=http://llm-speech:8000/v1/audio/speech
AUDIO_SPEECH_MODEL=
AUDIO_SPEECH_VOICE=default
AUDIO_SPEECH_API_KEY=REPLACE_MODEL_KEY
AUDIO_GENERATION_URL=http://llm-sound:8000/v1/audio/generate
AUDIO_GENERATION_MODEL=
AUDIO_GENERATION_API_KEY=REPLACE_MODEL_KEY
```

서버가 분리된 환경에서는 다음 예시로 호스트 부분을 바꿉니다. 앱 설정에서 채팅·임베딩·재정렬은 base URL, 이미지·WAV는 전체 엔드포인트입니다.

| 연결 | H100 서버가 `192.168.0.20`일 때 예시 |
| --- | --- |
| Gemma 채팅·비전 `base_url` | `http://192.168.0.20:50301/v1` |
| `KNOWLEDGE_EMBEDDING_URL` | `http://192.168.0.20:50303/v1` |
| `KNOWLEDGE_RERANK_URL` | `http://192.168.0.20:50304/v1` |
| `IMAGE_GENERATION_URL` | `http://192.168.0.20:50305/v1/images/generations` |
| `IMAGE_EDIT_URL` | `http://192.168.0.20:50306/v1/images/edits` 또는 Klein의 `50305` |
| `AUDIO_SPEECH_URL` | `http://192.168.0.20:50307/v1/audio/speech` |
| `AUDIO_GENERATION_URL` | `http://192.168.0.20:50308/v1/audio/generate` |

같은 호스트 Compose 예제는 아래 명령으로 앱을 시작합니다. 이미 떠 있는 Compose는 사용하던 파일 구성을 유지하면서 모델 네트워크 오버라이드를 추가합니다. 개별 실행에서는 설정 저장 후 백엔드를 재시작합니다.

```bash
docker compose -f compose.yaml -f "$model_network_override" \
  up -d --no-build --pull never
```

## 선택 Qdrant

Qdrant를 사용할 때는 [반입 단계](01-offline-transfer.md#외부망-준비)에서 `qdrant/qdrant:v1.20.0`도 저장합니다. 루트 `.env`에 `KNOWLEDGE_QDRANT_KEY`를 실제 비밀값으로 설정하고, 벡터화할 임베딩 서버를 먼저 확인합니다. 아래 기존 Compose 오버라이드가 `http://qdrant:6333`을 내부 주소로 지정하며, 저장 데이터는 `knowledge-vectors` 볼륨에 둡니다. [Qdrant Compose](../../compose.knowledge.yaml)

```bash
docker compose -f compose.yaml -f "$model_network_override" -f compose.knowledge.yaml \
  up -d --no-build --pull never
```

앱과 모델 서버가 서로 다른 호스트라면 Qdrant는 앱 호스트에서 시작할 수 있습니다. 이때 모델 네트워크 오버라이드를 제외하고 `compose.yaml`과 `compose.knowledge.yaml`을 사용합니다. 저장소 또는 임베딩 설정을 변경한 뒤 기존 문서를 재색인합니다.

## 동작 확인

1. 각 모델 호스트에서 역할별 `curl` 시험을 실행합니다. 모델 호스트에서 성공하고 앱에서 실패하면 API 컨테이너 또는 개별 실행 환경에서 같은 주소에 접근할 수 있는지 확인합니다.
2. **모델 관리 → 연결 확인**에서 Gemma 연결을 검사합니다. 일반 채팅을 보낸 뒤 샘플 PDF를 첨부하고 “핵심 내용을 PPTX 3장으로 정리해 줘”라고 요청합니다. 파일을 내려받아 슬라이드가 열리는지 확인합니다.
3. 임베딩을 켰다면 샘플 문서 색인 완료 후 원문과 다른 표현으로 질문하고 출처를 엽니다. 리랭커 연결을 켠 경우 위 [재정렬 응답 시험](03-search.md#bge-reranker-v2-m3)도 확인합니다.
4. 같은 Gemma 연결에서 스크린샷을 분석합니다. 이미지 생성·편집을 연결했다면 운영 화면의 이미지 서버 진단과 채팅 생성을 확인합니다.
5. WAV를 연결했다면 “한국어 한 문장을 WAV로 읽어 줘”와 “5초 파도 효과음을 WAV로 만들어 줘”를 해당 기능별로 보내고 재생과 다운로드를 확인합니다.

| 증상 | 먼저 확인할 내용 |
| --- | --- |
| `Connection refused` | `docker ps -a`, `docker logs --tail 80 llm-chat`으로 모델 로딩과 종료 사유를 확인합니다. `50301`은 호스트 포트이고 같은 브리지의 모델 컨테이너 포트는 `8000`입니다. |
| `401` 또는 `403` | 서버 키와 해당 연결의 `api_key` 또는 기능별 `*_KEY` 값을 대조합니다. 사설망 프록시가 인증 헤더를 전달하는지도 확인합니다. |
| `404` 또는 모델을 찾을 수 없음 | `/v1/models`의 ID와 `--served-model-name`을 대조합니다. 임베딩·리랭커의 base URL에 경로를 두 번 붙이지 않았는지 확인합니다. |
| `LocalEntryNotFoundError` 또는 템플릿 읽기 오류 | 모델 스냅샷 전체, 보조 가중치, 마운트 읽기 권한, `/offline-assets/tool_chat_template_gemma4.jinja`를 확인합니다. |
| CUDA OOM 또는 KV 캐시 부족 | 다른 컨테이너의 GPU 점유를 확인합니다. 비어 있는 GPU에서 먼저 시험하고 `--max-model-len`·`--max-num-seqs`를 조정합니다. 앱의 문맥 한도도 함께 맞춥니다. |
| 도구 호출 없이 텍스트 답변만 생성 | `--tool-call-parser gemma4`와 해당 버전의 Jinja 템플릿, 앱의 `supports_tools`, 실제 `tool_calls` 응답을 확인합니다. |
| 이미지·WAV 응답 시간 초과 | 모델 서버부터 직접 측정합니다. `IMAGE_GENERATION_TIMEOUT_SECONDS` 또는 `AUDIO_GENERATION_TIMEOUT_SECONDS`, `TERMINAL_AGENT_TIMEOUT_SECONDS`, 프론트엔드 `LLM_REQUEST_TIMEOUT_MS`를 작업 시간에 맞춥니다. |

실서비스를 옮기기 전에 각 컨테이너의 이미지 ID와 모델 리비전, 모델별 부팅 로그, API 시험 결과를 남깁니다. 이후 교체는 별도 모델 디렉터리와 포트에서 먼저 시험한 뒤 앱 주소를 바꾸면 이전 버전으로 되돌리기 쉽습니다.
