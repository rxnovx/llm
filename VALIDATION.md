# 계정 및 대화 워크스페이스 검증

검증일: 2026-11-10. Node.js 24.22.1, Python 3.12.14. 이 저장소의 루트 경로와 LLM 서비스 식별자로 다시 검증했습니다. GitHub Actions는 실행하지 않았습니다.

## 통과

| 검증 | 결과 |
| --- | --- |
| `cd backend && python -m pytest -q` | PostgreSQL 드라이버 없는 가상환경에서 46개 통과 (기존 계정·환경 설정 33개 + 대화 13개) |
| `npm run test:session` | 5개 통과 |
| `npm run test:proxy` | 14개 통과 |
| `npm run test:env` | 실제 Next.js 환경파일 로더로 우선순위 검증 3개 통과 |
| `npm run test:chat` | UTF-8·CRLF·분할 SSE·중단·크기 제한 5개 통과 |
| `npm run test:portable` | Next.js WASM 바인딩으로 TypeScript 변환·실행 1개 통과 |
| `npm run typecheck` | 통과 |
| `npm run lint` | 오류·경고 없이 통과 |
| `npm run build` | Webpack 프로덕션 빌드·정적 페이지 생성 통과 |
| `NEXT_TEST_WASM=1 npm run build` | 이전 Termux 대응에서 강제 WASM 프로덕션 빌드 통과; 이번 환경파일 변경은 일반 Webpack 빌드로 재검증 |
| `backend/.venv-portable/bin/python scripts/smoke_standalone.py` | 실제 계정 API + 대화 스트리밍·저장·중지·재생성·페이지 조회·삭제 통과 |
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

첨부 로그의 `psycopg-binary==3.3.4` 설치 실패에 대응해 기본 SQLite 의존성에서 PostgreSQL 바이너리를 분리했습니다. `requirements-dev.txt`만 설치한 가상환경에서 `psycopg` 미설치를 확인했으며, 이번 환경파일 변경 후 백엔드 33개 테스트가 통과했습니다. PostgreSQL import를 차단한 새 프로세스에서도 가입·로그인·프로필 조회가 성공하며, 시스템 시간대 DB를 비운 프로세스에서도 `tzdata`로 `Asia/Seoul`을 검증합니다.

프론트엔드는 Webpack으로 바꾸고, Next.js와 동일 버전의 SWC WASM 패키지를 고정했습니다. Android 준비 스크립트는 해당 패키지를 Next.js WASM 캐시에 연결합니다. 생성 캐시를 비운 뒤 준비 스크립트를 실행했고, 접근 불가능한 npm registry를 지정한 상태에서도 실제 Next.js WASM 로더가 TypeScript를 변환했습니다. Next.js의 강제 WASM 테스트 모드로 전체 프로덕션 빌드와 독립 실행 API 연동도 통과했습니다. 이 강제 플래그는 검증에만 사용하며 앱 실행 설정에는 넣지 않았습니다.

계정·설정·대화·프론트엔드 합계 74개 테스트가 통과했습니다. 이 결과는 Linux에서 확인한 설정·출처 검사·SQLite 의존성·WASM 경로 검증이며 Android ABI, Termux의 실제 패키지 빌드, 파일 감시, 휴대전화 브라우저 동작을 검증했다는 의미는 아닙니다. `LD_PRELOAD` 방식은 Termux 문서에 안내했으며 이 Linux 환경에서 해당 Android 심볼 오류의 해결을 검증한 것은 아닙니다.

## LLM 대화 검증

대화 테스트 13개는 미로그인 차단, 다른 계정의 대화 조회·변경·삭제·중지 차단, 테스트 연결의 무네트워크 응답, 테스트 모드 해제 시 자동 대체 금지, 임의 URL/키 입력 차단, 부분 응답 저장, 재생성 시 중복 메시지 방지, 동시 생성 잠금·취소 ID·만료 후 회수, 기존 정상 답변 보존을 확인합니다.

vLLM과 Ollama는 공식 프로토콜 형식의 HTTP 테스트 대역으로 검증했습니다. 실제 어댑터의 서버 인증 헤더, 모델 목록 경로, 이전 메시지 전달, 생성 옵션, 한글 UTF-8이 3바이트씩 나뉘는 스트림, 종료 표시 없는 응답·잘못된 JSON 거부를 확인했습니다. 실제 폐쇄망 GPU 서버·모델에 접속한 결과는 아닙니다.

프론트엔드의 공통 SSE 파서는 1바이트 단위 UTF-8 분할, CRLF가 조각 사이에 걸친 경우, 명시적 완료·오류·취소, 비정상 EOF, 크기 초과 시 reader 해제를 검증합니다. 실제 Next.js 프록시 검증은 첫 프레임에 완료 이벤트가 포함되지 않음을 확인해 응답 버퍼링을 검사하며, 대화 API의 출처 검사·인증 헤더 전달·쿠키 비전달·허용 경로·페이지 쿼리 전달을 확인합니다.

실제 Python + Next.js 통합 검증은 로컬 랜덤 답변의 첫 조각을 먼저 수신하고, 완료 후 SQLite 기록을 대조합니다. 두 번째 답변 도중 취소를 보내 잠금이 해제되고 부분 답변이 `cancelled`로 저장되는지 확인한 뒤, 같은 턴을 다시 생성해 메시지 중복이 없음을 검사합니다.

브라우저 테스트 시나리오 `tests/chat.spec.ts`와 기존 계정 시나리오를 함께 제공합니다. 이번 브라우저 제어 환경에서는 준비된 `http://127.0.0.1:3100` 서버에도 `ERR_CONNECTION_REFUSED`가 발생해 화면을 확인하지 못했습니다. 데스크톱·모바일 레이아웃, Markdown 화면, 한글 IME, 화면 키보드와 실제 클릭 흐름은 통과로 기록하지 않습니다. 디자인은 공식 서비스 10곳의 자료를 검토해 구성했으며 판단과 자료 링크는 `DESIGN.md`에 있습니다.

## 환경 제한으로 미검증

- Docker 실행 파일과 데몬이 없어 이미지 빌드·`docker compose up`·컨테이너 준비 상태·볼륨 영속성을 실제로 확인하지 못했습니다.
- Nginx 실행 파일이 없어 `nginx -t`와 실제 Nginx 경유 HTTPS를 확인하지 못했습니다.
- 시스템 Chromium 실행은 Unix 소켓 생성 권한 오류로 중단되었습니다. Playwright headless shell 설치도 유효한 ZIP을 받지 못해 실패했습니다. 데스크톱·모바일 브라우저 E2E와 화면 캡처는 통과로 기록하지 않습니다. `tests/accounts.spec.ts`와 실행 명령은 함께 제공합니다.
- 실제 PostgreSQL 서버, SMTP 메일 발송, Google 콘솔과 실제 Google 계정 로그인은 검증하지 못했습니다.
- 실제 Android/Termux 단말에서 설치·개발 서버·운영 빌드·화면 동작은 미검증입니다. Termux용 네이티브 Python/CSS 모듈은 단말에서 추가 확인이 필요합니다.

FastAPI 테스트 실행 시 상위 Starlette의 httpx 및 HTTP 422 상수에 관한 deprecation 경고 3건이 출력됐으나 테스트 실패는 없었습니다.
