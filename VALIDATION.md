# 계정·대화·운영 기능 검증


## 사용자 대화 흐름 확장 검증 — 2026-11-11

PR #4가 병합된 main `73e39e7`(tree `056eb203bee8f58cc589ca8a1ff2636cdda5c60f`)에서 새 브랜치로 진행했습니다. 아래 표가 현재 사용자 기능 변경의 최신 결과이며, 이후 절은 기존 PR의 검증 이력입니다. GitHub Actions를 사용하지 않았습니다.

| 검증 | 결과 |
| --- | --- |
| 백엔드 전체 | `cd backend && python -m pytest -q`: **207개 통과**, 74.75초. 기존 상태 코드 폐기 예정 경고 2건 |
| 새 사용자 기능 | 신규 21개 사례: 편집 분기·버전 선택/이전 답변 보존, 불러오지 않은 본문 검색·리터럴 와일드카드, 프로젝트 자동 검색 격리·revision·이동·삭제, 원본 삭제 후 첨부/파일/앱 근거 보존, 업로드 취소/중복 파일 보호, 5개 형식의 실제 바이트·권한·미리보기, 초안 파일 비공개/삭제, 늦은 렌더러 저장 차단, 실제 SDK MCP 읽기 자동 선택/출처/실시간 권한, 신고 스냅샷/운영 처리, 요약/이어가기, 부분 답변·서버 재시작 복구·쓰기 재시도 확인 |
| 프론트엔드 Node | **72개 통과**. 실제 XHR 전송률 이벤트·한 번의 401 갱신·중지·서버 오류 미재전송, MS/결과 파일/범위 SSE 포함 |
| 정적 검사·배포 빌드 | `npm run typecheck`, `npm run lint`, `npm run build` 통과. 새 workflow BFF·모달·버전/파일/프로젝트/운영 신고 화면 포함 |
| 실제 서버 HTTP | 격리 SQLite에서 `python main.py`와 빌드된 Next를 실행하는 `scripts/smoke_standalone.py` 통과. 기존 인증·대기열·GitLab·RAG와 새 프로젝트·본문 검색·버전/분기·multipart 업로드 취소·5종 파일 미리보기/바이너리 다운로드·신고 처리·결과 도구·이어가기 확인. 새 BFF의 출처 위조 차단과 다운로드 파일명 헤더 확인 |
| PDF 렌더링 | 실제 한글 PDF 생성 후 Poppler PNG 렌더링을 열어 한글 본문·표·숫자 0 표시 확인. PDF 텍스트 추출·XLSX 재열기·CSV 수식 이스케이프는 자동 검사 |
| 환경 예시 | 루트·백엔드·프론트엔드 `.env.example` 주석 모두 한글 포함 확인. 선택 문서 생성 의존성·PDF 글꼴 경로 추가. 기존 비밀 설정은 읽거나 변경하지 않음 |
| 브라우저 한계 | 설치된 `/usr/bin/chromium`으로 재시도했으나 `socket() failed: Operation not permitted`, `ptrace: Operation not permitted`로 시작 실패. 실제 서버 준비를 HTTP로 확인한 뒤 공유 Chrome에서 `127.0.0.1:3101` 접근도 `ERR_CONNECTION_REFUSED`. 화면·모바일·IME·포커스·브라우저 다운로드 이벤트는 통과로 기록하지 않음 |

`frontend/tests/workflows.spec.ts`에 실제 계정 생성 뒤 프로젝트·질문 편집·분기·버전 비교·PDF 다운로드·다중 첨부·본문 검색 시나리오를 작성했습니다. 위 브라우저 제한 때문에 이 시나리오의 실행 통과를 주장하지 않습니다.

실제 사용자 폐쇄망 모델의 자동 도구 선택·요약/답변 품질·동시 부하, GitLab OAuth·임베딩/Qdrant·사설 CA, PostgreSQL·Docker/Compose/Nginx·Termux 실기기는 별도 환경 검증이 필요합니다. 이 환경에는 Docker CLI가 없습니다. 일반 MCP는 운영자 서비스 인증을 유지하며 임의 MCP의 개인 OAuth를 제공하지 않습니다. 사용 방법·파일/자료 한도·문맥 생략 범위는 [USER_WORKFLOWS.md](USER_WORKFLOWS.md)에 있습니다.


## 자동 판단·RAG·코드 분석 최신 검증 — 2026-11-11

PR #4의 `02114ee` 소스를 새 검증 폴더로 복원하고 원격 223개 파일의 Git blob 해시와 전체 tree 일치를 확인했습니다. 아래 결과는 복원한 코드와 작업공간 UUID 직렬화 수정에 대한 결과입니다. GitHub Actions는 사용하지 않았습니다.

