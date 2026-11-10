# 모델 서버 연결

기본값은 `LLM_TEST_MODE=true`입니다. 로그인하면 서버 없이 랜덤 예시 답변을 주고받을 수 있습니다. 응답은 스트리밍되며 화면에 테스트 모드와 예시 답변을 표시합니다. 테스트 연결은 외부 모델 서버에 요청하지 않습니다.

## vLLM 연결

개별 실행은 `backend/.env`, Compose는 루트 `.env`에서 설정합니다. 주소와 모델 ID는 실제 내부 서버에 맞춥니다.

```dotenv
LLM_TEST_MODE=false
LLM_CONNECTIONS_JSON='[{"id":"office-vllm","name":"내부 vLLM","provider":"vllm","base_url":"http://192.168.0.10:8001/v1","model":"gpt-oss-120b","api_key":""}]'
LLM_TIMEOUT_SECONDS=120
LLM_MAX_TOKENS=2048
```

vLLM의 base URL은 `/v1`까지 지정합니다. 앱은 `GET /v1/models`로 모델 목록을 확인하고 `POST /v1/chat/completions`로 대화합니다. vLLM에서 제공하는 모델 ID/served model name을 사용하며, 해당 모델의 chat template이 구성되어 있어야 합니다.

## Ollama 연결

```dotenv
LLM_TEST_MODE=false
LLM_CONNECTIONS_JSON='[{"id":"office-ollama","name":"내부 Ollama","provider":"ollama","base_url":"http://192.168.0.10:11434","model":"","api_key":""}]'
```

Ollama의 base URL은 `/api` 없이 서버 루트로 지정합니다. 앱은 `GET /api/tags`와 `POST /api/chat`를 사용합니다. 모델 이름을 비워 두었다면 화면의 **모델 연결 → 선택 → 연결 확인**으로 실제 목록을 조회한 후 모델을 고릅니다. 모델 다운로드·서빙 시작·중단은 이 앱에서 수행하지 않습니다.

## 연결 추가·전환·해제

`LLM_CONNECTIONS_JSON` 배열에 연결을 추가하면 여러 vLLM/Ollama 서버를 함께 등록할 수 있습니다. `id`는 고유한 영문·숫자·밑줄·하이픈 조합이며 최대 64자입니다. `demo`는 테스트 연결의 예약 ID입니다. 서버의 API 인증이 필요한 경우 `api_key`를 지정합니다. 이 값은 브라우저에 전달되지 않습니다.

설정파일을 수정한 뒤 백엔드를 재시작하고 화면을 새로고침합니다. Compose는 컨테이너를 다시 만듭니다. 기존 파일은 `init_env.py`가 덮어쓰지 않으므로 이미 파일이 있다면 직접 항목을 추가합니다.

화면의 **모델 연결**에서 연결·모델을 선택하면 다음 답변부터 적용됩니다. **연결 해제**는 이 워크스페이스의 선택을 해제하며 추론 서버 자체를 종료하지 않습니다. 등록을 제거해도 저장된 대화 기록은 유지됩니다. 테스트 모드가 꺼져 있으면 연결 실패 시 테스트 답변으로 바뀌지 않습니다.

## 네트워크와 실행 방식

요청 경로는 브라우저 → Next.js → FastAPI → 등록된 내부 추론 서버입니다. FastAPI가 폐쇄망 모델 서버에 실제로 접근할 수 있어야 합니다. 모델 서버의 주소·인증키·응답 중 내부 오류는 브라우저에 노출하지 않습니다. 임의의 서버 URL을 클라이언트 요청으로 전달할 수 없고, 어댑터는 HTTP 리디렉션을 따르지 않습니다.

모델 접속은 시스템 HTTP 프록시를 자동 사용하지 않고 등록된 주소에 직접 연결합니다. 추론 서버를 loopback으로 지정하면 **FastAPI가 실행되는 환경**의 loopback입니다. Docker 안의 `127.0.0.1`은 호스트의 모델 서버가 아니므로 접근 가능한 내부 IP/DNS를 사용합니다. HTTPS는 인증서를 검증합니다.

기본 제한은 입력 16,000자, 한 대화 200턴, 최근 15턴과 현재 질문 범위의 최대 64,000자 문맥입니다. 실제 토큰 수는 서버·모델의 토크나이저에 따라 다릅니다. `LLM_MAX_TOKENS`는 응답 생성 상한이며 모델의 전체 context window를 설정하는 값은 아닙니다.

`LLM_TIMEOUT_SECONDS`는 한 응답의 전체 제한 시간입니다. 길게 조정하면 프론트엔드의 `LLM_REQUEST_TIMEOUT_MS`와 Nginx의 `proxy_read_timeout`도 더 길게 맞춥니다. 기본값은 각각 120초, 150초, 180초입니다. BFF와 Nginx는 답변을 버퍼링하지 않도록 구성합니다.

대화와 답변은 계정별로 SQLite/PostgreSQL에 저장됩니다. 중지·연결 실패의 부분 답변을 남기고, 동시 생성은 대화 단위로 제한합니다. 프로세스 비정상 종료 시 생성 잠금은 제한 시간 후 회수됩니다. 기존의 성공한 답변을 다시 생성하다 실패하거나 중지하면 이전 답변을 보존합니다.

## 어댑터 확장

`backend/app/modules/chat/providers.py`의 `Connection`, `list_models`, `stream_reply`가 추론 서버 인터페이스입니다. 공급자별 스트림을 공통 텍스트 조각으로 바꾼 뒤, 대화 API가 `start/delta/done/error/cancelled` SSE 이벤트로 전달합니다. 인증·대화 저장·브라우저 코드는 공급자 응답 형식을 직접 다루지 않습니다.

현재 범위는 텍스트 대화입니다. Markdown의 외부 이미지는 자동 요청하지 않고 링크로 표시합니다. 파일·이미지 입력, 도구 실행, RAG, 모델 다운로드, 실제 토큰 과금/통계는 포함하지 않습니다.

공식 프로토콜: [vLLM 온라인 서빙](https://docs.vllm.ai/en/latest/serving/online_serving/), [Ollama 대화](https://docs.ollama.com/api/chat), [Ollama 모델 목록](https://docs.ollama.com/api/tags).
