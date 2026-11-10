# LLM

`rxnovx/my-note-app`의 Next.js 프론트엔드와 FastAPI 백엔드에서 유저 기능을 분리한 초기 구성입니다. 현재는 계정 기능만 제공하며, 노트·채팅·캘린더·블로그와 협업 서버는 포함하지 않습니다.

## 포함 기능

| 구분 | 기능 |
| --- | --- |
| 계정 | 회원가입, 사용자명/이메일 로그인, 로그아웃, 모든 기기 로그아웃 |
| 프로필 | 표시 이름, 프로필 이미지 업로드·링크·삭제, 사용자명 고정 |
| 보안 | 비밀번호 설정·변경·재설정, 로그인 세션 조회·원격 해제 |
| 이메일 | 인증·재전송, 재인증 후 이메일 변경, 계정 비활성화·복구 |
| 선택 연동 | Google 로그인, 기존 계정에 연결·연결 해제 |
| 배포 | Docker Compose, SQLite 기본값, PostgreSQL 옵션, Nginx |

원본의 사용자 기능과 보안 로직을 재사용하되 앱별 설정, 체험, 관리자 콘솔, PAT, 법적 권리행사 워크플로는 이 초기 앱에 포함하지 않았습니다. 가입 화면의 계정 생성 동의는 원본 서비스의 약관을 LLM에 적용한다는 뜻이 아닙니다. 이관 범위와 수정 근거는 [PORTING.md](PORTING.md)에 기록했습니다.

## Docker 없이 실행

필수 환경: Node.js 22.13 이상, Python 3.12 이상. 다음 명령은 저장소 루트에서 시작합니다.

Termux(Android arm64)의 설치 순서와 현재 `psycopg-binary` 오류 복구 방법은 [TERMUX.md](TERMUX.md)에 정리했습니다. 기본 SQLite 설치에는 PostgreSQL 드라이버가 필요하지 않습니다.

```bash
python scripts/init_env.py
```

이 명령은 `.env.example`을 바탕으로 `.env`를 만들고 JWT·프록시 비밀값을 각각 생성합니다. 이미 있는 `.env`는 변경하지 않으며 비밀값을 터미널에 출력하지 않습니다.

백엔드 터미널:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

프론트엔드 터미널:

```bash
cd frontend
npm ci
npm run dev
```

브라우저에서 <http://localhost:3000>을 엽니다. API는 `127.0.0.1:8000`, 개발 문서는 <http://127.0.0.1:8000/docs>입니다. `.env`를 두 프로세스가 공통으로 읽습니다. 프론트엔드는 별도의 `.env.local`로 `AUTH_SERVER_URL`을 지정할 수도 있습니다.

SQLite DB와 프로필 이미지는 `backend/data/`에 생성됩니다. 다른 디렉터리에서 Python을 실행해도 상대 DB 경로는 백엔드 디렉터리를 기준으로 해석합니다. 개발 자동 재시작은 `.env`의 `SERVER_RELOAD=true`로 켤 수 있습니다.

## Docker Compose 배포

새 `.env`를 만드는 경우:

```bash
python scripts/init_env.py --compose
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

접속 주소는 <http://localhost:8080>입니다. 기존 `.env`를 재사용한다면 `APP_URL`과 `FRONTEND_URL`을 먼저 `http://localhost:8080`으로 바꿉니다. 실제 도메인·포트를 쓸 때도 이 두 주소는 브라우저 접속 주소와 일치해야 합니다. `.env`를 바꾼 후에는 컨테이너를 다시 만듭니다.

| 서비스 | 역할 | 기본 공개 포트 |
| --- | --- | --- |
| nginx | 브라우저 진입점 | `8080` |
| web | Next.js와 인증 BFF | `127.0.0.1:3000` |
| api | FastAPI | `127.0.0.1:8000` |

API와 web은 각각 준비 상태 확인 후 의존 서비스가 시작됩니다. Compose는 백엔드를 운영 모드로 실행하고 문서·자동 재시작·인증 토큰 디버그 노출을 끕니다. SQLite와 이미지는 `app-data` 볼륨에 보존됩니다. `docker compose down`은 볼륨을 삭제하지 않습니다.

메일 서버 없이도 회원가입·비밀번호 로그인·프로필 설정이 동작합니다. 기본 `EMAIL_PROVIDER=disabled`에서는 메일이 발송되지 않습니다. 이메일 인증과 재설정 메일을 실제로 사용하려면 아래 설정을 완료합니다.

### PostgreSQL 사용

새 설정을 만들 때 `python scripts/init_env.py --compose --postgres`를 사용할 수 있습니다. 이미 `.env`가 있으면 URL에 안전한 `POSTGRES_PASSWORD`를 직접 설정합니다.

```bash
docker compose -f compose.yaml -f compose.postgres.yaml config --quiet
docker compose -f compose.yaml -f compose.postgres.yaml up -d --build
```