| 검증 | 결과 |
| --- | --- |
| 백엔드 전체 | `python -m pytest -q`: **186개 통과**, 57.36초. 비밀번호 강도 검사의 기존 Starlette 상태 코드 상수 폐기 예정 경고 2건만 남음 |
| 추가 자동 처리·코드 분석 | 최초 대상 테스트 30개 통과. 실제 Git HTTP clone·MCP SDK/HTTP·출처·권한·재검색·보류·취소·중복 실행 방지·공유 해제·재생성 복원·정적 관계·캐시 검증 포함 |
| 작업공간 직렬화 | workspace_id에 UUID 객체를 할당하도록 수정. 자동 clone·명령 실행 흐름에서 Pydantic 직렬화 경고를 실패로 처리하는 회귀 조건 추가 |
| 프론트엔드 Node | `node --experimental-strip-types --test tests/*.test.mjs`: **69개 통과**, 프록시·SSE·CSRF·자료/앱·환경 우선순위·Markdown·WASM·모달 이동 포함 |
| 정적 검사·빌드 | `npm run typecheck`, `npm run lint`, `npm run build` 모두 통과 |
| 실제 서버 HTTP | `scripts/smoke_standalone.py` 통과. 실제 Python·빌드된 Next 서버에서 계정·스트리밍·운영·문맥/대기열·GitLab API 대역·multipart 업로드/자동 색인/RAG/인용/삭제·개인 설정 확인 |
| 브라우저 | Playwright Chromium이 `socket() failed: Operation not permitted`, `ptrace: Operation not permitted`로 시작하지 못함. 공유 Chrome도 준비된 테스트 프로세스의 localhost:3101에 `ERR_CONNECTION_REFUSED`로 접근하지 못함. 실제 화면·포커스·모바일·IME·스크린샷은 통과로 기록하지 않음 |

Python 3.12.14, Node 24.22.1을 사용했습니다. 모델 추론·GitLab 계정 API·임베딩/Qdrant/재정렬은 로컬 대역 또는 HTTP 계약으로 검사하며, 사용자의 실제 폐쇄망 서버에 접속한 결과가 아닙니다. 실제 모델의 JSON/도구 선택·답변/검색 품질·부하, 실제 GitLab OAuth·Qdrant·Docker/Compose/Nginx·PostgreSQL·사설 CA·Termux/Android는 해당 환경에서 추가 검증해야 합니다.

자동화 가능한 기본 검증은 개발 측에서 완료하고, 실제 서비스 연결과 단말에서만 확인할 수 있는 부분을 환경별 검증으로 구분합니다. 아래 기록은 이전 커밋 또는 초기 작성 당시의 이력입니다.

## 이전 검증 기록

검증일: 2026-11-10. Node.js 24.22.1, Python 3.12.14. 이 저장소의 루트 경로와 LLM 서비스 식별자로 다시 검증했습니다. GitHub Actions는 실행하지 않았습니다.

## 범용 대화·파일 RAG·MCP 앱 추가 검증

검증일: 2026-11-11 (Asia/Seoul). 기존 PR #4에 범용 기능을 처음 추가한 이전 커밋의 검증 기록입니다.

| 검증 | 결과 |
| --- | --- |
| 백엔드 전체 `python -m pytest -q` | **156개 통과**. 기존 127개 + 범용 지식·문서·임베딩·벡터·MCP·기억·조합 흐름 29개 |
| 문서·RAG | 실제 PDF/DOCX/XLSX/텍스트/Markdown/CSV/JSON/HTML 추출, 별도 추출 프로세스, 대기/오류/재색인·내용 해시 재사용·버전 교체, 대화별 첨부/만료, 개인/공유/공유 해제 권한, 검색 도중 삭제 후 재확인, 새 lease를 이전 워커가 덮어쓰지 않는 흐름 검증 |
| 모델·벡터 어댑터 | 도구 미지원 모델에도 검색 근거가 실제 입력으로 전달됨을 확인. vLLM/Ollama 임베딩 형식·정규화, SQLite dense 검색, 모델 변경 시 재색인, Qdrant 생성/upsert/현재 문서 ID 필터/삭제 정리 및 rerank 형식은 HTTP 계약 대역으로 검증 |
| 공통 MCP | 공식 SDK 기반 실제 HTTP MCP 서버 2개로 같은 도구명 구분, 선택·사용자/현재 권한·쓰기 허용·서비스 키 헤더 분리, 숨겨진 도구 미노출과 외부 schema 참조 차단 검증. 실제 Git HTTP/Terminal MCP와 일반 지식의 동시 사용도 확인 |
| 맞춤 설정·기억 | 사용자별 목록·입력 격리, revision 충돌, 명시적 저장/삭제/사용 토글, 실제 모델 입력에 적용되는 지침·기억 검증 |
| 프론트엔드 Node | **68개 통과**. multipart 업로드의 boundary 유지·인증/출처 검사·쿠키 비전달, 지식/운영 BFF 경로/크기 제한, 문서 출처 SSE 추가 |
| 타입·린트·빌드 | 모두 통과. 새 파일·지식·앱·기억·운영 모달과 BFF 포함 |
| 실제 별도 실행 smoke | Python main.py + 빌드한 Next.js + 격리 SQLite에서 업로드 → 자동 워커/문서 추출 → RAG SSE → 출처 저장·원문 조회 → 삭제·개인 설정 통과. 기존 계정·운영·채팅·GitLab OAuth HTTP 흐름도 통과 |
| 전용 워커 | 새 SQLite DB에서 knowledge_main.py가 스키마를 만들고 실행되는 것 확인 |
| Compose | compose.knowledge.yaml YAML 파싱·서비스/이미지 연결 확인. Qdrant v1.20.0 릴리스 메타데이터 확인. Docker 실행 파일이 없어 실제 이미지·Compose 실행은 미검증 |

