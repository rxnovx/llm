# LLM

내부 모델 서버에 연결해 대화하는 Next.js·FastAPI 앱입니다. `rxnovx/my-note-app`에서 이관한 계정 기능 위에 대화 워크스페이스를 구성했습니다. vLLM·Ollama 연결과 서버 없는 랜덤 테스트 대화를 지원합니다. 원본의 노트·메신저·캘린더·블로그·협업 서버는 포함하지 않습니다.

사용자 기능·설치·한도는 [USER_WORKFLOWS.md](USER_WORKFLOWS.md)에 정리했습니다.
사내 서버에 모델을 반입하고 Docker로 실행하려면 [폐쇄망 Docker 모델 설치 매뉴얼](docs/offline-models/README.md)을 참고하세요.

## 포함 기능

| 구분 | 기능 |
| --- | --- |
| 대화 | 스트리밍·백그라운드 생성, 중지·다시 생성·복사, Markdown·수식·코드 강조, 저장된 대화 재개·이름 변경·삭제, 질문 수정·분기·답변 버전 비교·본문 검색 |
| 프로젝트·자료 | 팀 프로젝트의 대화·지침·파일, 이미지·스캔 PDF OCR, 다중 첨부·전송률·취소, 긴 대화 자동 요약·이어가기 |
| 결과 | MD/TXT/CSV/XLSX/PDF 생성·미리보기·다운로드, 실제 분석 범위·답변/출처 오류 신고 |
| 운영 | 사용자·세션·응답 시간·실측/추정 토큰·일일 한도·실제 모델 테스트·감사 기록 |
| 모델 연결 | 등록된 vLLM·Ollama 연결, 병렬 Agent 분석, OpenAI 호환 서비스 API, 랜덤 테스트 모드 |
| 이미지 | 첨부 이미지 분석, 생성·편집과 대기열, 운영자 시험 생성·편집 |
| 계정 | 회원가입, 사용자명/이메일 로그인, 로그아웃, 모든 기기 로그아웃 |
| 프로필 | 표시 이름, 프로필 이미지 업로드·링크·삭제, 사용자명 고정 |
| 보안 | 비밀번호 설정·변경·재설정, 로그인 세션 조회·원격 해제 |
| 이메일 | 로그인 이메일 확인, 인증·재전송, 계정 비활성화·복구 |
| 선택 연동 | Google 로그인, 기존 계정에 연결·연결 해제 |
| 배포 | Docker Compose, SQLite 기본값, PostgreSQL 옵션, Nginx |

원본의 사용자 기능과 보안 로직을 재사용하되 앱별 설정, 체험, 관리자 콘솔, PAT, 법적 권리행사 워크플로는 이 초기 앱에 포함하지 않았습니다. 가입 화면의 계정 생성 동의는 원본 서비스의 약관을 LLM에 적용한다는 뜻이 아닙니다. 이관 범위와 수정 근거는 [PORTING.md](PORTING.md)에 기록했습니다.

로그인 후 `/`에서 대화를 시작합니다. 기본 `LLM_TEST_MODE=true`는 랜덤 예시 답변을 스트리밍하며 실제 모델에 접속하지 않습니다. 대화 기록은 기본 SQLite에 계정별로 저장됩니다. 실제 폐쇄망 서버를 연결하려면 [LLM_CONNECTIONS.md](LLM_CONNECTIONS.md)를 참고합니다. 서비스 10곳의 레퍼런스와 화면 구성 판단은 [DESIGN.md](DESIGN.md)에 기록했습니다.

메인은 로그인 전후 모두 채팅 화면입니다. 사이드바 계정 메뉴의 **설정**에서 일반·프로필·보안·로그인 기기·계정을 모달로 관리합니다. 로그인·가입·복구·운영 화면도 채팅 위에서 열리며, 앱 안에서 모달을 열고 닫는 동안 대화와 작성 중인 글을 유지하도록 구성했습니다. 직접 접근 주소와 동작은 [MODALS.md](MODALS.md)에 있습니다.

