# 범용 AI 대화·지식·MCP

메인 화면은 채팅입니다. 입력창의 **+ → 파일·지식**에서 자료를 선택하고 **+ → 연결된 앱**에서 앱을 선택합니다. 지식 관리, 공유, 개인 기억과 운영자 MCP 등록은 이 모달에서 처리합니다.

## 대화 실행 엔진

선택한 지식·첨부를 먼저 검색해 근거를 확보합니다. 도구를 지원하는 모델은 지식 검색과 선택된 MCP 도구를 추가 호출할 수 있습니다. 일반 대화 모델도 검색한 근거를 받아 스트리밍 답변을 생성합니다. 도구 작업 중에는 기존 DB 대기열의 추론 허가를 반납하고 모델 호출 때 다시 획득합니다. 도구 schema·결과·개인 설정도 입력 예산에 포함하며 오래된 완전한 대화/도구 묶음을 제외합니다. 최신 질문도 담기 어려우면 명시적 오류를 반환합니다.

GitLab 작업공간과 범용 자료·MCP를 함께 선택할 수 있습니다. 작업공간 ID·사용자·자료 범위는 호스트가 결정합니다. 모델이 다른 사용자의 자료나 MCP 주소를 지정할 수 없습니다. 단계·전체 시간 제한과 중지·재생성 복구는 기존 채팅 흐름을 사용합니다. 외부 MCP 요청을 중단해도 해당 서버에서 이미 수행한 외부 변경을 되돌리거나 원격 작업을 중지한다고 보장하지는 않습니다.

## 파일과 지식베이스

| 저장 방식 | 수명·권한 |
| --- | --- |
| 대화 첨부 | 해당 사용자·대화에서 선택. 24시간 뒤 접근 만료, 워커가 정리. 대화 삭제 시 사본 제거 |
| 지식베이스 | 여러 대화에서 재사용. 소유자가 자료 업로드·교체·삭제·공유 관리 |
| 공유받은 지식 | 등록된 사용자에게 읽기·검색만 허용. 공유 해제 후 새 검색·원문 조회 차단 |

채팅 입력창에 이미지를 붙여넣거나 파일을 끌어 놓으면 입력창 위에 썸네일이나 파일명이 표시됩니다. 전송을 누르면 첨부를 업로드하고, 문서는 색인을 완료한 뒤 메시지와 함께 보냅니다. 전송 전에 첨부를 제거할 수 있고, 업로드 실패나 취소 시 입력과 첨부를 유지합니다. LLM이 질문과 첨부를 보고 이미지 분석·편집·생성 중 필요한 작업을 고릅니다. 이미지 분석은 선택한 문서의 검색 결과와 함께 사용할 수 있습니다.

PDF, DOCX, XLSX, CSV/TSV, UTF-8 텍스트·Markdown·JSON·HTML을 지원합니다. PDF의 텍스트 레이어를 추출하고, 텍스트가 없는 이미지 페이지와 이미지 파일은 로컬 OCR로 읽습니다. DOCX는 문단·표, XLSX는 시트·행 위치를 표시하며 수식을 실행하거나 외부 링크를 따라가지 않습니다. PDF 페이지와 텍스트 줄, 파일 SHA-256·색인 버전·구간 ID를 답변 출처에 기록합니다. 원문은 출처를 눌러 권한 확인 후 모달에서 엽니다.

동일 위치·파일명·내용 해시의 재업로드는 기존 색인을 재사용합니다. 같은 파일명의 내용이 달라지면 새 버전으로 다시 색인합니다. 이전 답변은 보존하며 이전 버전의 출처임을 안내합니다. 이미 전달된 답변 내용까지 공유 해제로 지워지지는 않습니다.

기본 업로드 10MiB, 개인 원본 합계 64MiB·100개, 지식베이스 20개·질문당 5개, 첨부 10개 제한입니다. 문서당 2백만 자·1,500구간, 검색 시 15,000구간·200문서로 제한합니다. 큰 범위는 지식베이스를 나눠 선택합니다. 문서 추출은 별도 Python 프로세스에서 OCR 설정 시 80초, 비활성화 시 30초 제한과 지원 OS의 CPU·메모리 제한을 적용합니다. ZIP 기반 문서에는 압축 해제 크기·항목 수 제한이 있습니다. 원본과 추출 구간은 계정 데이터베이스에 저장되므로 DB 백업과 저장소 암호화 정책을 함께 적용하세요.