`tests/knowledge.spec.ts`에 실제 업로드·색인 완료·질문 초안 유지·출처 모달·맞춤 설정 시나리오를 추가했습니다. 실행을 시도했으나 Chromium이 `socket() failed: Operation not permitted`, `ptrace: Operation not permitted`로 시작하지 못했습니다. 화면·포커스·모바일/IME·스크린샷은 미검증입니다.

실제 사용자 임베딩·reranker·Qdrant·폐쇄망 vLLM/Ollama의 검색/도구 선택 품질과 부하, Docker·Termux·사설 CA 배포는 미검증입니다. 기본 SQLite는 명시적으로 키워드/BM25 검색이며 임베딩 설정 시에만 의미 검색을 결합합니다. 가짜 임베딩을 운영 검색으로 제공하지 않습니다. OCR/멀티미디어, 웹 자동 수집, 범용 MCP 개인 OAuth는 포함하지 않습니다. 설치·현재 제한·MCP 세션 격리는 [ASSISTANT.md](ASSISTANT.md)에 정리했습니다. GitHub Actions는 실행하지 않았습니다.

## Terminal MCP·프로젝트 분석 추가 검증

PR #4의 GitLab OAuth 구성에 Terminal MCP, 사용자·대화별 저장소, 모델 도구 호출 루프, 작업공간 모달을 추가했습니다. 아래 기록은 이전 GitLab/계정 검증 표보다 최신입니다.

| 검증 | 결과 |
| --- | --- |
| 백엔드 전체 `python -m pytest -q` | 127개 통과. 기존 104개와 terminal/agent/실제 Git HTTP·MCP·broker 흐름 23개 포함 |
| Terminal MCP | 공식 Python SDK 2.3.0 클라이언트로 현대 프로토콜의 HTTP·stdio 도구 조회/호출, 전용 audience/만료·소유권·경로·symlink·UTF-8·Python AST·JS/TS Tree-sitter·내용 해시 캐시 검증 |
| 실행 워커 | 개인 개발용 프로세스에서 stdout/stderr/stdin, 한글 출력, 앱 비밀 환경 변수 미상속, 종료 코드, 시간 초과, 중지, 출력 제한 검증. Docker 실행 인자의 권한·네트워크·자원 제한은 계약 검사 |
| 통합 분석 흐름 | 로컬 git-http-backend로 실제 shallow fetch, 실제 MCP HTTP 서버와 내부 broker HTTP를 연결해 작업공간 생성·재사용, 코드·출처 저장, 도구 추론 재호출, 실행 중 GPU 허가 반납, 취소 시 프로세스 정리, GitLab 연결 해제 시 사본 정리 검증. GitLab 계정 API와 모델 응답은 대역 사용 |
| 프론트엔드 Node 테스트 | 64개 통과. 새 workspace BFF 출처·본문 크기·내부 broker 차단과 작업공간/activity SSE 이벤트 포함 |
| 프론트엔드 타입·린트·빌드 | 통과. 프로젝트 모드·작업 기록·작업공간 모달·모델 도구 지원 설정 포함 |
| 기존 별도 실행 smoke | 실제 Python main.py와 빌드한 Next.js로 계정·대화·운영·GitLab OAuth/조회·출처·해제 HTTP 흐름 통과 |

Chromium으로 `tests/apps.spec.ts`를 실행했으나 `process_singleton_posix.cc: socket() failed: Operation not permitted`, `ptrace: Operation not permitted`로 브라우저 시작이 차단되었습니다. `tests/workspaces.spec.ts`도 추가했지만 화면·팝업·포커스·모바일/IME와 스크린샷은 미검증입니다.

이 환경에는 Docker CLI/daemon이 없어 이미지 빌드·Compose 실행·실제 컨테이너 격리는 미검증입니다. 실제 Termux, 사설 CA·폐쇄망 연결, 사용자 GitLab OAuth 및 실제 vLLM/Ollama 모델의 도구 선택 정확도도 미검증입니다. vLLM/Ollama 요청 형식은 HTTP 대역으로 검사했습니다. 분석기는 구문·텍스트 후보를 제공하며 완전한 LSP 의미 참조·런타임 호출 그래프·벡터 검색은 구현 범위에 포함하지 않습니다. 설정·모드·현재 제한은 [TERMINAL_MCP.md](TERMINAL_MCP.md)에 기록합니다.

## GitLab OAuth·앱 멘션 추가 검증

