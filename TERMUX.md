# Termux 실행

Android arm64의 네이티브 Termux에서 SQLite 백엔드와 Next.js 프론트엔드를 각각 실행하는 구성입니다. 기본 설치는 `backend/requirements.txt`, 개발 테스트는 `backend/requirements-dev.txt`를 그대로 사용합니다.

대화 화면은 로그인 후 `/`입니다. 기본 `LLM_TEST_MODE=true`에서는 폐쇄망 서버 없이 랜덤 예시 답변을 주고받습니다. 실제 vLLM/Ollama를 연결할 때는 `backend/.env`의 `LLM_CONNECTIONS_JSON`을 지정하고 백엔드를 재시작합니다. 설정 형식과 네트워크 경로는 [모델 연결](LLM_CONNECTIONS.md)에 있습니다. 새로 추가한 HTTP 클라이언트와 Markdown 렌더러는 GPU·추론 라이브러리를 요구하지 않습니다.

## 처음 설치하는 경우

[README의 Termux 설치 순서](README.md#termux에서-실행)에 시스템 패키지 설치, 환경파일 생성, 가상환경, 백엔드·프론트엔드 실행을 정리했습니다. `python-docx`를 설치하다 XML 라이브러리 오류가 나면 [lxml 빌드 오류 해결](README.md#lxml-빌드가-실패할-때) 순서를 따릅니다.

개발 테스트를 실행할 때는 백엔드 가상환경에서 `python -m pip install -r requirements-dev.txt`를 사용합니다. `pkg upgrade`로 Python의 주·부 버전이 바뀌었다면 기존 가상환경을 별도 이름으로 보관하고 새 Python으로 가상환경을 만든 뒤 의존성을 설치합니다.

기본 `requirements.txt` 설치에 GitLab, MCP 클라이언트와 문서 추출·생성이 포함됩니다. OAuth 등록과 키 설정은 [GITLAB_APPS.md](GITLAB_APPS.md)를 참고하세요. `FRONTEND_URL`은 이메일 링크뿐 아니라 GitLab OAuth callback 주소에도 사용하므로 프론트엔드 `APP_URL`과 일치시킵니다.

## cryptography의 `PyType_Type` 심볼 오류

`_rust.abi3.so`를 불러올 때 `cannot locate symbol "PyType_Type"`가 나오면 Python의 공유 라이브러리를 먼저 불러오는 방법을 시도할 수 있습니다. README에 Python 3.13 예시가 있습니다. 아래 명령은 백엔드 디렉터리의 활성화된 가상환경에서 현재 Python에 맞는 라이브러리를 찾습니다.

```bash
libpython="$PREFIX/lib/$(python -c 'import sysconfig; print(sysconfig.get_config_var("LDLIBRARY") or "")')"
if [ -f "$libpython" ]; then
  LD_PRELOAD="$libpython${LD_PRELOAD:+:$LD_PRELOAD}" python main.py
else
  printf '현재 Python의 공유 라이브러리를 확인하세요 (%s)\n' "$libpython" >&2
fi
```

이 명령의 `LD_PRELOAD`는 백엔드 실행에만 적용됩니다. 선행 로딩으로 import가 성공한 사례가 있습니다. [Termux의 같은 유형 오류 보고](https://github.com/termux/termux-packages/issues/30705)

## 프론트엔드

`npm run dev`와 `npm run build`는 원본 프로젝트와 같은 Webpack을 사용합니다. Next.js와 동일한 버전의 SWC WASM 패키지를 설치하며, Android에서는 실행 전에 해당 패키지를 Next.js의 WASM 캐시에 연결합니다. 따라서 Turbopack 전용 네이티브 바인딩에 의존하지 않습니다. Tailwind/Lightning CSS의 Android arm64 패키지도 설치되도록 optional 의존성을 포함합니다.

파일을 수정해도 개발 서버가 변경을 감지하지 못하는 환경에서는 다음과 같이 polling을 켭니다.

```bash
WATCHPACK_POLLING=true npm run dev
```

운영용 Next.js 실행 명령은 `npm run build` 후 `npm start`입니다. 실제 Termux 단말에서의 빌드·화면 동작은 아직 검증하지 못했으며, Linux에서 PostgreSQL 없는 설치와 WASM 컴파일 경로를 검증한 결과는 [VALIDATION.md](VALIDATION.md)에 기록합니다.

### 빌드의 TypeScript 단계에서 `expected usize`가 나오는 경우

Next.js 16.2.12의 WASM 오류 출력기는 터미널 너비가 없는 worker에서 `maxWidth: undefined`를 전달받으면 `invalid type: unit value, expected usize`로 실패합니다. 이 메시지는 **원래 TypeScript 진단을 표시하는 과정에서 생기는 추가 오류**이므로, 컴파일 성공만으로 타입 검사가 통과했다고 볼 수 없습니다. [Next.js의 관련 오류 보고](https://github.com/vercel/next.js/issues/99348)

Android에서는 `next.config.ts`의 `experimental.useTypeScriptCli`를 켜서 설치된 TypeScript의 `tsc`로 검사합니다. Next가 생성한 페이지·라우트 타입도 검사하며, 타입 오류가 있으면 원래 진단을 출력하고 빌드가 실패합니다. `ignoreBuildErrors`로 검사를 건너뛰거나 설치된 Next.js 파일을 직접 수정하지 않습니다. Linux에서는 `NEXT_TEST_WASM=1` 검증 시 같은 경로를 사용합니다.

프론트엔드 디렉터리에서 다음 명령으로 타입 생성과 검사를 먼저 실행할 수 있습니다.

```bash
cd ~/workspace/llm/frontend
npm run typecheck
npm run build
```

기존 버전에서 오류가 가려져도, 빌드 직후 아래 명령은 WASM 오류 출력기를 거치지 않고 실제 진단을 보여줍니다. `TS` 오류 코드와 파일·줄 위치를 기준으로 수정합니다.

```bash
node node_modules/typescript/bin/tsc --noEmit --pretty false
```

타입 검사가 통과한 소스에서는 기존 WASM 빌드도 성공할 수 있습니다. 따라서 정상 빌드뿐 아니라 의도적인 타입 오류가 있는 경우 원래 진단을 보여주며 실패하는지도 회귀 검사합니다. 이 변경은 빌드의 타입 검사 경로에 적용되며, Next.js 자체의 다른 WASM 코드프레임 경로까지 수정하지는 않습니다.

### 인증 스토어와 여러 화면에서 TS7006이 함께 나오는 경우

`authStore.ts`의 `set`, `get`, 로그인 인수뿐 아니라 `useAuthStore`를 사용하는 여러 화면의 selector·구독 인수까지 동시에 `implicitly has an 'any' type`가 된다면, 먼저 Zustand의 타입 선언 연결을 확인합니다. 정상 설치에서는 `create<State>`가 이 인수에 상태 타입을 전달합니다. Zustand 내부의 `zustand/vanilla` 선언을 해석하지 못해도 같은 연쇄 오류가 발생할 수 있으므로 각 인수에 `any`를 추가해서 해결하지 않습니다.

선언 파일 내부의 원인 오류도 빌드에 표시하도록 `skipLibCheck`는 `false`로 설정합니다. `strict`도 계속 켜 둡니다. 설치 상태는 잠금 파일 기준으로 일치시킨 후 검사합니다. `npm ci`는 의존성을 다시 설치하며 `package-lock.json`을 재생성하지 않습니다.

```bash
cd ~/workspace/llm/frontend
npm ci --include=optional
npm run typecheck
npm run test:types
npm run build
```

같은 오류가 계속되면 다음 출력으로 설치 버전과 숨겨진 선언 오류를 확인합니다. TypeScript 5.9.3·Zustand 5.1.0은 이번 잠금 파일을 새로 설치해 확인한 버전이며, 사용자 단말의 설치 버전이 이미 같다고 가정하지 않습니다.

```bash
npm ls typescript zustand
node node_modules/typescript/bin/tsc --noEmit --incremental false --skipLibCheck false --pretty false
```

`npm run test:types`는 실제 인증 스토어를 TypeScript로 검사합니다. 잘못된 상태 필드, 로그인·Google 로그인·로그아웃 인수는 컴파일 오류여야 하며 selector·구독·스토어 API가 `any`로 바뀌면 검사가 실패합니다. 정상 Linux 설치와 강제 WASM 빌드의 통과만으로 사용자 Termux 설치의 문제가 해결됐다고 판단하지 않습니다.

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

`psycopg-binary==3.3.4`에 호환되는 wheel을 찾지 못했다면 SQLite는 기본 `requirements.txt`로 설치합니다. Termux에서 PostgreSQL을 사용하는 경우 Termux의 libpq와 순수 Python 드라이버를 설치합니다.

```bash
pkg install postgresql
cd ~/workspace/llm/backend
source .venv/bin/activate
python -m pip install "psycopg==3.3.4"
PSYCOPG_IMPL=python python main.py
```

이 경우 `backend/.env`의 `DATABASE_URL`도 실제 PostgreSQL 주소로 지정합니다. 기존 SQLite 데이터는 자동으로 옮겨지지 않습니다.

## 참고 근거

원격 MCP와 프로젝트 분석을 사용하는 경우 [TERMINAL_MCP.md](TERMINAL_MCP.md)를 참고하세요. API와 원격 MCP 연결에는 `requirements.txt`, 같은 환경에서 분석 서버도 실행할 때는 `requirements-terminal.txt`를 사용합니다. 원격 Linux 워커가 명령을 수행하도록 구성하면 Termux에 Docker를 설치할 필요가 없습니다. 개인 개발용 `trusted_local`은 OS 계정에서 직접 실행하는 모드입니다.

- [Psycopg 설치 방식과 libpq 요구 사항](https://www.psycopg.org/psycopg3/docs/basic/install.html)
- [Next.js CLI의 Webpack 옵션](https://nextjs.org/docs/app/api-reference/cli/next)
- 설치 버전 Next.js 16.2.12의 `build/swc/index.js`는 Android를 WASM 우선 대상으로 분류합니다. `scripts/prepare-next-wasm.mjs`는 그 버전의 캐시 위치를 준비하며 Next.js 코드를 수정하지 않습니다.
- [Python의 tzdata fallback](https://docs.python.org/3/library/zoneinfo.html): 시스템 시간대 DB가 없는 환경에서도 `Asia/Seoul` 같은 계정 시간대 검증이 가능하도록 `tzdata`를 기본 의존성에 포함합니다.

범용 파일·지식 검색은 [ASSISTANT.md](ASSISTANT.md)를 참고하세요. 텍스트·PDF·DOCX·XLSX 검색은 기본 의존성으로 동작합니다. OCR은 별도로 설치한 `tesseract`, `pdftoppm`과 해당 언어 데이터를 사용합니다. SQLite 기본 검색을 사용하거나 내부 임베딩·Qdrant 서버를 연결할 수 있습니다.

## 대화·프로젝트와 결과 파일

질문 수정·분기·버전·본문 검색·프로젝트·자동 요약은 기존 개별 실행에서 동작합니다. PDF/XLSX 결과 파일 생성도 기본 Python 설치에 포함됩니다. 기본 PDF의 한글 표시와 TTF 경로 설정, 첨부·파일 한도는 [USER_WORKFLOWS.md](USER_WORKFLOWS.md)를 참고하세요. 실제 Android의 설치·화면 동작은 해당 단말에서 확인해야 합니다.
