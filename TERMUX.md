# Termux 실행

Android arm64의 네이티브 Termux에서 SQLite 백엔드와 Next.js 프론트엔드를 각각 실행하는 구성입니다. 기본 설치는 `backend/requirements.txt`, 개발 테스트는 `backend/requirements-dev.txt`를 그대로 사용합니다.

## 첨부된 설치 오류 해결

`psycopg-binary==3.3.4` 설치 실패는 PostgreSQL용 바이너리를 SQLite 기본 설치에도 포함했던 문제입니다. PostgreSQL 의존성은 `requirements-postgres.txt`로 분리했습니다.

현재처럼 `backend` 디렉터리에서 `venv`가 활성화되어 있다면, 수정된 PR 브랜치를 받은 뒤 다음 명령으로 이어갈 수 있습니다.

```bash
git pull --ff-only
python -m pip install -r requirements.txt
python main.py
```

개발 테스트가 필요하면 `python -m pip install -r requirements-dev.txt`를 사용합니다. 기존 가상환경을 계속 사용할 수 있습니다.

## 처음 설치하는 경우

프로젝트는 Termux 홈 아래에 둡니다. 예시 경로는 `~/workspace/llm`입니다. Python 3.12 이상과 Node.js 22.13 이상을 사용합니다.

필요한 도구가 없는 새 Termux 환경에서는 다음 패키지를 준비합니다. Rust와 C 빌드 도구는 Pydantic, cryptography, Pillow 등의 Android wheel이 없어서 소스 빌드가 필요할 때 사용합니다.

```bash
pkg update
pkg install python python-pip nodejs-lts npm git rust clang make pkg-config openssl libffi libjpeg-turbo libpng zlib
```

저장소 루트에서 환경 설정을 만듭니다. 기존 `.env`가 있으면 그대로 보존합니다.

```bash
cd ~/workspace/llm
python scripts/init_env.py
```

### 백엔드 세션

```bash
cd ~/workspace/llm/backend
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

이미 `venv`라는 이름으로 만들었다면 `source venv/bin/activate`로 활성화하면 됩니다. SQLite DB와 프로필 이미지는 `backend/data/`에 저장합니다. 별도의 PostgreSQL 서버는 기본 실행에 필요하지 않습니다.

### 프론트엔드 세션

```bash
cd ~/workspace/llm/frontend
npm ci --include=optional
npm run dev
```

휴대전화 브라우저에서 `http://localhost:3000`을 엽니다. 백엔드는 `127.0.0.1:8000`으로 실행됩니다. 두 Termux 세션을 유지합니다.

`npm run dev`와 `npm run build`는 원본 프로젝트와 같은 Webpack을 사용합니다. Next.js와 동일한 버전의 SWC WASM 패키지를 설치하며, Android에서는 실행 전에 해당 패키지를 Next.js의 WASM 캐시에 연결합니다. 따라서 Turbopack 전용 네이티브 바인딩에 의존하지 않습니다. Tailwind/Lightning CSS의 Android arm64 패키지도 설치되도록 optional 의존성을 포함합니다.

파일을 수정해도 개발 서버가 변경을 감지하지 못하는 환경에서는 다음과 같이 polling을 켭니다.

```bash
WATCHPACK_POLLING=true npm run dev
```

운영용 Next.js 실행 명령은 `npm run build` 후 `npm start`입니다. 실제 Termux 단말에서의 빌드·화면 동작은 아직 검증하지 못했으며, Linux에서 PostgreSQL 없는 설치와 WASM 컴파일 경로를 검증한 결과는 [VALIDATION.md](VALIDATION.md)에 기록합니다.

## PostgreSQL을 사용하는 경우

일반 Linux/Docker용 `requirements-postgres.txt`는 바이너리 배포용입니다. Termux에서 PostgreSQL을 직접 사용하려면 Termux의 libpq와 순수 Python 드라이버를 설치합니다.

```bash
pkg install postgresql
cd ~/workspace/llm/backend
source .venv/bin/activate
python -m pip install "psycopg==3.3.4"
PSYCOPG_IMPL=python python main.py
```

이 경우 `.env`의 `DATABASE_URL`도 실제 PostgreSQL 주소로 지정합니다. 기존 SQLite 데이터는 자동으로 옮겨지지 않습니다.

## 참고 근거

- [Psycopg 설치 방식과 libpq 요구 사항](https://www.psycopg.org/psycopg3/docs/basic/install.html)
- [Next.js CLI의 Webpack 옵션](https://nextjs.org/docs/app/api-reference/cli/next)
- 설치 버전 Next.js 16.2.12의 `build/swc/index.js`는 Android를 WASM 우선 대상으로 분류합니다. `scripts/prepare-next-wasm.mjs`는 그 버전의 캐시 위치를 준비하며 Next.js 코드를 수정하지 않습니다.
- [Python의 tzdata fallback](https://docs.python.org/3/library/zoneinfo.html): 시스템 시간대 DB가 없는 환경에서도 `Asia/Seoul` 같은 계정 시간대 검증이 가능하도록 `tzdata`를 기본 의존성에 포함합니다.