기존 PR #3을 main에 병합한 뒤 별도 `feat/gitlab-oauth-mentions-20261110` 브랜치에서 구현했습니다. 아래는 GitLab 연동을 추가한 현재 변경의 검증이며, 뒤의 표는 이전 기반 구현 기록입니다.

| 검증 | 결과 |
| --- | --- |
| 백엔드 전체 `python -m pytest -q` | 104개 통과: 기존 77개 + GitLab 27개. OAuth state/nonce/PKCE, 계정·로그인 세션 격리와 앱 토큰 회전, 중도 로그아웃, 암호화 키 교체, 동시 refresh, 401 재시도·403 권한 거절, 경로/크기/전체 시간 제한, 출처·재생성 복구·중지·연결 해제·대기열 회수 포함 |
| 프론트엔드 Node 테스트 | 총 62개: 세션 5 + BFF 20 + 환경 3 + SSE 8 + Markdown 10 + API 4 + 모달 경로 8 + WASM 1 + 앱 멘션/팝업/작성 내용 복원 3 |
| `npm run typecheck`, `npm run lint`, `npm run build` | 통과. 새 앱 설정·콜백 모달 및 BFF 경로 포함, 새 프론트엔드 의존성 없음 |
| `backend/.venv-portable/bin/python scripts/smoke_standalone.py` | 실제 `python main.py`와 빌드한 Next.js 별도 프로세스, 격리 SQLite, 로컬 HTTP GitLab/모델 응답 서버 사용. 인증 시작 → PKCE 교환 → 앱 세션 갱신 → GitLab 토큰 갱신 → 프로젝트/브랜치/파일 조회 → SSE 출처와 저장 → 연결 해제/원격 철회 통과. 기존 계정·운영·모달·채팅 검증도 함께 실행 |
| Playwright UI 실행 시도 | 기본 다운로드 브라우저가 없어 설치된 `/usr/bin/chromium`으로도 시도했으나 `process_singleton_posix.cc: socket() failed: Operation not permitted`, `ptrace: Operation not permitted`로 브라우저 시작 자체가 차단됨 |

`tests/apps.spec.ts`에 GitLab API 응답 대역을 이용하는 데스크톱/모바일용 파일 선택·출처·채팅 유지 시나리오를 추가했습니다. 실제 브라우저 UI·키보드/IME·화면 크기·팝업 복귀의 동작 및 스크린샷은 이 환경에서 검증하지 못했습니다. Node 테스트는 입력 범위와 팝업/저장소 로직을 검증하며 실제 브라우저 검증을 대체하지 않습니다.

사용자 GitLab URL·OAuth 앱 인증 정보는 제공되지 않았으므로 **실제 GitLab 서버에서의 로그인과 권한 동작은 미검증**입니다. 실제 Termux/Android, Docker 이미지 빌드·Compose 실행, Nginx 런타임, 사설 CA, PostgreSQL도 이번 검증에 포함하지 않았습니다. GitLab OAuth 암호화는 별도 `requirements-gitlab.txt`로 제공하고 Docker 이미지에는 포함합니다. 일반 SQLite·채팅의 기본 의존성은 유지했습니다. 설정은 [GITLAB_APPS.md](GITLAB_APPS.md)를 참고하세요.

## 통과

| 검증 | 결과 |
| --- | --- |
| `cd backend && python -m pytest -q` | PostgreSQL 드라이버 없는 가상환경에서 77개 통과 (계정·설정 33개 + 대화 13개 + 운영·제목 15개 + 모델 운영·문맥·대기열 16개) |
| `npm run test:session` | 5개 통과 |
| `npm run test:proxy` | 18개 통과 |
| `npm run test:env` | 실제 Next.js 환경파일 로더로 우선순위 검증 3개 통과 |
| `npm run test:chat` | UTF-8·CRLF·분할 SSE·중단·크기 제한·대기/문맥 이벤트 7개 통과 |
| `npm run test:markdown` | 실제 렌더러의 CommonMark·GFM·코드·수식·각주·스트리밍·안전한 링크 10개 통과 |
| `npm run test:api` | 인증 헤더·갱신·권한 오류·장애·스트림 재전송 금지 4개 통과 |
| `npm run test:navigation` | 모달 경로·대화 주소·인증 복귀·토큰 비복사·계정 범위 8개 통과 |
| `npm run test:portable` | Next.js WASM 바인딩으로 TypeScript 변환·실행 1개 통과 |
| `npm run typecheck` | 통과 |
| `npm run lint` | 오류·경고 없이 통과 |
| `npm run build` | Webpack 프로덕션 빌드·정적 페이지 생성 통과 |
| `NEXT_TEST_WASM=1 npm run build` | 이전 Termux 대응에서 강제 WASM 프로덕션 빌드 통과; 이번 환경파일 변경은 일반 Webpack 빌드로 재검증 |
| `backend/.venv-portable/bin/python scripts/smoke_standalone.py` | 실제 계정·대화·운영 권한·수식 폰트 + 연결 편집·요약 + HTTP 모델 대역의 대기 취소·슬롯 회수 통과 |
| `python -m pip check` | 의존성 충돌 없음 |
| SQLite Compose 구성 | 공식 Compose JSON Schema 검증 통과 |
| PostgreSQL Compose 병합 구성 | 공식 Compose JSON Schema 검증 통과 |
| 환경 설정 생성 | 공통·서비스별 파일 생성, 기존 설정 보존, 새 파일 권한 0600 확인 |
| PostgreSQL 선택 의존성 | `requirements-postgres.txt` 설치 및 psycopg 3.3.4 엔진 생성 통과; 실제 DB 연결은 미검증 |

