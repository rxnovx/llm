# 유저 기능 이관 기록

원본: `rxnovx/my-note-app`, `main`의 커밋 `385d34409a39660f7d2e1cdf85e5ce977d2a9f10`.

대상: 빈 저장소 `rxnovx/llm`. PR 기준을 위한 최소 README 초기화 커밋은 `155873ddd2893cfefbe3cf7f46c591dc33e4b37c`입니다.

프론트엔드·백엔드·Compose·Nginx·실행 스크립트는 저장소 루트에 배치합니다.

| 원본 | 이번 앱 | 처리 |
| --- | --- | --- |
| `simple-auth-platform/app/core/security.py` | `backend/app/core/security.py` | scrypt, JWT 검증, 해시 유지; PAT 생성 제외 |
| `simple-auth-platform/app/modules/auth/` | `backend/app/modules/auth/` | 계정 모델·인증·회전·메일·세션·속도 제한·감사 기록 추출 |
| `simple-auth-platform/app/modules/users/router.py` | `backend/app/modules/users/router.py` | 프로필·비밀번호·세션·Google API 유지 |
| `myfront/src/components/auth/AuthCard.tsx` | `frontend/src/components/auth/AuthCard.tsx` | 공통 폼 디자인 유지, 서비스 표시를 LLM으로 변경 |
| `myfront/src/components/user/UserAccountPage.tsx` | `frontend/src/components/account/AccountSections.tsx` | 프로필·사진·계정 보안 폼 추출, 앱별 설정 제외 |
| `myfront/src/lib/auth.ts` | `frontend/src/lib/auth.ts` | 메모리 토큰·갱신 단일화·탭 통신 유지, 체험·관리자 대리 접속 제외 |
| `myfront/src/components/auth/GoogleSignInButton.tsx` | 같은 상대 경로 | Google Identity Services 버튼 유지 |
| `myfront/src/lib/avatar-url.ts`, `UserAvatar.tsx`, `api-error.ts` | 같은 상대 경로 | 원본 재사용 |
| `myfront/src/app/api/auth/*` | `frontend/src/app/api/auth/[...path]/route.ts` | HttpOnly 갱신 쿠키와 서버 간 프록시 유지, 계정 경로로 제한 |
| `compose.yaml`, `nginx.config.file` | `compose.yaml`, `deploy/nginx.conf`, `nginx.config.file` | web/api·영속 저장·준비 확인·프록시 설정을 계정 전용으로 구성 |

사용자 흐름은 회원가입 → 로그인 → 프로필·보안 설정이며, 원본의 노트 화면으로 이동하던 경로는 `/user/profile`로 바꿨습니다. 가입 승인 관리자 워크플로, 원본의 법적 문서·관리 화면, PAT, 체험, 사용자 간 검색, 노트·채팅·캘린더·블로그 설정은 제외했습니다. 공개 가입은 환경 변수로 켜고 끌 수 있습니다.

원본과 분리하기 위해 DB·이미지 저장 경로와 쿠키 이름을 독립시켰습니다. 원본 서비스의 사용자·세션·실제 데이터·환경 비밀값은 복사하지 않습니다.

이관 중 갱신 토큰 소비와 새 세션 생성을 하나의 트랜잭션으로 묶었으며, 일회용 링크의 소비를 조건부 UPDATE로 보호했습니다. 비밀번호 설정·변경 시 기존 세션을 해제하고, 이미 폐기된 갱신 토큰으로 새로운 세션 전체를 해제할 수 없게 했습니다. 브라우저에서는 로그인·갱신·로그아웃을 공통 잠금으로 직렬화해 진행 중 갱신이 로그아웃 이후 쿠키를 되살리는 경합을 막습니다.

프로필 사진 처리는 원본의 노트 업로드 엔진 대신 계정 전용 저장소로 옮겼습니다. 허용 이미지의 실제 내용을 검사·재인코딩하며 사용자별 해시 경로와 임의 파일명을 사용합니다. 변경 후 이전 파일은 소유자 경로에 한해 제거합니다.

Termux 지원을 위해 기본 SQLite 의존성에서 PostgreSQL 바이너리를 분리했습니다. Docker는 `requirements-postgres.txt`를 설치해 PostgreSQL 옵션을 유지합니다. 프론트엔드는 원본과 같이 Webpack으로 실행하며, Android에서는 고정 버전 SWC WASM 의존성을 Next.js의 캐시 경로에 연결합니다. `tzdata`와 `TERMUX.md`도 추가했습니다.

개별 실행용으로 `backend/.env.example`과 `frontend/.env.example`을 제공하며 `init_env.py --standalone`은 기존 파일을 보존하면서 각 로컬 설정을 생성합니다. 프론트엔드는 기본 `APP_URL` 외에 `APP_ALLOWED_ORIGINS`의 정확한 출처를 추가로 검사합니다. 이를 통해 Termux의 localhost·루프백 IP 접속을 명시적으로 지원하며, 외부 출처·다른 포트·위조된 전달 헤더로 허용 범위가 확장되지는 않습니다.

계정 기반 PR 병합 후 LLM 대화 워크스페이스를 추가했습니다. 원본 메신저를 가져온 것이 아니라 독립적인 텍스트 대화 모듈이며, `chat_conversations`·`chat_messages` 테이블을 추가합니다. 기존 계정 테이블과 데이터를 재생성하지 않습니다. vLLM·Ollama와 로컬 테스트 응답은 어댑터로 분리하고, 기존 인증·출처 검사·BFF·배포 구성을 이어서 사용합니다. 디자인 근거는 `DESIGN.md`, 추론 서버 연결 규약은 `LLM_CONNECTIONS.md`에 있습니다.

다음 단계에서 Markdown 수식·코드 강조·각주 처리를 강화하고 별도의 운영 대시보드를 추가했습니다. 원본 관리자 콘솔을 이관한 것이 아니며, 기존 `is_global_admin` 역할과 새 생성 요청 메타데이터 테이블을 사용합니다. 역할 부여는 로컬 CLI로 명시적으로 수행합니다. 관리 API는 대화 본문·서버 비밀값을 반환하지 않습니다. 제목 변경은 기존 API를 보완해 상단에서 접근할 수 있게 하고 자동 제목과의 덮어쓰기를 방지합니다.