이 명령은 새 PostgreSQL DB를 사용합니다. 기존 SQLite 데이터를 자동으로 이관하지 않습니다. 개별 실행에서는 `DATABASE_URL=postgresql+psycopg://사용자:암호@호스트:5432/DB명`을 지정합니다.

Linux/macOS/Windows에서 PostgreSQL을 개별 실행으로 사용할 때는 백엔드 가상환경에서 `python -m pip install -r requirements-postgres.txt`로 선택 드라이버를 설치합니다. Docker 이미지는 이 드라이버를 포함합니다. Termux에서 PostgreSQL을 사용할 경우에는 [Termux용 순서](TERMUX.md#postgresql을-사용하는-경우)를 따릅니다.

## Nginx / HTTPS

Compose는 [deploy/nginx.conf](deploy/nginx.conf)를 바로 마운트합니다. 모든 브라우저 요청을 Next.js로 보내므로 인증 쿠키와 API 출처가 일치합니다. 파일 업로드 상한은 multipart 여유를 포함해 6MB이며 API에서 실제 이미지 5MB를 다시 검사합니다.

호스트 Nginx를 쓰려면 [nginx.config.file](nginx.config.file)의 도메인과 인증서 경로를 실제 값으로 바꿉니다. 해당 구성은 루프백 web 포트 `3000`으로 연결합니다.

```bash
docker compose up -d --build api web
# 호스트에 Nginx 설정을 설치한 후 실행
sudo nginx -t
sudo systemctl reload nginx
```

운영 HTTPS에서는 `.env`의 `APP_URL`, `FRONTEND_URL`, `CORS_ORIGINS`를 `https://실제도메인`으로 맞추고 `AUTH_COOKIE_SECURE=true`를 설정합니다. 호스트 Nginx를 사용하면서 앱을 개별 실행한다면 `APP_ENV=production`, `EXPOSE_DOCS=false`, `SERVER_RELOAD=false`, `AUTH_PROXY_CLIENT_IP_HEADER=x-real-ip`도 지정합니다.

쿠키 이름은 `llm_accounts_refresh`, 경로는 `/api/auth`, 속성은 HttpOnly·SameSite=Lax입니다. 갱신 토큰은 브라우저 JavaScript와 localStorage에 저장하지 않습니다. 로그인·갱신·로그아웃은 브라우저 탭 간에 순서를 맞추며, API는 회전된 토큰과 해제된 세션을 거부합니다.

## 이메일 / Google 선택 설정

- 개발에서만 `EMAIL_PROVIDER=console`을 사용하면 API 터미널에서 확인 링크를 볼 수 있습니다.
- 실제 발송은 `EMAIL_PROVIDER=smtp`, `SMTP_HOST`, `SMTP_PORT`, `SMTP_USE_TLS`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `EMAIL_FROM`을 설정합니다.
- 가입 시 이메일 인증을 요구하려면 메일 발송 설정 후 `EMAIL_VERIFICATION_REQUIRED=true`를 설정합니다.
- Google은 `GOOGLE_OAUTH_ENABLED=true`, `GOOGLE_CLIENT_ID`를 지정하고 Google 콘솔에 실제 브라우저 origin을 등록합니다. 필요하면 `GOOGLE_HOSTED_DOMAIN`으로 조직 도메인을 제한합니다.
- 사용자명은 가입 후 고정이며 이메일은 현재 비밀번호 또는 연결된 Google 재인증과 새 이메일 확인을 모두 거쳐 변경됩니다.
- 업로드 사진은 최대 20메가픽셀을 검사하고 1024×1024 안으로 축소한 PNG로 저장합니다. GIF는 첫 프레임을 사용합니다.

## 로컬 검증

```bash
cd backend
source .venv/bin/activate
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

```bash
cd frontend
npm ci
npm run typecheck
npm run lint
npm run build
npm run test:session
npm run test:proxy
npm run test:portable
```

실제 백엔드와 빌드한 프론트엔드를 함께 실행해 가입·프로필·업로드·갱신·로그아웃을 검증하려면 다음을 실행합니다. 임시 DB와 이미지 디렉터리를 사용하며 실행 후 두 프로세스를 종료합니다.

```bash
backend/.venv/bin/python scripts/smoke_standalone.py
```

프론트엔드·백엔드를 위의 개별 실행 명령으로 띄운 뒤 브라우저 검증:

```bash
cd frontend
npx playwright install chromium
npx playwright test
```

브라우저 검증은 개발용 DB에 고유한 테스트 계정을 생성합니다. 운영 서버를 대상으로 실행하지 않습니다. 기본 주소는 `http://localhost:3000`이며 `ACCOUNTS_BASE_URL`로 바꿀 수 있습니다. 설치된 Chromium을 사용하려면 `PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH`를 지정합니다. 검증 결과와 환경 제한은 [VALIDATION.md](VALIDATION.md)에 기록합니다.