운영 화면의 **모델 관리**에서 서버·모델·사용 허용 계정·문맥 및 동시 생성 한도를 저장하면 재시작 없이 적용됩니다. 채팅의 **문맥**에서 입력 예산·제외된 대화를 확인하고 요약 메모를 직접 넣을 수 있습니다. 모델·사용자 한도를 넘는 생성은 DB 대기열에서 순서와 취소 버튼을 표시합니다. 답변이 생성되는 동안 **새 대화**를 열어 다른 질문을 보낼 수 있습니다. 기본 사용자 한도는 2개이며, 연결 또는 모델 한도가 먼저 차면 대기합니다. 환경파일 예제와 [연결 문서](LLM_CONNECTIONS.md)에 전체 설정을 정리했습니다.

현재 대화의 상단 제목 옆 편집 버튼 또는 사이드바 대화 메뉴의 **이름 변경**으로 제목을 수정합니다. 직접 정한 제목은 첫 메시지의 자동 제목으로 덮어쓰지 않습니다. Markdown 범위는 [MARKDOWN.md](MARKDOWN.md), 최초 운영자 지정과 `/admin` 사용법은 [OPERATIONS.md](OPERATIONS.md)에 있습니다.

## Docker 없이 실행

필수 환경: Node.js 22.13 이상, Python 3.12 이상. 다음 명령은 저장소 루트에서 시작합니다.

