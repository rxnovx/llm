# 초기 이관 검증

검증일: 2026-11-10. Node.js 24.22.1, Python 3.12.14. 이 저장소의 루트 경로와 LLM 서비스 식별자로 다시 검증했습니다. GitHub Actions는 실행하지 않았습니다.

## 통과

| 검증 | 결과 |
| --- | --- |
| `backend/.venv/bin/python -m pytest -q` | 25개 통과 |
| `npm run test:session` | 5개 통과 |
| `npm run test:proxy` | 11개 통과 |
| `npm run typecheck` | 통과 |
| `npm run lint` | 오류·경고 없이 통과 |
| `npm run build` | Next.js 프로덕션 빌드·정적 페이지 생성 통과 |
| `backend/.venv/bin/python scripts/smoke_standalone.py` | 실제 Python + Next.js 독립 프로세스의 전체 계정 API 연동 통과 |
| `python -m pip check` | 의존성 충돌 없음 |
| SQLite Compose 구성 | 공식 Compose JSON Schema 검증 통과 |
| PostgreSQL Compose 병합 구성 | 공식 Compose JSON Schema 검증 통과 |
| 환경 설정 생성 | 기존 `.env`·비밀값 보존, 새 파일 권한 0600 확인 |

백엔드 테스트는 중복 가입·비밀번호 강도·가입 제한·로그인 속도 제한·갱신 토큰 회전/재사용·회전 실패 롤백·로그아웃·다른 사용자의 세션 보호·비밀번호 변경·이메일 인증/변경/복구·사진 형식/크기/삭제·Google 계정 연결 정책을 확인합니다. Google 자격 증명 검증과 메일 발송은 테스트 대역을 사용합니다.

세션 테스트는 실제 `lib/auth.ts`를 격리된 탭 컨텍스트에서 실행해 갱신 단일화, 늦은 응답 무시, 장애 시 세션 만료 전파 방지, 다른 탭의 갱신과 로그아웃 순서, 실패 후 잠금 해제를 확인합니다. Web Locks·BroadcastChannel 전송 계층은 테스트 대역이며 실제 브라우저 검증을 대체하지 않습니다.

프록시 테스트는 빌드된 Next.js 서버를 실제로 실행합니다. 쿠키 속성·토큰 비노출·타임아웃·잘못된 인증 서버 응답·출처 검증·경로 제한·본문 크기·401/429/503 처리·로그아웃 실패 전달을 검증합니다.

독립 실행 통합 검증은 `python main.py`와 Next.js standalone 서버를 동시에 실행하고, 실제 FastAPI로 회원가입 → 로그인 → 프로필 변경 → 이미지 업로드/다운로드 → 갱신 → 로그아웃을 수행합니다. 테스트 DB와 업로드 경로는 임시 디렉터리로 분리합니다.

Compose 스키마 출처: `compose-spec/compose-spec`의 `schema/compose-spec.json`, 파일 SHA `44bbc2710502200b78a3e7793369a771d1ed7356`. YAML 파싱·서비스 연결·Dockerfile 경로를 함께 확인했습니다.

## 환경 제한으로 미검증

- Docker 실행 파일과 데몬이 없어 이미지 빌드·`docker compose up`·컨테이너 준비 상태·볼륨 영속성을 실제로 확인하지 못했습니다.
- Nginx 실행 파일이 없어 `nginx -t`와 실제 Nginx 경유 HTTPS를 확인하지 못했습니다.
- 시스템 Chromium 실행은 Unix 소켓 생성 권한 오류로 중단되었습니다. Playwright headless shell 설치도 유효한 ZIP을 받지 못해 실패했습니다. 데스크톱·모바일 브라우저 E2E와 화면 캡처는 통과로 기록하지 않습니다. `tests/accounts.spec.ts`와 실행 명령은 함께 제공합니다.
- 실제 PostgreSQL 서버, SMTP 메일 발송, Google 콘솔과 실제 Google 계정 로그인은 검증하지 못했습니다.

FastAPI 테스트 실행 시 상위 Starlette의 httpx 및 HTTP 422 상수에 관한 deprecation 경고 3건이 출력됐으나 테스트 실패는 없었습니다.
