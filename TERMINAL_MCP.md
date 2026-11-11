# Terminal MCP와 프로젝트 분석

채팅에서 `@GitLab`을 선택한 뒤 작업 방식을 고릅니다.

| 방식 | 동작 |
| --- | --- |
| 선택한 파일 조회 | 기존처럼 최대 5개 파일을 발췌해 모델에 전달합니다. |
| 프로젝트 자동 분석 | GitLab 저장소 사본을 만들고 모델이 지도·검색·함수 조회·코드 읽기를 반복합니다. |
| 프로젝트 분석·실행 | 위 기능에 명령 실행과 작업 사본의 파일 수정 기능을 추가합니다. 운영자의 실행 설정과 사용자의 모드 선택이 모두 필요합니다. |

프로젝트 모드는 입력창에 유지되며 다음 질문에서도 같은 작업공간을 사용합니다. 입력창의 GitLab 선택을 제거하면 일반 대화로 돌아갑니다. 작업공간은 최초에 확인한 커밋으로 고정합니다. 브랜치 최신 상태를 다시 가져오려면 작업공간 모달에서 기존 사본을 제거하고 프로젝트를 다시 선택합니다. 이미 완료된 답변·출처는 대화에 남습니다.

## 구성

Next.js는 사용자 세션과 출처 검사를 적용하는 BFF이고, FastAPI는 MCP 호스트와 모델 도구 실행 루프를 담당합니다. `python terminal_main.py`로 실행하는 별도 서비스가 공식 Python MCP SDK 2.3.0으로 도구를 제공합니다. 실제 명령은 워커에서 수행하고, 워크스페이스는 다운로드한 저장소와 수정 파일을 보관합니다. 일반 로그인·SQLite·텍스트 채팅은 MCP 서비스 없이 계속 실행됩니다.

MCP 서비스는 Streamable HTTP와 stdio를 지원합니다. SDK가 현대 MCP 2026-07-28과 이전 프로토콜 호환 처리를 담당합니다. 현재 앱의 MCP 연결은 Streamable HTTP를 사용합니다. 이 서버는 앱이 발급한 전용 audience의 짧은 인증으로 사용하는 **비공개 실행 서비스**입니다. 임의의 외부 ChatGPT/MCP 클라이언트용 OAuth 등록·인가 서버를 제공하는 구성은 아닙니다.

API는 사용자·대화·작업공간·실행 허용 여부를 서명한 MCP 전용 자격을 발급합니다. 서버는 호출마다 작업공간·작업 소유권을 확인합니다. 웹 로그인 JWT나 GitLab 토큰을 MCP 인증으로 재사용하지 않습니다. 브라우저 BFF는 내부 credential broker 경로를 노출하지 않습니다.

## GitLab 권한과 다운로드