## 설치와 개별 실행

```bash
cd backend
python -m pip install -r requirements.txt
python main.py
```

다른 터미널에서:

```bash
cd frontend
npm run dev
```

텍스트·Markdown·PDF·DOCX·XLSX 추출과 MCP 클라이언트는 기본 설치에 포함됩니다. 기본은 임베딩 서버 없이 SQLite의 제한된 키워드/BM25 방식으로 검색하며, UI에 **키워드 검색**이라고 표시합니다. 데모 답변은 실제 모델의 분석 정확도를 뜻하지 않습니다. 가짜 임베딩을 운영 검색으로 사용하지 않습니다.

API 프로세스 안의 비동기 워커가 영속 DB 작업을 처리하므로 `python main.py`만으로 색인이 시작됩니다. 여러 API 프로세스는 조건부 DB 갱신으로 작업을 claim하며 180초 lease와 150초 작업 제한을 적용합니다. 전용 워커를 쓰려면 API의 `KNOWLEDGE_WORKER_ENABLED=false`를 설정하고 같은 DB 설정으로 다음을 실행하세요.

```bash
cd backend
python knowledge_main.py
```

중단된 색인은 lease 만료 후 다시 처리합니다. 문서/모델 오류는 지식 관리에 표시하고 재색인할 수 있습니다. 별도 워커를 실행하지 않고 API 워커까지 비활성화하면 대기 상태가 유지됩니다.

## 로컬 OCR

Compose API 이미지에는 Tesseract, 영어·한국어 언어 데이터, Poppler를 포함합니다. 개별 실행에서는 호스트에 `tesseract`와 `pdftoppm`을 설치합니다. Debian/Ubuntu 패키지는 `tesseract-ocr`, `tesseract-ocr-kor`, `poppler-utils`입니다. 지원 언어는 `tesseract --list-langs`로 확인합니다.

```dotenv
# OCR 활성화와 설치된 언어 선택
KNOWLEDGE_OCR_ENABLED=true
KNOWLEDGE_OCR_LANGUAGES=kor+eng
```

Compose의 기본 언어는 `kor+eng`, 개별 실행의 기본값은 `eng`입니다. PNG·JPEG·WebP·TIFF 단일 이미지와 텍스트가 없는 PDF 이미지 페이지를 읽습니다. 이미지는 1,600만 픽셀까지이며, PDF 한 파일당 OCR 대상은 최대 8쪽입니다. 추출 내용에는 페이지와 OCR 표시가 붙고 기존 자료와 같은 방식으로 색인됩니다. 이미 올린 스캔 문서는 OCR 도구를 설치한 후 재색인합니다.

## 병렬 Agent

채팅 입력창의 **+ → 파일·지식**에서 **병렬 Agent 분석**을 선택합니다. 도구 호출이 가능한 vLLM/Ollama 모델이 `parallel_analyze`를 선택하면 현재 질문을 2~3개의 독립적인 분석 과제로 나눕니다. 각 작업은 현재 질문과 이번 요청에서 수집한 발췌를 사용합니다. 메인 모델이 결과를 모아 답변하며 작업별 진행·실패는 답변의 작업 기록에서 볼 수 있습니다.

하위 작업은 읽기 전용 분석이고 별도 도구 호출 권한을 받지 않습니다. 동시 실행은 사용자·서버·모델 한도를 공유합니다. 테스트 모드에서는 작업 분배와 취합을 예시로 확인할 수 있습니다.