Android의 네이티브 Termux에서는 아래 [Termux 설치 순서](#termux에서-실행)를 따릅니다.

```bash
python scripts/init_env.py --standalone
```

이 명령은 서비스별 예제를 바탕으로 `backend/.env`와 `frontend/.env.local`을 만들고 JWT·프록시 비밀값을 생성합니다. 기존 파일은 변경하지 않으며 비밀값을 터미널에 출력하지 않습니다.

| 실행 방식 | 예제 | 실제 설정파일 |
| --- | --- | --- |
| 백엔드 개별 실행 | `backend/.env.example` | `backend/.env` |
| 프론트엔드 개별 실행 | `frontend/.env.example` | `frontend/.env.local` |
| Docker Compose / 공통 설정 | `.env.example` | 루트 `.env` |

환경변수 우선순위는 터미널에 지정한 값 → 서비스별 파일 → 루트 `.env`입니다. 기존 공통 설정 방식은 `python scripts/init_env.py`로 계속 사용할 수 있습니다. 서비스별 파일이 이미 있다면 변경할 항목을 직접 수정하고 두 프로세스를 재시작합니다. Compose는 루트 `.env`를 사용하며 서비스별 로컬 파일은 이미지에 포함하지 않습니다.

백엔드 터미널:

```bash
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python main.py
```

이 한 번의 설치에 GitLab 연결, MCP 클라이언트, 문서 추출과 파일 생성이 포함됩니다. OCR의 시스템 패키지는 [ASSISTANT.md](ASSISTANT.md#로컬-ocr)를 참고하세요.

프론트엔드 터미널:

```bash
cd frontend
npm ci
npm run dev
```

브라우저에서 <http://localhost:3000> 또는 <http://127.0.0.1:3000>을 엽니다. 개별 실행 예제는 두 출처를 명시적으로 허용합니다. API는 `127.0.0.1:8000`, 개발 문서는 <http://127.0.0.1:8000/docs>입니다.

“요청 출처를 확인할 수 없습니다”가 표시되면 `frontend/.env.local`의 `APP_URL`과 브라우저 주소의 프로토콜·호스트·포트를 확인합니다. 추가 주소는 `APP_ALLOWED_ORIGINS`에 쉼표로 구분해 지정합니다. 백엔드 연결 주소인 `AUTH_SERVER_URL`이나 백엔드 `CORS_ORIGINS`를 바꾸는 것만으로는 프론트엔드의 출처 검사가 변경되지 않습니다. 상세 예시는 [TERMUX.md](TERMUX.md#요청-출처를-확인할-수-없습니다-오류)에 있습니다.

SQLite DB와 프로필 이미지는 `backend/data/`에 생성됩니다. 다른 디렉터리에서 Python을 실행해도 상대 DB 경로는 백엔드 디렉터리를 기준으로 해석합니다. 개발 자동 재시작은 `backend/.env`의 `SERVER_RELOAD=true`로 켤 수 있습니다.

### Termux에서 실행

Android arm64 기준입니다. 저장소는 Termux 홈 아래에 둡니다. 예시 경로는 `~/workspace/llm`입니다.

#### 1. Termux 시스템 패키지 설치

Python 3.12 이상과 Node.js 22.13 이상을 사용합니다. 이미 이 조건을 충족하는 Node.js와 npm이 있다면 `pkg install nodejs-lts npm`을 건너뜁니다.

```bash
pkg update && pkg upgrade
pkg install git python python-pip
pkg install nodejs-lts npm
pkg install clang make pkg-config ndk-sysroot rust \
  libxml2 libxslt openssl libffi libjpeg-turbo libpng zlib libwebp
```

Android에 맞는 Python wheel이 없으면 pip가 패키지를 소스에서 빌드합니다. `libxml2`와 `libxslt`는 `python-docx`의 의존성인 `lxml`에, OpenSSL·libffi와 Rust는 `cryptography`를 빌드하는 데 쓰입니다. JPEG·PNG·WebP 라이브러리는 Pillow의 이미지 처리를 지원합니다. Termux의 pip는 `pkg`로 관리합니다. [Termux 패키지 관리](https://github.com/termux/termux-packages/wiki/Package-Management) · [lxml 설치 안내](https://lxml.de/installation.html)

#### 2. 환경파일 생성과 백엔드 설치

저장소 루트에서 시작합니다. 환경파일 생성 명령은 기존 `backend/.env`와 `frontend/.env.local`을 보존합니다. 이미 가상환경이 있다면 `python -m venv .venv` 줄을 건너뜁니다. 폴더 이름이 `venv`라면 `source venv/bin/activate`로 활성화합니다.

```bash
python scripts/init_env.py --standalone
cd backend
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
```

기본 SQLite 실행에 필요한 Python 패키지는 `requirements.txt`에 있습니다. 설치가 끝나면 같은 세션에서 백엔드를 띄웁니다.

```bash
python main.py
```

Python 3.13에서 `cannot locate symbol "PyType_Type"` 오류가 나오면 `$PREFIX/lib/libpython3.13.so`가 있는지 확인한 뒤, 같은 세션에서 아래처럼 실행합니다. 다른 Python 버전은 [현재 버전에 맞는 실행 방법](TERMUX.md#cryptography의-pytype_type-심볼-오류)을 참고하세요.

먼저 러스트 컴파일러를 설치해준다.
``` bash
pkg install -y rust clang make pkg-config openssl libffi

# Rust 설치 확인
rustc --version
cargo --version

# 빌드 환경 설정
export OPENSSL_DIR="$PREFIX"
export PKG_CONFIG_PATH="$PREFIX/lib/pkgconfig"
export CARGO_BUILD_TARGET="aarch64-linux-android"

# cryptography 설치
python -m pip install 'cryptography>=46.0.7,<51'
```

```bash
LD_PRELOAD="$PREFIX/lib/libpython3.13.so${LD_PRELOAD:+:$LD_PRELOAD}" \
python main.py
```

#### 3. 프론트엔드 실행

새 Termux 세션에서 저장소 루트로 이동한 뒤 실행합니다. `npm ci --include=optional`은 Android용 CSS 의존성도 설치합니다.

```bash
cd frontend
npm ci --include=optional
npm run dev
```

두 세션을 유지하고 휴대전화 브라우저에서 <http://127.0.0.1:3000>을 엽니다. 백엔드 상태는 <http://127.0.0.1:8000/ready>에서 확인할 수 있습니다.

#### `lxml` 빌드가 실패할 때

`Getting requirements to build wheel` 단계에서 `libxml2`와 `libxslt` 개발 패키지를 요구하면 아래 명령으로 헤더와 라이브러리를 준비합니다. `lxml`의 PyPI 소스 배포본에는 빌드에 필요한 C 코드가 들어 있습니다. [lxml 소스 빌드 안내](https://lxml.de/installation.html#building-lxml-from-dev-sources)

백엔드 디렉터리에서 가상환경을 활성화한 뒤 실행합니다. `pkg-config`가 라이브러리 두 개의 버전을 출력하면 Termux의 헤더·라이브러리 경로를 지정해 설치를 재시도합니다.

```bash
pkg install clang pkg-config libxml2 libxslt
termux_pkg_config="$PREFIX/lib/pkgconfig${PKG_CONFIG_PATH:+:$PKG_CONFIG_PATH}"
PKG_CONFIG_PATH="$termux_pkg_config" pkg-config --modversion libxml-2.0 libxslt

PKG_CONFIG_PATH="$termux_pkg_config" \
CFLAGS="-I$PREFIX/include -I$PREFIX/include/libxml2 ${CFLAGS:-}" \
LDFLAGS="-L$PREFIX/lib ${LDFLAGS:-}" \
  python -m pip install -r requirements.txt

python -m pip check
python -c 'import docx; from lxml import etree; print(etree.LXML_VERSION)'
```

PostgreSQL 선택 설치, 출처 오류, `cryptography`의 `PyType_Type` 오류는 [Termux 문제 해결](TERMUX.md)에 있습니다.

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

Compose는 [deploy/nginx.conf](deploy/nginx.conf)를 바로 마운트합니다. 모든 브라우저 요청을 Next.js로 보내므로 인증 쿠키와 API 출처가 일치합니다. 지식 문서 업로드 경로는 multipart 포함 21MiB를 허용합니다. 앱에서 파일을 기본 10MiB, 설정 가능한 최대 20MiB로 검사합니다. 일반 요청의 Nginx 상한은 6MiB, 프로필 이미지는 API에서 5MiB로 검사합니다.

호스트 Nginx를 쓰려면 [nginx.config.file](nginx.config.file)의 도메인과 인증서 경로를 실제 값으로 바꿉니다. 해당 구성은 루프백 web 포트 `3000`으로 연결합니다.

```bash
docker compose up -d --build api web
# 호스트에 Nginx 설정을 설치한 후 실행
sudo nginx -t
sudo systemctl reload nginx
```

운영 HTTPS에서는 `.env`의 `APP_URL`, `FRONTEND_URL`, `CORS_ORIGINS`를 `https://실제도메인`으로 맞추고 `AUTH_COOKIE_SECURE=true`를 설정합니다. `APP_ALLOWED_ORIGINS`는 비워 두거나 실제 운영 출처만 지정합니다. 호스트 Nginx를 사용하면서 앱을 개별 실행한다면 `APP_ENV=production`, `EXPOSE_DOCS=false`, `SERVER_RELOAD=false`, `AUTH_PROXY_CLIENT_IP_HEADER=x-real-ip`도 지정합니다.

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
npm run test:env
npm run test:chat
npm run test:apps
npm run test:markdown
npm run test:api
npm run test:navigation
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

## GitLab 앱 연결

GitLab 계정 연결과 `@GitLab` 코드 조회는 [GITLAB_APPS.md](GITLAB_APPS.md)를 참고하세요. 사용자별 OAuth 승인과 서버 환경 설정이 필요하며, 설정은 앱 연결 모달에서 사용합니다. 기본 SQLite·개별 실행·Compose 구성을 그대로 사용합니다.

프로젝트 분석·실행에 MR 작성 권한을 연결하면 코드 수정과 테스트를 거쳐 GitLab에 커밋하고 MR을 열 수 있습니다. [MR 생성 설정](GITLAB_APPS.md#코드-수정-후-mr-생성)

기존 MR의 링크를 채팅에 보내면 리뷰와 실패한 CI를 조회할 수 있습니다. 후속 작업을 허용하면 같은 MR의 소스 브랜치에 수정 사항을 추가 커밋하고, 진행 기록과 결과 링크를 채팅에 표시합니다. [기존 MR 검토와 후속 커밋](GITLAB_APPS.md#기존-mr-검토와-후속-커밋)

프로젝트 전체 다운로드·자동 코드 탐색·명령 실행·작업공간 관리는 [TERMINAL_MCP.md](TERMINAL_MCP.md)에 정리했습니다. MCP는 별도 서비스이며 개인별 저장소 권한과 실행 모드를 적용합니다. 기존 연결은 `read_repository` 권한 추가 후 다시 인증해야 합니다.

범용 RAG·OCR·MCP 앱·병렬 Agent는 [ASSISTANT.md](ASSISTANT.md), 외부 프로그램의 서비스 키 설정은 [OPERATIONS.md](OPERATIONS.md#서비스-api)를 참고하세요.