기본 GitLab 연결은 OAuth 앱의 **`read_user read_api read_repository`** 권한을 사용합니다. MR 작성은 서버에서 기능을 활성화하고 사용자 계정에 `api` 권한을 추가로 연결합니다. [MR 생성 설정](GITLAB_APPS.md#코드-수정-후-mr-생성)

앱이 현재 사용자의 프로젝트 접근과 커밋을 확인한 뒤 일회용·3분 만료 ticket을 생성합니다. MCP의 가져오기 기능은 고정된 내부 broker 주소에서 그 ticket을 사용해 해당 저장소의 인증을 받습니다. Git은 URL에 토큰을 넣지 않고 임시 askpass를 사용해 고정 커밋을 shallow fetch합니다. 인증 임시 파일은 가져오기가 끝나면 제거하며 실행 컨테이너에 마운트하지 않습니다. 재귀 submodule과 Git LFS 파일 다운로드는 자동 수행하지 않습니다. 사설 CA는 API와 MCP 서비스에서 접근 가능한 동일 경로에 별도로 마운트하세요.

## 개별 실행

먼저 새 환경 파일은 저장소 루트에서 `python scripts/init_env.py --standalone`으로 생성할 수 있습니다. 새 파일에는 `TERMINAL_SHARED_SECRET`도 생성합니다. 기존 파일은 보존되므로 기존 환경에는 같은 무작위 32바이트 이상 secret을 추가해야 합니다. 비밀 값은 프론트엔드 전용 파일이나 `NEXT_PUBLIC_*`에 넣지 마세요.

**MCP 서비스가 설치된 환경**에서는:

```bash
cd backend
source .venv/bin/activate
python -m pip install -r requirements-terminal.txt
```

`backend/.env` 예:

```dotenv
TERMINAL_MCP_URL=http://127.0.0.1:8010/mcp
TERMINAL_SHARED_SECRET=별도로-생성한-동일한-서버용-secret
TERMINAL_HOST=127.0.0.1
TERMINAL_PORT=8010
TERMINAL_BROKER_URL=http://127.0.0.1:8000
TERMINAL_EXECUTION_MODE=disabled
TERMINAL_EXECUTION_ENABLED=false
TERMINAL_AGENT_MAX_STEPS=12
TERMINAL_AGENT_TIMEOUT_SECONDS=600
```

세 개의 터미널에서 각각 실행합니다.

```bash
# 백엔드
cd backend
python main.py
```

```bash
# MCP 서비스
cd backend
python terminal_main.py
```

```bash
# 프론트엔드
cd frontend
npm run dev
```

`disabled`는 명령 실행을 막지만 저장소 다운로드와 코드 탐색을 제공하는 모드입니다. 개인 개발 환경에서 같은 기기의 프로세스를 실행하려면 `APP_ENV=development`, `TERMINAL_EXECUTION_MODE=trusted_local`, `TERMINAL_EXECUTION_ENABLED=true`로 명시합니다. 이 모드는 현재 OS 계정으로 명령을 실행하며 보안 격리 환경이 아닙니다. production에서는 사용할 수 없습니다. 명령 프로세스에는 앱의 전체 환경 변수를 상속하지 않습니다.

현재 명령은 파이프 기반 stdout/stderr/stdin을 지원합니다. `terminal_input`으로 입력과 EOF를 보낼 수 있습니다. PTY 전용 프로그램의 `tty=true`는 지원하지 않으며 오류로 안내합니다. 명령마다 지정한 `cwd`에서 시작하므로 이전 명령의 `cd`는 다음 프로세스에 유지되지 않습니다. 파일 변경은 유지됩니다.

### Termux와 원격 MCP

Termux에서 앱만 실행하고 원격 Linux MCP/워커를 사용하는 구성이 가능합니다. Termux 백엔드의 기본 `requirements.txt`에 원격 MCP 클라이언트가 포함됩니다. JS/TS 파서는 분석 서버에서 설치합니다. 일반 채팅만 사용할 때는 기존 `requirements.txt`로 충분합니다. SDK 암호화 의존성에서 Termux libpython 심볼 오류가 발생하면 [TERMUX.md](TERMUX.md)의 현재 Python 버전에 맞는 방법을 확인하세요.

Termux의 `TERMINAL_MCP_URL`은 도달 가능한 Linux MCP 주소, Linux MCP의 `TERMINAL_BROKER_URL`은 도달 가능한 Termux/API 주소로 지정합니다. 양방향 네트워크 경로가 필요합니다. `127.0.0.1`은 각 기기 자신이므로 서로 다른 기기에서는 실제 주소나 터널을 사용합니다. 원격 HTTP 서비스 앞에는 TLS와 접근 통제를 구성하고 `TERMINAL_ALLOWED_HOSTS`에 접속 hostname을 등록합니다. MCP secret은 양쪽에 동일하게 설정합니다. 실제 Android 기기와 폐쇄망 서버 연결은 별도 확인이 필요합니다.

## Compose

기존 Compose는 그대로 사용합니다. 코드 분석 MCP를 추가할 때:

```bash
docker compose -f compose.yaml -f compose.terminal.yaml up -d --build
```

명령 실행까지 사용하려면 먼저 워커 이미지를 준비합니다. 워커는 기본적으로 네트워크를 사용하지 않으므로 프로젝트별 의존성이 필요한 경우 사전에 포함한 별도 이미지를 만들어 지정합니다. 기본 이미지는 Python 3.12, Node.js 22, Git, ripgrep, pytest를 제공합니다.

```bash
docker build -f deploy/Dockerfile.worker -t llm-terminal-worker:local .
```

루트 `.env`에 전용 호스트 디렉터리의 **절대 경로**와 Docker 소켓 그룹 ID를 지정합니다. 디렉터리는 서비스의 UID/GID 10001이 읽고 쓸 수 있게 준비합니다. socket 그룹 ID는 `stat -c '%g' /var/run/docker.sock`로 확인할 수 있습니다.

```dotenv
TERMINAL_WORKSPACE_HOST_PATH=/srv/llm-terminal
TERMINAL_DOCKER_GID=실제-socket-그룹-ID
TERMINAL_WORKER_IMAGE=llm-terminal-worker:local
```

```bash
docker compose -f compose.yaml -f compose.terminal.yaml -f compose.terminal-exec.yaml up -d --build
```

Docker 소켓은 비공개 MCP 관리자만 사용합니다. API·웹·실행 워커에는 전달하지 않습니다. 각 실행은 해당 저장소만 `/workspace`에 연결한 일회용 컨테이너에서 수행하며, non-root·read-only root filesystem·network none·capabilities 제거·PID/CPU/메모리 제한·제한된 `/tmp`를 사용합니다. 관리 서비스의 소켓 권한은 호스트 컨테이너 관리 권한이므로 운영자만 설정하고 MCP 서비스를 공개하지 마세요. Docker의 실제 보안 경계와 볼륨 quota는 배포 환경에 맞게 점검해야 합니다.

`TERMINAL_MAX_REPOSITORY_BYTES`는 clone과 작업공간 크기를 감시하는 소프트 한도입니다. 파일시스템의 하드 quota는 별도 설정입니다. 작업은 한 작업공간당 하나, 전체 기본 4개, 작업 제한 시간 기본 300초입니다. 강제 종료 시 실행 프로세스 그룹과 Docker 실행 컨테이너를 정리합니다.

## 모델 설정과 실행 흐름

모델 연결 또는 모델별 프로필에서 `supports_tools=true`를 설정합니다. 운영 대시보드의 모델 관리에서도 도구 호출 사용을 선택할 수 있습니다. 실제 모델·채팅 템플릿·서빙 서버의 도구 parser 설정을 확인한 뒤 활성화하세요. vLLM은 OpenAI-compatible tool arguments를, Ollama는 object arguments를 처리합니다. 도구 미지원 모델에서는 프로젝트 모드 요청을 거절하고 파일 조회 모드는 유지합니다.

하네스는 도구 schema를 모델에 전달하고 tool call → MCP 실행 → tool result 재입력을 반복합니다. 기본 최대 12회 모델 단계, 단계당 최대 4개 도구, 전체 기본 600초로 제한합니다. 각 추론 시에만 DB의 기존 모델 대기열에서 허가를 받고 도구 실행 중에는 반납합니다. 진행 상황은 SSE로 보내고 답변의 작업 기록에 저장합니다. 중지하면 진행 중인 요청과 추적 중인 명령을 취소합니다.

문맥 예산에는 도구 schema와 결과도 포함합니다. 예산을 넘으면 오래된 완전한 대화/도구 묶음부터 제외하고 UI 기록에 표시합니다. 최신 질문과 결과도 담기 어려우면 더 큰 문맥의 모델을 요청합니다. UTF-8 기반 추정이며 실제 tokenizer의 사용량과 같다는 보장은 없습니다.

데모 모드는 실제 모델을 호출하지 않습니다. 연결된 GitLab의 실제 작업공간에서 지도와 파일을 조회하는 정해진 도구 흐름만 실행하고 분석 정확도를 주장하지 않는 테스트 답변을 제공합니다. 데모가 자율적으로 명령을 실행하거나 코드를 수정하지는 않습니다.

## MCP 도구 계약

MCP는 연결·도구 호출 규격이며, 아래 도구 이름과 권한 정책은 이 앱이 정의한 계약입니다.

| 도구 | 주요 입력 | 결과 |
| --- | --- | --- |
| `workspace_create` | 서명된 provisioning 권한 | 사용자·대화에 묶인 작업공간 |
| `gitlab_checkout` | `workspace_id`, 일회용 `ticket` | 고정 커밋 저장소 사본; 호스트 전용 |
| `workspace_inspect` | `workspace_id` | 상태·출처·실행 가능 여부 |
| `repo_map` | `workspace_id`, `limit` | 제한된 파일 목록·언어 수·범위 표시 |
| `code_search` | 작업공간, `query`, `limit` | 제한된 텍스트 검색 후보 |
| `symbol_lookup` | 작업공간, `path` | 함수·클래스·import 범위와 문법상 호출 표현 |
| `find_references` | 작업공간, `symbol` | 텍스트상의 사용 후보; 의미 참조 분석 아님 |
| `workspace_read_file` | 작업공간, 경로, 시작·끝 줄 | 최대 300줄·24KB, 현재 내용 해시·작업 사본 여부 |
| `workspace_write_file` | 경로, 내용, `expected_hash` | 현재 해시가 일치할 때만 수정; 실행 허용 필요 |
| `workspace_diff` | 작업공간 | 다운로드 당시 소스와 현재 사본의 차이, 최대 32KB |
| `workspace_export_files` | 작업공간, 게시 파일 경로 | 백엔드가 승인한 MR 요청의 파일 원문과 삭제 여부 |
| `terminal_exec` | `argv`, `cwd`, `timeout_seconds` | `job_id`; 명령 인자는 배열로 실행 |
| `terminal_read` | 작업 ID, 로그 cursor, 최대 크기 | stdout/stderr, 다음 cursor, 종료 상태·코드·잘림 |
| `terminal_jobs` | 작업공간 | 최근 10개 명령의 상태 |
| `terminal_input` | 작업 ID, 입력 또는 EOF | 실행 중인 프로세스 입력 |
| `terminal_cancel` | 작업 ID | 소유한 실행 중지 |
| `workspace_close` | 작업공간 | 작업 중지·소스/캐시/로그 정리 |

앱은 모델에 provisioning 도구와 인증 정보를 노출하지 않고 작업공간 ID도 서버에서 주입합니다. 모델에는 탐색 도구와 실행 모드의 `workspace_write_file`, `terminal_exec`만 제공합니다. 호스트가 실행 완료까지 로그를 수집하고 취소를 처리하므로 polling·stdin 제어 도구는 모델 입력에서 제외합니다. 사용자는 작업공간 모달에서 명령 입력·조회·중지를 할 수 있으며 `terminal_input`은 실행 허용이 필요합니다. 브라우저에서는 `/api/workspaces` BFF를 경유하며 `internal/checkout`은 접근할 수 없습니다.

MR 쓰기를 허용한 요청에는 API 호스트가 새 MR용 `gitlab_open_merge_request` 또는 기존 MR용 `gitlab_update_merge_request`를 제공합니다. Terminal MCP는 지정된 파일을 읽고 GitLab API 호출은 백엔드가 수행합니다.

## 분석 범위와 수명

파일 목록은 기본 최대 10,000개, 파일당 최대 512KiB, 저장소 기본 256MiB로 제한합니다. 일반적인 생성물·의존성 폴더·환경 변수·키 파일을 제외합니다. 임의 소스에 하드코딩한 비밀을 완전히 탐지하는 기능은 아닙니다. 코드 조회는 symlink와 경로 탈출을 막고 파일을 descriptor 기준으로 엽니다.

Python은 표준 AST로 함수·클래스·import·문법상 호출 표현을 추출합니다. JS/TS/TSX는 Tree-sitter를 사용합니다. 다른 언어는 텍스트 검색과 범위 조회를 제공합니다. 캐시는 작업공간·파일·현재 내용 해시 기준이므로 로컬 수정 후 이전 심볼 결과를 재사용하지 않습니다. 완전한 LSP 의미 참조·런타임 호출 그래프·벡터 의미 검색·전체 저장소의 LLM 요약 인덱스는 이번 구현에 포함하지 않습니다.

출처에는 실제 읽은 파일·줄·고정 커밋을 기록합니다. 수정된 작업 사본은 별도 표시하고 링크는 원본 커밋임을 명시합니다. 추론이나 검색 후보를 확인된 호출 관계로 표현하지 않도록 지시합니다. 터미널 실행 중에는 동시에 코드를 읽는 것을 제한합니다.

기본 사용자당 8개 작업공간, TTL 24시간이며 새 작업공간 생성 시 만료 사본을 정리합니다. GitLab 연결 해제와 대화 삭제 시 원격 사본 정리도 시도하고 즉시 앱 접근을 차단합니다. MCP 서버가 응답하지 않으면 즉시 원격 정리를 보장하지 못하며 실행 시간 제한과 만료 정리가 적용됩니다. 앱 인증·GitLab 연결 상태를 확인하고 자료 조회 시 프로젝트 권한을 재확인합니다. 이미 시작된 명령의 로그 조회는 앱 연결 상태를 확인하며 GitLab 권한 변경의 즉각적인 실행 종료를 보장하지 않습니다.

## 검증

`requirements-dev.txt`를 설치한 뒤 전체 `pytest`를 실행합니다. `tests/test_terminal.py`는 공식 SDK의 MCP HTTP/stdio, 소유권·경로·암호화 자격·검색·심볼 캐시·명령 stdout/stdin·시간 초과·취소·출력 제한을 검증합니다. `tests/test_workspace_flow.py`는 실제 Git HTTP backend, MCP HTTP 서버와 내부 broker HTTP를 띄워 clone → 분석 → 출처 → 실행 → 중지 → 연결 해제 흐름을 확인합니다. GitLab 계정 API와 모델 추론 응답은 로컬 대역을 사용합니다. Docker/Termux/실제 모델 및 브라우저 화면 검증 여부는 [VALIDATION.md](VALIDATION.md)를 확인하세요.

공식 문서: [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/), [GitLab OAuth](https://docs.gitlab.com/api/oauth2/), [vLLM 도구 호출](https://docs.vllm.ai/en/latest/features/tool_calling/), [Ollama 도구 호출](https://docs.ollama.com/capabilities/tool-calling), [Tree-sitter](https://tree-sitter.github.io/tree-sitter/).


## 정적 코드 관계 분석

repository_index·code_definition·code_references·call_hierarchy·dependency_graph·code_context를 추가했습니다. Python AST 및 JS/TS Tree-sitter의 스코프·import 바인딩으로 관계 후보를 찾고 실제 소스 구간을 반환합니다. 동적 호출·외부 모듈·지원하지 않는 바인딩은 미해석으로 남깁니다. 정의 id는 파일·줄·한정 이름이며 내용 해시는 편집 시 캐시 무효화에 사용합니다.

자동 GitLab 분석은 기존 사용자·대화별 워크스페이스와 일회용 브로커를 사용하며 실행 권한을 요청하거나 승격하지 않습니다. 도구·범위 제한·질문 기반 연결 방법은 [AUTOMATIC_ASSISTANT.md](AUTOMATIC_ASSISTANT.md)를 참고하세요. 새 도구의 정의·참조·호출·캐시·권한 경계와 실제 Git HTTP·MCP 통신 자동 검증은 통과했습니다. 실제 사용자 모델·GitLab 계정 서버·Docker·Termux 실행은 별도 검증 대상입니다.
