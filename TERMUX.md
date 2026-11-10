# Termux 실행

Android arm64의 네이티브 Termux에서 SQLite 백엔드와 Next.js 프론트엔드를 각각 실행하는 구성입니다. 기본 설치는 `backend/requirements.txt`, 개발 테스트는 `backend/requirements-dev.txt`를 그대로 사용합니다.

대화 화면은 로그인 후 `/`입니다. 기본 `LLM_TEST_MODE=true`에서는 폐쇄망 서버 없이 랜덤 예시 답변을 주고받습니다. 실제 vLLM/Ollama를 연결할 때는 `backend/.env`의 `LLM_CONNECTIONS_JSON`을 지정하고 백엔드를 재시작합니다. 설정 형식과 네트워크 경로는 [모델 연결](LLM_CONNECTIONS.md)에 있습니다. 새로 추가한 HTTP 클라이언트와 Markdown 렌더러는 GPU·추론 라이브러리를 요구하지 않습니다.

## 첨부된 설치 오류 해결

`psycopg-binary==3.3.4` 설치 실패는 PostgreSQL용 바이너리를 SQLite 기본 설치에도 포함했던 문제입니다. PostgreSQL 의존성은 `requirements-postgres.txt`로 분리했습니다.

현재처럼 `backend` 디렉터리에서 `venv`가 활성화되어 있다면, 수정된 PR 브랜치를 받은 뒤 다음 명령으로 이어갈 수 있습니다.

```bash
git pull --ff-only
python ../scripts/init_env.py --standalone
python -m pip install -r requirements.txt
python main.py
```

개발 테스트가 필요하면 `python -m pip install -r requirements-dev.txt`를 사용합니다. 기존 가상환경을 계속 사용할 수 있습니다.

GitLab 계정 연결과 `@GitLab` 파일 조회도 사용할 경우 `python -m pip install -r requirements-gitlab.txt`를 추가로 설치합니다. 암호화 모듈을 기본 채팅 의존성과 분리했으며 [GITLAB_APPS.md](GITLAB_APPS.md)에 OAuth 등록·각 서비스 환경 변수·키 생성 방법을 정리했습니다. `FRONTEND_URL`은 이메일 링크뿐 아니라 GitLab OAuth callback 주소에도 사용하므로 프론트엔드 `APP_URL`과 일치시킵니다.

## 처음 설치하는 경우

프로젝트는 Termux 홈 아래에 둡니다. 예시 경로는 `~/workspace/llm`입니다. Python 3.12 이상과 Node.js 22.13 이상을 사용합니다.

필요한 도구가 없는 새 Termux 환경에서는 다음 패키지를 준비합니다. Rust와 C 빌드 도구는 Pydantic, cryptography, Pillow 등의 Android wheel이 없어서 소스 빌드가 필요할 때 사용합니다.

```bash
pkg update
pkg install python python-pip nodejs-lts npm git rust clang make pkg-config openssl libffi libjpeg-turbo libpng zlib
```

저장소 루트에서 각 서비스의 환경 설정을 만듭니다. 기존 설정파일은 보존합니다.

```bash
cd ~/workspace/llm
python scripts/init_env.py --standalone
```

`backend/.env.example` → `backend/.env`, `frontend/.env.example` → `frontend/.env.local`이 생성됩니다. 루트 `.env`가 Compose용 주소로 설정되어 있어도 서비스별 파일이 우선합니다. 터미널에 직접 지정한 환경변수는 파일보다 우선합니다. 이미 서비스별 파일이 있다면 새 예제를 참고해 필요한 값을 직접 수정합니다.

### 백엔드 세션

```bash
cd ~/workspace/llm/backend
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

이미 `venv`라는 이름으로 만들었다면 `source venv/bin/activate`로 활성화하면 됩니다. SQLite DB와 프로필 이미지는 `backend/data/`에 저장합니다. 별도의 PostgreSQL 서버는 기본 실행에 필요하지 않습니다.

#### cryptography의 `PyType_Type` 심볼 오류

`_rust.abi3.so`를 불러올 때 `cannot locate symbol "PyType_Type"`가 나오면 `.env`의 주소 설정과는 별개인 네이티브 라이브러리 연결 문제입니다. Python 3.13을 사용하는 Termux에서 아래 파일이 존재한다면 해당 프로세스에만 libpython을 먼저 불러오는 방법을 시험할 수 있습니다.

```bash
python --version
ls "$PREFIX/lib/libpython3.13.so"
LD_PRELOAD="$PREFIX/lib/libpython3.13.so${LD_PRELOAD:+:$LD_PRELOAD}" python main.py
```

버전 번호는 실행 중인 Python과 일치해야 합니다. 이 방법은 해당 심볼 오류의 우회이며 실제 단말에서 성공 여부를 확인해야 합니다. [Termux의 같은 유형 오류 보고](https://github.com/termux/termux-packages/issues/30705)에도 선행 로딩으로 import가 성공한 사례가 있습니다.

### 프론트엔드 세션

```bash
cd ~/workspace/llm/frontend
npm ci --include=optional
npm run dev
```

휴대전화 브라우저에서 `http://localhost:3000` 또는 `http://127.0.0.1:3000`을 엽니다. 백엔드는 `127.0.0.1:8000`으로 실행됩니다. 두 Termux 세션을 유지합니다.

