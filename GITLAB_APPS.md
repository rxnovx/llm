# GitLab 앱 연결

프로젝트 분석·터미널 확장에서는 `read_repository`를 추가로 요청하므로 GitLab OAuth 앱에 `read_user read_api read_repository`를 등록한 뒤 기존 연결을 재인증하세요. 파일 조회 모드 외에 프로젝트 자동 탐색·분석·실행 모드를 제공하며 상세 구성과 현재 범위는 [TERMINAL_MCP.md](TERMINAL_MCP.md)를 참고하세요. 아래의 파일 조회 설명은 기존 파일 모드에 해당합니다.

앱 설정에서 각 사용자가 자기 GitLab 계정을 OAuth로 연결합니다. 입력창의 **+ → GitLab**을 고르면 `@gitlab`이 들어갑니다. **+ → GitLab 범위 직접 선택**에서는 연결·프로젝트·브랜치와 텍스트 파일 최대 5개를 지정할 수 있습니다. 파일 조회는 발췌 자료와 질문을 모델에 전달하며 도구 호출이 필요하지 않습니다.

GitLab 주소만 등록하면 모든 사용자에게 공유되는 토큰이 생기는 구조가 아닙니다. 서버의 OAuth 앱 등록과 각 사용자의 GitLab 승인이 모두 필요합니다. GitLab 연결은 이 서비스에 로그인하는 기능과도 별개입니다.

## 1. GitLab에서 OAuth 앱 등록

대상 GitLab의 Applications 설정에서 다음 애플리케이션을 등록합니다. Self-Managed GitLab에서도 같은 OAuth API를 사용합니다.

| 항목 | 값 |
| --- | --- |
| Name | 조직에서 식별할 수 있는 이름, 예: LLM |
| Redirect URI | `https://llm.example.internal/apps/gitlab/callback` |
| Confidential | 사용 |
| Scopes | `read_user`, `read_api`, `read_repository` 선택 |

로컬 Termux에서는 예를 들어 `http://localhost:3000/apps/gitlab/callback`을 등록합니다. GitLab 인증 후 돌아오는 브라우저에서 해당 주소에 접근할 수 있어야 합니다. 등록된 redirect URI와 백엔드 `FRONTEND_URL + /apps/gitlab/callback`이 정확히 같아야 합니다.

**같은 브라우저 주소를 계속 사용하세요.** `localhost`, `127.0.0.1`, LAN IP, HTTP/HTTPS는 쿠키가 공유되지 않을 수 있습니다. 프론트엔드 `APP_URL`과 백엔드 `FRONTEND_URL`을 같은 origin으로 맞추고, 그 주소에서 연결을 시작합니다. `APP_ALLOWED_ORIGINS`의 추가 주소로 일반 채팅을 하더라도 OAuth 연결 시작은 `APP_URL`에서 진행하도록 안내합니다.

발급된 Application ID와 Secret은 서버 환경 변수에만 넣습니다. GitLab 사용자별 PAT를 채팅에 입력할 필요는 없습니다.

## 2. 서버 환경 설정

개별 실행은 `backend/.env`, Compose는 루트 `.env`에 설정합니다. 아래 값은 형식 예제이며 실제 인증 정보가 아닙니다.

```dotenv
GITLAB_INSTANCES_JSON='[{"id":"office","name":"사내 GitLab","base_url":"https://gitlab.example.internal","client_id":"발급받은-application-id","secret_ref":"office"}]'
GITLAB_CLIENT_SECRETS_JSON='{"office":"발급받은-client-secret"}'
INTEGRATIONS_ENCRYPTION_KEYS='["별도로-생성한-Fernet-키"]'
GITLAB_TIMEOUT_SECONDS=10
GITLAB_CA_BUNDLE=
```

백엔드 `FRONTEND_URL`, 프론트엔드 `APP_URL`은 실제 브라우저 origin으로 지정합니다. 개발용 `AUTH_COOKIE_SECURE=false`, HTTPS 운영은 `AUTH_COOKIE_SECURE=true`를 사용합니다. OAuth client secret과 암호화 키는 `frontend/.env.local`이나 `NEXT_PUBLIC_*`에 넣지 않습니다.