백엔드 테스트는 중복 가입·비밀번호 강도·가입 제한·로그인 속도 제한·갱신 토큰 회전/재사용·회전 실패 롤백·로그아웃·다른 사용자의 세션 보호·비밀번호 변경·이메일 인증/변경/복구·사진 형식/크기/삭제·Google 계정 연결 정책을 확인합니다. Google 자격 증명 검증과 메일 발송은 테스트 대역을 사용합니다.

세션 테스트는 실제 `lib/auth.ts`를 격리된 탭 컨텍스트에서 실행해 갱신 단일화, 늦은 응답 무시, 장애 시 세션 만료 전파 방지, 다른 탭의 갱신과 로그아웃 순서, 실패 후 잠금 해제를 확인합니다. Web Locks·BroadcastChannel 전송 계층은 테스트 대역이며 실제 브라우저 검증을 대체하지 않습니다.

프록시 테스트는 빌드된 Next.js 서버를 실제로 실행합니다. 쿠키 속성·토큰 비노출·타임아웃·잘못된 인증 서버 응답·출처 검증·경로 제한·본문 크기·401/429/503 처리·로그아웃 실패 전달을 검증합니다. `APP_URL`이 localhost인 상태에서 루프백 IP 출처의 로그인이 403으로 거부되는 문제를 수정 전 빌드로 재현했습니다. 수정 후에는 명시적으로 허용한 두 출처가 모두 성공하고, 등록되지 않은 호스트·포트·HTTPS 주소·null 출처·위조 Host/전달 헤더·cross-site 요청은 거부됨을 확인했습니다.

추가한 백엔드 환경 설정 테스트 6개는 `--standalone`의 서비스별 파일 생성, 비밀값 생성·비노출, 파일 권한, 기존 루트·서비스 파일 보존, Compose 설정 유지, 백엔드 환경변수 우선순위를 검증합니다. 프론트엔드 환경 테스트 3개는 설치된 Next.js의 `@next/env`와 실제 `next.config.ts`로 루트 `.env` → 프론트엔드 `.env` 또는 `.env.local` → 터미널 환경변수의 우선순위를 검증합니다. Compose 포트 8080·내부 api 호스트가 서비스별 개발 설정을 덮어쓰지 않는 경우를 포함합니다.

독립 실행 통합 검증은 `python main.py`와 Next.js standalone 서버를 동시에 실행하고, 실제 FastAPI로 회원가입 → 로그인 → 프로필 변경 → 이미지 업로드/다운로드 → 갱신 → 로그아웃을 수행합니다. 테스트 DB와 업로드 경로는 임시 디렉터리로 분리합니다.

Compose 스키마 출처: `compose-spec/compose-spec`의 `schema/compose-spec.json`, 파일 SHA `44bbc2710502200b78a3e7793369a771d1ed7356`. YAML 파싱·서비스 연결·Dockerfile 경로를 함께 확인했습니다.

## Termux 대응 검증

첨부 로그의 `psycopg-binary==3.3.4` 설치 실패에 대응해 기본 SQLite 의존성에서 PostgreSQL 바이너리를 분리했습니다. `requirements-dev.txt`만 설치한 가상환경에서 `psycopg` 미설치를 확인했으며, 환경파일 추가 단계에서 백엔드 33개 테스트가 통과했고 이번 확장 후에는 77개가 통과했습니다. PostgreSQL import를 차단한 새 프로세스에서도 가입·로그인·프로필 조회가 성공하며, 시스템 시간대 DB를 비운 프로세스에서도 `tzdata`로 `Asia/Seoul`을 검증합니다.

프론트엔드는 Webpack으로 바꾸고, Next.js와 동일 버전의 SWC WASM 패키지를 고정했습니다. Android 준비 스크립트는 해당 패키지를 Next.js WASM 캐시에 연결합니다. 생성 캐시를 비운 뒤 준비 스크립트를 실행했고, 접근 불가능한 npm registry를 지정한 상태에서도 실제 Next.js WASM 로더가 TypeScript를 변환했습니다. Next.js의 강제 WASM 테스트 모드로 전체 프로덕션 빌드와 독립 실행 API 연동도 통과했습니다. 이 강제 플래그는 검증에만 사용하며 앱 실행 설정에는 넣지 않았습니다.