`npm run dev`와 `npm run build`는 원본 프로젝트와 같은 Webpack을 사용합니다. Next.js와 동일한 버전의 SWC WASM 패키지를 설치하며, Android에서는 실행 전에 해당 패키지를 Next.js의 WASM 캐시에 연결합니다. 따라서 Turbopack 전용 네이티브 바인딩에 의존하지 않습니다. Tailwind/Lightning CSS의 Android arm64 패키지도 설치되도록 optional 의존성을 포함합니다.

파일을 수정해도 개발 서버가 변경을 감지하지 못하는 환경에서는 다음과 같이 polling을 켭니다.

```bash
WATCHPACK_POLLING=true npm run dev
```

운영용 Next.js 실행 명령은 `npm run build` 후 `npm start`입니다. 실제 Termux 단말에서의 빌드·화면 동작은 아직 검증하지 못했으며, Linux에서 PostgreSQL 없는 설치와 WASM 컴파일 경로를 검증한 결과는 [VALIDATION.md](VALIDATION.md)에 기록합니다.

## “요청 출처를 확인할 수 없습니다” 오류

이 메시지는 Next.js의 인증 요청 출처 검사에서 나옵니다. `localhost`와 `127.0.0.1`, 서로 다른 포트, HTTP와 HTTPS는 각각 다른 출처입니다. `frontend/.env.local`의 개발 설정은 다음과 같습니다.

```dotenv
APP_URL=http://localhost:3000
APP_ALLOWED_ORIGINS=http://127.0.0.1:3000
AUTH_SERVER_URL=http://127.0.0.1:8000
AUTH_COOKIE_SECURE=false
```

`APP_URL`은 기본 브라우저 주소, `APP_ALLOWED_ORIGINS`는 추가로 허용할 정확한 브라우저 주소, `AUTH_SERVER_URL`은 Next.js가 연결할 백엔드 주소입니다. 설정을 수정한 후 `npm run dev`를 종료하고 다시 실행합니다. 브라우저에서 실제 사용하는 주소에 맞춰 지정하며, 와일드카드는 지원하지 않습니다. HTTP 로컬 테스트에서 `AUTH_COOKIE_SECURE=true`를 사용하면 세션 쿠키가 정상 전달되지 않을 수 있습니다.

다른 포트나 LAN 주소로 접속한다면 그 출처를 명시적으로 추가해야 합니다. 예를 들어 `http://192.168.0.10:3001`로 접속할 경우 해당 주소를 추가하고 Next.js도 그 인터페이스·포트에서 실행합니다. `FRONTEND_URL`은 백엔드에서 만드는 이메일 링크의 기본 주소이며 프론트엔드 출처 허용 목록을 대신하지 않습니다.

루트 `.env`에 `APP_URL=http://localhost:8080` 같은 Compose 설정만 있고 서비스별 파일이 없으면 직접 실행한 `3000` 포트의 요청이 차단됩니다. `--standalone`으로 로컬 파일을 만들거나 기존 로컬 파일의 주소를 수정하면 됩니다. 허용하지 않은 외부 출처와 `Sec-Fetch-Site: cross-site` 요청은 계속 차단합니다.

## PostgreSQL을 사용하는 경우

일반 Linux/Docker용 `requirements-postgres.txt`는 바이너리 배포용입니다. Termux에서 PostgreSQL을 직접 사용하려면 Termux의 libpq와 순수 Python 드라이버를 설치합니다.

```bash
pkg install postgresql
cd ~/workspace/llm/backend
source .venv/bin/activate
python -m pip install "psycopg==3.3.4"
PSYCOPG_IMPL=python python main.py
```

이 경우 `backend/.env`의 `DATABASE_URL`도 실제 PostgreSQL 주소로 지정합니다. 기존 SQLite 데이터는 자동으로 옮겨지지 않습니다.

## 참고 근거

- [Psycopg 설치 방식과 libpq 요구 사항](https://www.psycopg.org/psycopg3/docs/basic/install.html)
- [Next.js CLI의 Webpack 옵션](https://nextjs.org/docs/app/api-reference/cli/next)
- 설치 버전 Next.js 16.2.12의 `build/swc/index.js`는 Android를 WASM 우선 대상으로 분류합니다. `scripts/prepare-next-wasm.mjs`는 그 버전의 캐시 위치를 준비하며 Next.js 코드를 수정하지 않습니다.
- [Python의 tzdata fallback](https://docs.python.org/3/library/zoneinfo.html): 시스템 시간대 DB가 없는 환경에서도 `Asia/Seoul` 같은 계정 시간대 검증이 가능하도록 `tzdata`를 기본 의존성에 포함합니다.