`python scripts/init_env.py --standalone` 또는 `--compose`는 새 파일에 암호화 키도 생성합니다. 기존 파일을 덮어쓰지 않으므로 이미 설정파일이 있다면 키를 별도로 생성해 추가합니다. GitLab 선택 의존성을 설치한 가상환경에서 다음 명령을 사용할 수 있습니다. 출력값은 로컬 설정파일에만 보관하세요.

```bash
python -c 'from cryptography.fernet import Fernet; import json; print(json.dumps([Fernet.generate_key().decode()]))'
```

개별 실행에서 GitLab 기능을 사용하려면:

```bash
cd backend
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

다른 터미널에서 `cd frontend && npm run dev`로 실행합니다. GitLab 연동은 기본 API 의존성에 포함됩니다. Compose는 `docker compose up -d --build`로 배포합니다. 환경 변수를 바꾼 뒤에는 각 서비스를 재시작합니다.

폐쇄망에서는 **사용자 브라우저 → 앱과 GitLab**, **백엔드 → GitLab**, **백엔드 → 모델 서버** 경로가 필요합니다. GitLab 또는 모델 서버가 외부 인터넷에 공개될 필요는 없습니다. 사설 CA는 API 프로세스가 읽을 수 있는 PEM 묶음을 `GITLAB_CA_BUNDLE`에 지정합니다. Compose에서는 해당 파일을 별도로 읽기 전용 마운트하고 컨테이너 내부 경로를 지정합니다. TLS 검증을 끄는 옵션은 제공하지 않습니다. 운영 모드의 GitLab base URL은 HTTPS여야 합니다.

Termux의 cryptography `PyType_Type` 오류는 환경 변수 주소와 별개인 Python 네이티브 모듈 문제입니다. [TERMUX.md](TERMUX.md)의 현재 Python 버전에 맞는 `LD_PRELOAD` 방법을 참고하세요. 실제 Android 단말과 사용자 GitLab에서의 인증은 이 PR의 자동 검증 대상이 아닙니다.

## 3. 사용 흐름

1. 앱에 로그인한 뒤 **설정 → 앱 연결 → GitLab 연결**을 누릅니다.
2. 인증 창에서 GitLab에 로그인하고 읽기 권한을 승인합니다. 완료하면 원래 채팅으로 돌아옵니다.
3. 채팅에 `@gitlab`을 입력하거나 **+ → GitLab**을 선택합니다. `@`를 입력해 열린 목록에서는 Enter/Tab으로 앱을 선택할 수 있습니다.
4. 프로젝트 경로 또는 MR 링크와 함께 요청을 전송합니다. 같은 대화에서 이미 GitLab을 사용했다면 직전 GitLab 작업 범위를 이어서 사용할 수 있습니다.
5. 파일을 직접 고를 때는 **GitLab 범위 직접 선택 → 선택한 파일 조회**에서 파일을 선택합니다. 답변 아래에 파일 경로·발췌 줄·고정된 커밋 링크가 표시됩니다.

**다시 생성**은 저장된 범위와 현재 접근 권한을 사용하며 원격 게시는 새 메시지로 요청합니다. 실패한 재생성은 기존 완료 답변과 출처를 보존합니다.

인증은 별도 창을 우선 사용해 채팅을 유지합니다. 팝업이 차단되면 전체 페이지 이동을 사용하며, 작성 중인 질문만 현재 사용자·대화에 묶어 `sessionStorage`에 10분 동안 보관한 뒤 한 번 복원합니다. 브라우저 저장소가 차단되면 이 복원은 보장되지 않습니다. 전체 이동은 진행 중인 스트리밍을 중단할 수 있습니다.

설정에서 **연결 해제**하면 앱의 토큰을 즉시 삭제하고 GitLab 원격 철회도 시도합니다. 서버에 접근할 수 없거나 등록 설정이 이미 제거되었다면 로컬 해제와 원격 철회 미확인을 구분해서 표시합니다. 이때 GitLab의 승인된 애플리케이션에서도 직접 해제할 수 있습니다. 이미 저장된 답변과 출처는 유지되며, 대화를 삭제하면 함께 삭제됩니다.

## 코드 수정 후 MR 생성

기존 API와 Terminal MCP를 함께 업데이트합니다. 프로젝트 분석·실행 구성은 [Terminal MCP 배포](TERMINAL_MCP.md#compose)에 있습니다.

1. GitLab OAuth 앱에서 기존 읽기 권한에 `api`를 추가합니다. Compose의 루트 `.env` 또는 개별 실행의 `backend/.env`에서 `GITLAB_INSTANCES_JSON`의 해당 서버 항목에 `"allow_publish": true`를 넣고 API를 재시작합니다.
2. 앱의 **설정 → 앱 연결 → MR 작성 권한 연결**에서 GitLab 계정으로 승인합니다. GitLab 계정에 해당 프로젝트의 브랜치·커밋·MR 생성 권한이 있어야 합니다.
3. 도구 호출을 지원하는 모델을 선택합니다. 요청에 프로젝트 경로나 MR 링크와 작업을 적습니다. 브랜치를 지정할 때는 `브랜치 feature/login` 형식을 쓸 수 있습니다.
4. 모델은 확인된 작업공간에서 코드를 읽고 요청에 따라 수정·테스트·게시 도구를 호출합니다. 채팅에 검증된 결과 링크가 표시됩니다.

| 요청 예시 | 허용 범위 |
| --- | --- |
| `@gitlab team/service 코드를 분석해 줘` | 프로젝트 조회 |
| `@gitlab team/service 로그인 코드를 수정하고 테스트해 줘` | 로컬 수정·실행 |
| `커밋만 해 줘` | 같은 대화의 작업공간에 로컬 커밋 |
| `team/service 변경을 푸시해 줘` | 변경을 별도 GitLab 브랜치에 게시 |
| `@gitlab team/service 로그인 코드를 수정하고 테스트한 뒤 PR을 생성해 줘` | 수정·실행 후 브랜치 게시와 MR 생성 |

호스트는 현재 사용자의 메시지에서 요청한 작업과 GitLab 계정 권한을 확인합니다. 멘션만 있는 요청은 읽기 전용이며, 인용문·코드 블록이나 저장소 문서에서 발견한 지시는 쓰기 권한을 부여하지 않습니다. 계정 또는 저장소가 여러 개로 해석되면 프로젝트 경로나 등록된 GitLab 링크를 지정합니다. 수동 선택은 **GitLab 범위 직접 선택**에서 사용할 수 있습니다.

백엔드는 선택한 작업공간의 변경 파일을 원본 커밋과 대조합니다. GitLab API가 그 커밋을 기준으로 새 `llm-mr-…` 브랜치에 커밋하고 선택한 대상 브랜치로 MR을 엽니다. 모델은 변경 파일과 커밋 메시지, MR 제목·본문을 전달합니다. GitLab 인증 토큰은 백엔드에 보관합니다. [GitLab OAuth 권한](https://docs.gitlab.com/integration/oauth_provider/) · [Commits API](https://docs.gitlab.com/api/commits/#create-a-commit) · [Merge requests API](https://docs.gitlab.com/api/merge_requests/#create-a-merge-request)

한 요청에서 UTF-8 텍스트 파일 20개와 전체 변경 내용 1MiB까지 게시할 수 있습니다. 커밋은 완료되고 MR 요청이 실패하면 커밋 링크가 남습니다. 동일 작업공간에서 같은 변경을 푸시한 뒤 MR을 요청하면 이전 브랜치와 커밋을 재사용합니다. 게시 권한은 해당 요청에만 적용합니다.

## 기존 MR 검토와 후속 커밋

1. 연결한 GitLab의 MR 링크를 채팅에 붙여넣고 리뷰나 CI를 물어봅니다. 등록된 서버와 일치하는 연결이 하나면 리뷰 댓글, 변경 파일, MR 파이프라인과 실패 작업 로그를 읽습니다. **GitLab 범위 직접 선택 → 기존 MR 검토·후속 작업**에서 계정을 선택해 `그룹/프로젝트!27` 또는 프로젝트 선택 후 `!27`로 조회할 수도 있습니다.
2. MR 링크와 함께 수정·테스트·푸시를 요청하거나, 이전 답변의 **이 MR에서 작업**에서 권한을 선택합니다. 게시에는 OAuth `api`, `allow_publish`, 도구 호출 모델 및 Terminal MCP 실행 구성이 필요합니다.
3. “리뷰에서 지적된 반환값을 수정하고 테스트한 뒤 이 MR에 커밋해 줘”처럼 요청합니다. 채팅은 실제 조회·작업공간 준비·수정·실행·게시 단계와 검증된 MR·커밋 링크를 표시합니다. 다른 대화로 이동한 뒤 돌아와도 저장된 진행 기록을 확인할 수 있습니다.

기존 MR의 대상 프로젝트와 소스 프로젝트를 각각 확인합니다. 포크 MR이라면 사용자의 권한으로 포크의 소스 브랜치에 추가 커밋합니다. 작업 사본은 확인된 MR의 커밋에서 체크아웃하며, 게시 직전 MR·브랜치가 이동했으면 중단합니다. 업데이트·삭제에는 파일별 `last_commit_id`를 전달합니다. 게시 요청의 응답이 끊기면 소스 브랜치에서 해당 커밋을 조회합니다. 게시 요청을 보낸 뒤 중지해도 GitLab에서 반영이 확인되면 커밋 링크를 기록합니다. 쓰기 허용은 해당 요청에만 적용합니다. [Merge requests API](https://docs.gitlab.com/api/merge_requests/) · [Commits API](https://docs.gitlab.com/api/commits/#create-a-commit) · [Repository files API](https://docs.gitlab.com/api/repository_files/)

리뷰 조회는 최대 20개 스레드에서 미해결 의견을 우선해 8개 댓글을 발췌합니다. MR 변경 파일은 최대 12개, 실패 작업 로그는 최대 3개를 조회합니다. 큰 로그에는 끝부분 발췌를 사용하며 2MiB를 넘거나 권한이 없는 로그는 조회 상태를 표시합니다. 파이프라인 없음, 로그 조회 실패, 로컬 테스트 결과는 각각 구분합니다. 조회한 발췌는 선택한 모델 서버로 전송됩니다. [Discussions API](https://docs.gitlab.com/api/discussions/#list-all-merge-request-discussion-items) · [Jobs API](https://docs.gitlab.com/api/jobs/#retrieve-a-log-file-for-a-job)

## 파일 조회 범위와 제한

- 사용자 계정마다 OAuth access/refresh token을 별도로 암호화합니다. 다른 사용자나 운영자의 연결로 대신 조회하지 않습니다. `state`는 10분·일회용이고 PKCE, 별도 HttpOnly 브라우저 nonce, 앱 사용자·로그인 세션 계열·인증 버전에 묶습니다. 앱 토큰 갱신은 같은 로그인으로 인정하지만 다른 로그인이나 계정으로 연결을 완료할 수 없습니다.
- 토큰 갱신은 DB의 비교 후 갱신과 만료되는 임대로 직렬화합니다. 갱신 중 통신이 끊겨 결과를 확인할 수 없으면 다시 인증하도록 표시합니다. 연결 설정의 URL·client ID·redirect URI·scope가 바뀌면 기존 토큰을 변경된 서버로 보내지 않고 다시 연결하도록 합니다.
- 파일 조회 모드는 프로젝트 권한을 요청마다 확인하고, 한 질문의 파일을 같은 커밋 SHA에서 읽습니다. 프로젝트 전체 탐색·수정은 작업공간 모드를 사용합니다.
- 파일당 최대 128KiB, 질문당 최대 5개, 발췌 총량 최대 약 30KB와 모델 입력 예산 중 작은 쪽을 사용합니다. UTF-8 텍스트만 읽습니다. 질문 단어가 처음 등장하는 곳 주변 또는 파일 앞부분의 연속 줄을 발췌하므로 전체 파일·저장소를 확인한 결과는 아닙니다. 기존 대화는 문맥 예산에 맞춰 오래된 완전한 턴부터 제외됩니다.
- `.env`, 일반적인 인증서·키·credentials 파일, 의존성/빌드 디렉터리는 선택에서 제외합니다. 소스 코드에 직접 적힌 비밀까지 완전히 탐지하는 기능은 아닙니다. 사용자가 선택한 코드 발췌는 현재 선택한 모델 서버로 전달됩니다.
- 출처 카드의 `[GL1]` 등은 실제 읽은 자료입니다. 모델이 본문에서 그 표기를 정확히 인용하는지는 모델 응답에 달려 있습니다. 데모 모드에서도 명시적으로 선택한 GitLab 파일은 실제 조회하지만 답변은 계속 랜덤 예시이므로 코드 분석의 정확도를 검증할 수 없습니다.

암호화 키는 DB와 별도로 백업합니다. 키를 잃으면 기존 토큰을 복구할 수 없어 다시 연결해야 합니다. 키 교체는 새 키를 배열 맨 앞에 추가하고 기존 키를 보존하는 방식입니다. 기존 연결의 토큰 갱신 또는 재연결 때 새 키로 저장하며, 이전 키가 필요한 연결과 진행 중인 OAuth 요청이 없는 것을 확인한 뒤 제거합니다. 키 목록은 최대 5개입니다.

Nginx 기본 access log에서는 OAuth code가 있는 query string과 referrer를 기록하지 않습니다. 별도의 프록시나 개발 서버에서 callback URL 전체를 기록하는 설정도 점검하세요.

## API와 검증

| API | 역할 |
| --- | --- |
| `GET /api/apps/catalog` | 현재 사용자의 앱·서버·연결 목록 |
| `POST /api/apps/gitlab/{provider}/authorize` | nonce cookie와 PKCE 인증 시작 |
| `POST /api/apps/gitlab/oauth/complete` | 로그인한 사용자의 일회용 인증 완료 |
| `POST /api/apps/gitlab/merge-request/resolve` | 내 연결을 사용해 기존 MR 링크 또는 번호 확인 |
| `DELETE /api/apps/connections/{id}` | 내 연결 제거 및 원격 철회 시도 |
| `GET /api/apps/connections/{id}/projects` | 내 권한으로 프로젝트 검색·페이지 조회 |
| `GET /api/apps/connections/{id}/projects/{project}/branches` | 브랜치 페이지 조회 |
| `GET /api/apps/connections/{id}/projects/{project}/tree` | 디렉터리·파일 페이지 조회 |

브라우저는 Next.js BFF를 경유합니다. FastAPI의 같은 경로는 `/api` 접두어 없이 사용하며 Bearer 인증을 요구합니다. 채팅 POST의 선택은 `apps: [{app:"gitlab", connection_id, project_id, ref, paths}]`로 전달합니다. 새 테이블만 추가하므로 기존 SQLite/PostgreSQL 계정·대화 열을 변경하지 않습니다.

백엔드 `pytest`에 OAuth·권한·토큰 갱신·출처·제한 검증을 포함합니다. 프론트엔드는 `npm run test:apps`, `npm run test:chat`, 빌드 후 `npm run test:proxy`로 입력 선택과 BFF 계약을 확인합니다. `tests/apps.spec.ts`는 실제 앱 가입과 GitLab API 응답 대역으로 UI 흐름을 검증하는 Playwright 시나리오입니다. 실제 GitLab/OAuth 등록과 브라우저/Termux 단말 검증 여부는 [VALIDATION.md](VALIDATION.md)를 확인하세요.

## 공식 문서

- [GitLab OAuth 2.0 API: PKCE, 토큰 갱신, 철회](https://docs.gitlab.com/api/oauth2/)
- [GitLab Repository Files API](https://docs.gitlab.com/api/repository_files/)
- [GitLab Repositories API](https://docs.gitlab.com/api/repositories/)
- [cryptography Fernet와 MultiFernet](https://cryptography.io/en/latest/fernet/)