계정·설정·대화·운영·프론트엔드의 통과 기록은 합계 133개(백엔드 77개 + 프론트엔드 56개)입니다. 이번 모달 확장은 프론트엔드 56개를 다시 실행했으며 백엔드 코드는 변경하지 않아 직전 확장의 77개 검증 기록을 유지합니다. 이 결과는 Linux에서 확인한 설정·출처 검사·SQLite 의존성·WASM 경로 검증이며 Android ABI, Termux의 실제 패키지 빌드, 파일 감시, 휴대전화 브라우저 동작을 검증했다는 의미는 아닙니다. `LD_PRELOAD` 방식은 Termux 문서에 안내했으며 이 Linux 환경에서 해당 Android 심볼 오류의 해결을 검증한 것은 아닙니다.

## LLM 대화 검증

대화 테스트 13개는 미로그인 차단, 다른 계정의 대화 조회·변경·삭제·중지 차단, 테스트 연결의 무네트워크 응답, 테스트 모드 해제 시 자동 대체 금지, 임의 URL/키 입력 차단, 부분 응답 저장, 재생성 시 중복 메시지 방지, 동시 생성 잠금·취소 ID·만료 후 회수, 기존 정상 답변 보존을 확인합니다.

vLLM과 Ollama는 공식 프로토콜 형식의 HTTP 테스트 대역으로 검증했습니다. 실제 어댑터의 서버 인증 헤더, 모델 목록 경로, 이전 메시지 전달, 생성 옵션, 한글 UTF-8이 3바이트씩 나뉘는 스트림, 종료 표시 없는 응답·잘못된 JSON 거부를 확인했습니다. 실제 폐쇄망 GPU 서버·모델에 접속한 결과는 아닙니다.

프론트엔드의 공통 SSE 파서는 1바이트 단위 UTF-8 분할, CRLF가 조각 사이에 걸친 경우, 명시적 완료·오류·취소, 비정상 EOF, 크기 초과 시 reader 해제를 검증합니다. 실제 Next.js 프록시 검증은 첫 프레임에 완료 이벤트가 포함되지 않음을 확인해 응답 버퍼링을 검사하며, 대화 API의 출처 검사·인증 헤더 전달·쿠키 비전달·허용 경로·페이지 쿼리 전달을 확인합니다.

실제 Python + Next.js 통합 검증은 로컬 랜덤 답변의 첫 조각을 먼저 수신하고, 완료 후 SQLite 기록을 대조합니다. 두 번째 답변 도중 취소를 보내 잠금이 해제되고 부분 답변이 `cancelled`로 저장되는지 확인한 뒤, 같은 턴을 다시 생성해 메시지 중복이 없음을 검사합니다.

브라우저 테스트 시나리오 `tests/chat.spec.ts`와 기존 계정 시나리오를 함께 제공합니다. 이 환경의 앞선 브라우저 제어 시도에서는 준비된 `http://127.0.0.1:3100` 서버에도 `ERR_CONNECTION_REFUSED`가 발생해 화면을 확인하지 못했습니다. 데스크톱·모바일 레이아웃, Markdown 화면, 한글 IME, 화면 키보드와 실제 클릭 흐름은 통과로 기록하지 않습니다. 디자인은 공식 서비스 10곳의 자료를 검토해 구성했으며 판단과 자료 링크는 `DESIGN.md`에 있습니다.

## Markdown·운영·제목 검증

Markdown 테스트는 실제 `MessageContent`와 플러그인을 컴파일해 서버 렌더링 HTML을 확인합니다. 1–6단계 제목, 중첩 목록·인용·참조 링크, 표 정렬·체크리스트·각주, Python 강조·코드 복사용 텍스트, 알 수 없는 언어, 네 종류의 수식 구분자, 코드 내부 수식 변환 금지, 미완성 스트리밍 접두부, 원격 이미지 자동 로드 금지, 스크립트 URL·위험 TeX를 검사합니다. 여러 메시지에서 각주 ID가 겹치는 문제와 각주 링크가 새 탭으로 열리는 문제를 재현하고 수정했습니다. 수식 CSS에 선언된 WOFF2 자원이 앱의 HTTP 경로에서 모두 제공되는 것도 독립 실행 검증으로 확인했습니다.

운영 테스트는 미로그인 401·일반 사용자 403, DB 역할 변경의 즉시 적용, 프로필 요청을 통한 권한 상승 금지, 실제/테스트 필터·평균·성공률·7일 집계, 만료 요청 표시, 잘못된 연결 설정에서도 기본 통계 유지, 대화 내용·비밀값 비노출을 확인합니다. 재생성 실패·취소·만료 회수·대화 삭제 이후에도 요청 메타데이터가 일관되게 남는지 검증했습니다. 로컬 권한 도구는 임시 DB에서만 실행해 기존 사용자 지정·권한 해제·마지막 운영자 보호·감사 기록을 확인했습니다.

실제 Python + 새 Next.js 빌드에서도 일반 사용자 권한 거부 → 임시 계정에 로컬 CLI로 역할 지정 → 같은 로그인 세션으로 운영 조회, 요청 필터·모델 확인·사용자·감사 기록을 검증했습니다. 생성 요청 수가 3건(완료 2·취소 1)인 실제 테스트 흐름과 일치하는지 대조했습니다. 실제 사용자에게 운영자 권한을 부여하지 않았습니다.

