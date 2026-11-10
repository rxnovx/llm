# 유저 기능 이관 기록

원본: `rxnovx/my-note-app`, `main`의 커밋 `385d34409a39660f7d2e1cdf85e5ce977d2a9f10`.

대상: 빈 저장소 `rxnovx/llm`. PR 기준을 위한 최소 README 초기화 커밋은 `155873ddd2893cfefbe3cf7f46c591dc33e4b37c`입니다.

프론트엔드·백엔드·Compose·Nginx·실행 스크립트는 저장소 루트에 배치합니다.

| 원본 | 이번 앱 | 처리 |
| --- | --- | --- |
| `simple-auth-platform/app/core/security.py` | `backend/app/core/security.py` | scrypt, JWT 검증, 해시 유지; PAT 생성 제외 |
| `simple-auth-platform/app/modules/auth/` | `backend/app/modules/auth/` | 계정 모델·인증·회전·메일·세션·속도 제한·감사 기록 추출 |
| `simple-auth-platform/app/modules/users/router.py` | `backend/app/modules/users/router.py` | 프로필·비밀번호·세션·Google·이메일 변경 API 유지 |
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