메인 Agent가 최종 응답을 작성하는 구조와 병렬 실행 패턴은 OpenAI Agents SDK 공식 문서를 참고했습니다. 모델 호출은 이 앱에 등록된 vLLM/Ollama 어댑터를 사용합니다. [Agents as tools](https://openai.github.io/openai-agents-python/agents/#manager-agents-as-tools) · [병렬 처리 예제](https://github.com/openai/openai-agents-python/blob/main/examples/agent_patterns/parallelization.py)

## 내부 임베딩·Qdrant·재정렬

서버 환경 예시:

```dotenv
KNOWLEDGE_EMBEDDING_PROVIDER=vllm
KNOWLEDGE_EMBEDDING_URL=http://embedding.internal:8002/v1
KNOWLEDGE_EMBEDDING_MODEL=your-local-embedding-model
KNOWLEDGE_EMBEDDING_KEY=
KNOWLEDGE_VECTOR_STORE=sqlite
```

Ollama는 provider를 `ollama`, URL을 `http://ollama.internal:11434`처럼 서버 루트로 설정합니다. vLLM 호환 `/embeddings`, Ollama `/api/embed`의 dense 벡터를 검증·정규화합니다. 키워드 후보와 벡터 후보를 RRF로 결합하고, 구성한 경우 `/rerank` 호환 재정렬 서버를 사용합니다. 언어·자료 유형에 맞는 모델은 실제 질문으로 평가하세요. 현재 dense endpoint에서 sparse 벡터를 받는 구현은 아닙니다.

SQLite는 벡터를 DB에 저장하고 제한된 검색 범위 안에서 cosine 검색합니다. 별도 벡터 서버를 쓰려면:

```dotenv
KNOWLEDGE_VECTOR_STORE=qdrant
KNOWLEDGE_QDRANT_URL=http://qdrant.internal:6333
KNOWLEDGE_QDRANT_KEY=your-private-service-key
KNOWLEDGE_RERANK_URL=
KNOWLEDGE_RERANK_MODEL=
KNOWLEDGE_RERANK_KEY=
```

사설 인증서는 `ASSISTANT_CA_BUNDLE`에 API 런타임 내부의 PEM 경로를 지정하세요. 모델·검색·범용 MCP에 적용하며 TLS 검증은 유지합니다. GitLab과 Terminal 전용 설정은 기존 문서를 따릅니다.

Qdrant에는 벡터·문서 ID·소유자·버전만 저장하고 원문은 보내지 않습니다. SQL에서 현재 권한이 있는 문서 ID로 범위를 확정한 뒤 벡터 검색 필터에도 적용합니다. 결과를 현재 SQL 구간·버전과 다시 대조하므로 다른 사용자의 벡터나 삭제된 버전이 최종 근거로 들어갈 수 없습니다. 삭제는 SQL 접근을 즉시 차단하고 Qdrant 정리 작업을 남깁니다. 정리 실패는 이후 워커가 재시도합니다.

임베딩 provider·URL·모델·벡터 저장소 설정이 바뀌면 기존 자료를 재색인해야 합니다. 같은 이름으로 모델 가중치를 교체한 경우도 직접 재색인하세요. Qdrant 서버 주소를 변경할 때 이전 서버 데이터의 물리적 제거는 운영자가 별도로 확인해야 합니다.

Compose에서 Qdrant 추가:

```bash
docker compose -f compose.yaml -f compose.knowledge.yaml up -d --build
```

`KNOWLEDGE_QDRANT_KEY`와 내부 임베딩 설정을 먼저 등록합니다. Terminal MCP 오버레이와 함께 적용할 수 있습니다. Qdrant 포트는 이 파일에서 호스트에 공개하지 않습니다. Termux에서는 API·웹을 개별 실행하고 내부 임베딩/Qdrant·원격 MCP를 연결하면 됩니다. 문서 추출의 lxml이 필요할 때는 Termux용 개발 패키지/컴파일 환경이 필요할 수 있으며 실제 단말 설치는 별도 검증 대상입니다.

## 여러 MCP 앱 등록

운영자 계정으로 **+ → 파일·지식 → 운영**에서 HTTP MCP 서버를 등록합니다. 앱 이름·주소, 허용할 읽기/쓰기 도구 이름, 사용자 허용 목록, 인증 키 참조를 설정합니다. 연결·도구 조회 버튼은 실제 서버의 도구 목록을 보여줍니다. 목록에서 사용하려는 이름을 명시적으로 허용해야 합니다. 서버의 readOnly annotation만 보고 실행 권한을 부여하지 않습니다.

같은 도구명은 서버별 내부 이름으로 분리합니다. 도구 입력 JSON Schema를 검증하고 외부 `$ref` 네트워크 조회는 차단합니다. 모델은 선택된 서버의 허용 도구만 받으며 호출 직전·직후 현재 등록/계정 권한을 확인합니다. 쓰기는 운영자가 write 목록에 등록하고 사용자가 해당 대화의 **쓰기 도구 허용**을 선택해야 합니다. 기본은 읽기 전용입니다. 운영자는 서버를 비활성화할 수 있고 이전 버전으로 덮어쓰는 저장은 거부됩니다.

환경 등록도 지원합니다. DB에서 같은 ID를 저장하면 해당 등록을 우선합니다.

```dotenv
MCP_SERVERS_JSON=[{"id":"internal-search","name":"사내 검색","url":"http://search.internal:8011/mcp","secret_ref":"search","read_tools":["search","read_document"],"write_tools":[],"allowed_users":["alice"]}]
MCP_SECRETS_JSON={"search":"your-private-service-token"}
```

인증 값은 서버 환경의 참조로 관리하고 브라우저·DB 등록에는 실제 값을 저장하지 않습니다. 현재 범용 MCP 인증은 운영자가 설정한 서비스 Bearer 키 방식입니다. GitLab의 개인 OAuth는 기존 기능을 사용하며, 임의 MCP의 사용자별 OAuth 자동 등록/로그인은 제공하지 않습니다. 기존 Terminal MCP는 별도의 사용자·대화·실행 권한 서명을 계속 사용합니다.

공용 서비스 키를 사용하는 MCP의 데이터 범위는 운영자가 결정합니다. 브라우저처럼 상태/쿠키를 유지하는 서버는 사용자별 독립 인스턴스와 사용자 1명의 허용 목록으로 등록하거나, 자체적으로 사용자 격리를 제공해야 합니다. 여러 사용자가 하나의 브라우저 세션을 공유하는 구성은 사용하지 마세요. 브라우저·DB·문서 서비스는 이 공통 HTTP 연결에 등록할 수 있으며 해당 서버의 설치·라이선스·네트워크 설정은 별도입니다.

## 맞춤 설정과 기억

사용자가 답변 지침과 기억을 직접 등록·삭제합니다. 기억 사용은 기본 꺼짐이며 등록한 기억만 다음 대화 입력에 포함합니다. 대화를 보고 자동으로 개인정보를 영구 저장하지 않습니다. 개인 기록은 계정별로 분리하고 설정 변경에는 revision 충돌 검사를 적용합니다. 현재 입력에 포함하는 개인 설정·기억은 합계 6KB로 제한합니다.

## 현재 범위와 검증

실제 모델의 도구 선택과 검색 품질은 사용하는 폐쇄망 모델과 자료로 평가합니다. [검증 결과](VALIDATION.md)


## 질문에 따른 자동 GitLab·문서 검색

프로젝트 경로를 포함한 분석 요청은 모델의 구조화된 판단 후 내 GitLab 연결에서 프로젝트를 조회하고 읽기 전용 Terminal MCP 분석으로 이어집니다. 자동 RAG는 준비된 내 자료·공유 자료·현재 대화 첨부에서만 검색합니다. 근거가 부족한 초안은 보류하고 제한된 횟수로 재검색·재작성하며, 확인하지 못하면 보류 답변을 저장합니다.

설정 모달의 자동 처리·검색 방식, 서버의 ASSISTANT_* 설정, 실제 코드 분석 알고리즘·한계와 실행 검증 결과는 [AUTOMATIC_ASSISTANT.md](AUTOMATIC_ASSISTANT.md)에 있습니다. 실제 폐쇄망 연결·모델 품질·단말 확인은 별도로 필요하며, 근거 점검은 모든 할루시네이션 탐지를 보장하지 않습니다.