제목 변경은 상단 버튼과 기존 사이드바 메뉴에서 같은 저장 흐름을 사용합니다. 백엔드에서는 첫 메시지 자동 제목이 사용자 지정 제목을 덮어쓰지 않는지, 공백·길이·추가 필드 검증과 소유권을 확인했습니다. 독립 실행 검증으로 제목 저장·재조회도 확인하며, 데스크톱·모바일 브라우저 시나리오는 상단에서 수정 후 새로고침해 제목이 유지되는 경로로 보완했습니다.

새 JS 의존성 26개가 추가됐으며 기존 의존성 버전은 바꾸지 않았습니다. Python 외부 의존성은 추가하지 않았습니다. 새 운영 통계 테이블은 추가 방식이며 기존 계정·대화 컬럼을 변경하지 않습니다. 브라우저에서의 실제 수식 폰트 표시, 클립보드, 모바일 표/코드 스크롤, 운영 화면 클릭, 한글 IME는 미검증입니다. 서버 HTML·HTTP 검증을 시각적 브라우저 검증으로 간주하지 않습니다.

## 모델 운영·문맥·대기열 검증

모델 운영 테스트 16개는 운영자 제한, 환경 연결의 DB 우선 설정·재시작 없는 반영, 기존 키 상속·이름 참조·키 삭제 시 제공 중단, 사용자/모델 허용 목록, 비활성화, 버전 충돌, 잘못된 설정과 비밀값 비노출을 확인합니다. 요약 메모의 소유권·범위·충돌, 전체 턴 단위 문맥 포함·제외, UTF-8 추정 표시, 예산 초과 시 메시지·허가 롤백도 검증합니다.

SQLite 파일을 공유하는 **독립 Python 프로세스 두 개**에서 허가를 동시에 요청해 한 개만 실행 상태에 들어가는지 확인했습니다. API 프로세스를 여러 개 배포한 부하 테스트는 아닙니다. 연결/모델/사용자 한도, 관련 대기열 순서, 전역·계정 대기 상한, 기한 만료 회수, 대기 중 취소·연결 비활성화·시간 초과를 검사했습니다. 첫 응답이 오기 전에 중지해도 공급자 iterator가 닫히고 허가가 반환되는지 확인했습니다.

독립 실행 통합 검증은 실제 Next.js BFF와 FastAPI, 로컬 HTTP vLLM 프로토콜 대역을 연결합니다. 첫 모델 호출을 보류한 동안 두 번째 요청의 대기 이벤트가 완료 전에 도착하고, 취소한 요청은 대역에 전달되지 않는지 검사했습니다. 첫 호출 완료 후 다음 요청이 실행되며, 요약 메모가 지정 범위의 원문을 대신하는 실제 공급자 payload와 출력 상한을 대조했습니다. 완료 2·취소 1의 실제 모드 통계도 비교했습니다. 실제 GPU/폐쇄망 모델 결과는 아닙니다.

프록시 테스트는 연결 등록·수정 본문/인증 전달·쿠키 비전달·출처 제한·256 KiB 상한, 문맥 미리보기·요약 수정 경로 제한을 확인합니다. SSE 테스트는 대기/문맥 이벤트의 UTF-8 분할과 비종료 처리, 잘못된 순서 값 거부를 추가했습니다. 이번 확장에는 JS/Python 외부 의존성을 추가하지 않았습니다.

설정 폼·문맥 다이얼로그·대기 버튼의 실제 브라우저 클릭과 모바일 레이아웃은 미검증입니다. 시스템 Chromium을 다시 실행했지만 `process_singleton_posix.cc: socket() failed: Operation not permitted`로 시작하지 못했습니다. 타입 검사·서버 통합 검증을 브라우저 검증으로 기록하지 않습니다. 모델 토큰 수는 로컬 추정치이며 자동 요약 기능은 없습니다.

## 채팅 중심 모달 검증

메인은 로그인 전후 모두 채팅으로 렌더링하고 인증·설정·운영 페이지 내용은 최상위 채팅과 나란히 배치한 공통 모달에 넣었습니다. 실제 빌드한 Next.js 서버에서 `/login`, 가입·인증·복구 4개 경로, 프로필·수정·설정 3개 경로, `/admin`의 총 9개 주소에 직접 접근해 모달 종류·채팅 입력창·배경 inert HTML이 함께 제공되는지 확인했습니다. 존재하지 않는 주소의 404와 안내 모달도 검사했습니다.

URL 테스트 8개는 앱 내 이동 시 대화 ID 보존, 현재 모달을 닫지 않는 대화 URL 갱신, 검증 토큰·이메일 비복사, 안전한 인증 복귀 주소, 외부 링크로 대화 ID 미전달, 계정/운영 데이터의 사용자 범위 초기화와 세션 변경 확인 결과의 유지 정책을 검사합니다. TypeScript·린트·Webpack 빌드가 통과했고 기존 계정·프로필·스트리밍·모델 설정·문맥·대기 취소의 실제 서버 통합 검증도 통과했습니다. 외부 패키지와 백엔드 애플리케이션 코드는 변경하지 않았습니다.

`tests/modals.spec.ts`에 비로그인 초안 → 가입 → 대화, 동일 입력 요소·초안·대화의 설정 이동 전후 유지, Tab 포커스 범위·Escape·직접 접근·닫기 시나리오를 작성했습니다. 기존 계정 브라우저 시나리오도 새 설정 항목과 DOM에 맞췄습니다. 이 브라우저 시나리오들은 실행하지 못했습니다. 이번에도 Chromium이 `process_singleton_posix.cc: socket() failed: Operation not permitted`로 종료되어 실제 렌더링·포커스 복귀·스크롤 보존·모바일 키보드·터치 동작은 미검증입니다. URL·HTML·타입 검증을 실제 UI 검증으로 기록하지 않습니다.

## 환경 제한으로 미검증

- Docker 실행 파일과 데몬이 없어 이미지 빌드·`docker compose up`·컨테이너 준비 상태·볼륨 영속성을 실제로 확인하지 못했습니다.
- Nginx 실행 파일이 없어 `nginx -t`와 실제 Nginx 경유 HTTPS를 확인하지 못했습니다.
- 시스템 Chromium 실행은 Unix 소켓 생성 권한 오류로 중단되었습니다. Playwright headless shell 설치도 유효한 ZIP을 받지 못해 실패했습니다. 데스크톱·모바일 브라우저 E2E와 화면 캡처는 통과로 기록하지 않습니다. `tests/accounts.spec.ts`와 실행 명령은 함께 제공합니다.
- 실제 PostgreSQL 서버, SMTP 메일 발송, Google 콘솔과 실제 Google 계정 로그인은 검증하지 못했습니다.
- 실제 Android/Termux 단말에서 설치·개발 서버·운영 빌드·화면 동작은 미검증입니다. Termux용 네이티브 Python/CSS 모듈은 단말에서 추가 확인이 필요합니다.

FastAPI 테스트 실행 시 상위 Starlette의 httpx 및 HTTP 422 상수에 관한 deprecation 경고 3건이 출력됐으나 테스트 실패는 없었습니다.


## 2026-11-10 추가 변경: 자동 판단·근거 점검·코드 관계 분석

**초기 등록 당시 상태: 실행 미검증.** `02114ee`를 등록할 때 작업 실행 환경이 unavailable 상태여서 Python/Node 실행·타입 검사·린트·빌드·브라우저 검증을 수행하지 못했습니다. 이후 환경 복구 후의 결과는 이 문서 상단의 최신 검증 항목에 기록했습니다. GitHub Actions는 실행하지 않았습니다.

추가한 테스트 범위:
- 수동 선택 없는 질문 → 연결된 프로젝트 조회 → 실제 Git HTTP clone·MCP 색인·출처·저장
- 연결 누락·동명 프로젝트의 대상 확인, 서버용 토큰이 판단 메타데이터에 없는지 확인
- 내 자료·공유 자료·현재 대화 첨부의 자동 범위와 만료·공유 해제
- 불확실한 초안 비노출, 검색어 재작성·재검색·재생성, 없는 인용 거부·최대 시도 후 보류
- 터미널 명령 중복 실행 방지, 실패한 재생성의 기존 답변·출처·판단 복구
- vLLM/Ollama의 구조화 출력 요청 계약
- Python import 별칭·상대 경로·중첩 스코프·파라미터 가림·클래스 참조
- TS 기본 export·재수출·상대 import·파라미터 가림, 실제 MCP 도구·권한 경계
- 내용 해시 캐시 갱신·실제 소스 문맥·파싱 오류·외부 의존성·분석 제한 표시
- 자동 workspace 선택과 최종 routing의 SSE 전달

비실행 검토에서 Python 파일의 문자열/괄호 쌍과 예시 환경 변수의 누락·중복을 확인했습니다. 이것은 Python 컴파일·타입 검사·실행 테스트를 대체하지 않습니다.

재현 명령:

```bash
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest tests/test_automatic_assistant.py tests/test_codeintel.py -q
python -m pytest -q
cd ../frontend
npm ci
npm run typecheck
npm run lint
npm run build
npm run test:chat
npm run test:session
npm run test:api
npm run test:env
npm run test:apps
npm run test:markdown
npm run test:navigation
npm run test:portable
npm run test:proxy
npm run test:browser
```

실제 연결에서는 GitLab OAuth·Terminal MCP를 준비하고 대화 도구 선택 없이 그룹/프로젝트 분석을 요청합니다. 문서를 준비한 뒤 일반 질문·사내 규정 질문·자동 검색 끄기/항상·검색 실패·공유 해제·취소를 확인합니다. 실제 모델의 JSON schema·도구 parser·근거 점검 품질과 실제 Qdrant/임베딩·Termux·Docker는 별도 검증 대상입니다.
