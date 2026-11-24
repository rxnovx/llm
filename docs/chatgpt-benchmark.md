# ChatGPT 기능 대응표 — 2026-11-24

이 표는 ChatGPT의 공식 기능 안내를 참고해 선정한 **20개 사용자 흐름의 구현 범위**를 코드·로컬 계약 검사로 검토한 결과입니다. 제품 전체의 동기화율·모델 지능·정답률·디자인 유사도·보안 인증 점수는 아닙니다. 기능 수와 가중치는 공개한 이 표의 범위에만 적용됩니다.

기준 코드: PR #12의 `be732ac5d6b3172056ba21cee88c3365f7811022`. 원격 main 기준은 `e4ad6a9d9410e1ba519dd0959b28b465a3b893ea`입니다. “변경 전”은 기준 코드를 이번에 소급 검토한 값이며, “이번 변경”은 대화 정리·작업 센터를 포함합니다.

## 산정 기준

- **1점:** 아래에 정의한 핵심 흐름에 필요한 앱 경로·권한·저장·UI가 구현되어 있습니다. 실제 모델과의 품질 동등성을 뜻하지 않습니다.
- **0.5점:** 대응 기능이 있으나 명시한 범위의 일부만 제공합니다.
- **0점:** 대응하는 사용자 흐름이 없습니다.
- 모두 같은 가중치를 적용합니다. 외부 모델·도구가 필요한 기능은 배포 설정이 갖춰져야 사용할 수 있습니다. 모의 계약 통과를 실제 서버 통합 성공으로 간주하지 않습니다.

**변경 전 13/20점 = 65%, 이번 변경 14/20점 = 70%입니다.**

| 번호 | 비교할 핵심 흐름 | 변경 전 | 이번 변경 | 코드 근거·남은 차이 |
| --- | --- | --- | --- | --- |
| 1 | 일반 대화·스트리밍·Markdown | 1 | 1 | `chat/router.py`, `MessageContent.tsx`: 한국어·수식·코드·표·답변 채널 |
| 2 | 긴 이력·본문 검색·직접 링크 | 1 | 1 | `chat/history.py`, `workflows/conversations.py`: 페이지 조회·검색 일치 위치·`/c/{id}` |
| 3 | 제목·고정·보관·검색·복원 | 0.5 | 1 | `chat/titles.py`, `chat/organization.py`, `ArchivedChatsDialog.tsx`: 이번에 고정·보관과 버전 충돌 보호 추가 |
| 4 | 질문 수정·분기·재생성·답변 버전 | 1 | 1 | `workflows/conversations.py`, `MessageWorkflow.tsx`: 원본·버전·수정 초안 보존 |
| 5 | Canvas 편집 작업 | 0.5 | 0.5 | `chat/canvas.py`, `CanvasPanel.tsx`: Markdown 편집·저장·미리보기 중심. 범용 앱 실행 환경은 없음 |
| 6 | 문서·Office·스캔 PDF 읽기 | 0.5 | 0.5 | `knowledge/extract.py`, `knowledge/ocr.py`: 텍스트 추출·로컬 OCR 중심. 복잡한 문서 배치·도형 의미 보존은 제한 |
| 7 | 실제 결과 파일 생성·다운로드·수정 | 1 | 1 | `workflows/artifacts.py`, `artifact_revision.py`: 문서·표·발표·HTML·ZIP 등 실제 파일과 후속 수정 |
| 8 | 자유로운 데이터 분석 | 0.5 | 0.5 | `workflows/table_analysis.py`: 전체 행 결합·집계·차트. 일반 문서 첨부용 임의 Python 분석 샌드박스는 없음 |
| 9 | 문서 RAG·근거·다단계 재검색 | 0.5 | 0.5 | `knowledge/service.py`, `assistant/automatic.py`: 키워드·선택형 벡터/재정렬·출처·제한된 재검색. 복합 질문의 장기 다단계 탐색은 제한 |
| 10 | 이미지 이해·생성·편집 자동 선택 | 1 | 1 | `workflows/image_inputs.py`, `images.py`, `assistant/automatic.py`: 모델 기능과 생성/편집 서버 설정 필요 |
| 11 | 음성 입력·음성 대화 | 0.5 | 0.5 | `workflows/audio.py`: WAV 생성·재생은 지원. 마이크 음성 인식·양방향 실시간 대화는 없음 |
| 12 | 웹 검색·심층 연구 | 0.5 | 0.5 | `assistant/web_search.py`: 선택형 검색 서버·출처. 실제 웹 페이지 탐색·장기 연구 작업은 제한 |
| 13 | 연결 앱 생태계·자동 선택 | 0.5 | 0.5 | `integrations/`, `assistant/mcp.py`: GitLab OAuth·관리자가 정의한 MCP. 범용 앱 스토어·여러 서비스별 OAuth는 없음 |
| 14 | 저장소 개발·검증·원격 게시 | 1 | 1 | `workspaces/agent.py`, `integrations/publishing.py`: Clone·분석·수정·로컬 검사·커밋·게시·신규/기존 MR |
| 15 | 도구 조합·병렬 읽기 Agent | 1 | 1 | `assistant/engine.py`, `parallel.py`: 권한을 확인하며 도구 조합·2~3개 독립 분석 |
| 16 | 프로젝트 지침·개인 기억 | 0.5 | 0.5 | `workflows/projects.py`, `assistant/personal_tools.py`: 지침·자료·명시적 기억 저장. 자동 개인화·장기 기억 선택은 제한 |
| 17 | 일회성·반복 예약·실행 이력 | 1 | 1 | `schedules/`, `ScheduledTasksDialog.tsx`: 앱 내 예약·결과 대화·중지·이력 |
| 18 | 백그라운드 작업·중지·재접속 | 0.5 | 1 | `chat/runs.py`, `JobCenterDialog.tsx`: 이번에 소유자별 작업 센터·현재 단계·대기·완료/중단 이력·기존 취소 연결 추가 |
| 19 | 브라우저·컴퓨터 직접 조작 | 0 | 0 | 앱 자체의 브라우저/데스크톱 실행 환경·인증 인계 UI 없음 |
| 20 | 임시 대화·별도 보존 정책 | 0 | 0 | 자동 만료되는 임시 대화와 그에 맞춘 파일·기억 보존 흐름 없음 |

경로의 `chat/`, `workflows/` 등은 `backend/app/modules/` 아래이며 TSX 파일은 `frontend/src/components/chat/` 아래입니다. 이름이 비슷한 모듈의 존재만으로 점수를 부여하지 않고 위 표의 실제 처리 범위와 제약을 함께 검토했습니다. 예를 들어 파일 ZIP의 `archives.py`를 대화 보관 기능으로 세지 않았습니다.

## 별도로 측정해야 할 것

| 평가 축 | 현재 상태 | 측정 방법 |
| --- | --- | --- |
| 구현 기능 대응 범위 | 위 점검표상 70% | 같은 20항목과 가중치를 유지하며 변경 내용·검사 근거를 갱신 |
| 실제 모델의 답변·도구 선택 정확도 | 미측정 | 공개 대표 질문·기대 도구·정답/출처·거절 조건을 마련하고 동일 모델 설정으로 반복 실행 |
| RAG 품질 | 미측정 | 정답 문서가 검색되는지, 답변의 주장과 인용이 일치하는지, 근거 부족을 구분하는지 평가 |
| 개발 에이전트 성공률 | 실제 폐쇄망 서버 미측정 | 알려진 오류가 있는 저장소에서 수정 전 실패·수정 후 통과·Diff·커밋·MR을 대조 |
| 화면·모바일 사용성 | 이번 실행 환경에서 실제 화면 미검증 | 데스크톱·모바일 브라우저에서 고정/보관/검색·모달·초안·진행 작업을 조작 |
| 실제 10명 GPU 처리량·속도 | 미측정 | H100·실제 서빙 구성에서 대기·TTFT·완료 시간·실패율·메모리 측정 |

테스트 개수·API 성공률·제목의 단어 일치를 전체 정답률로 환산하지 않습니다. ChatGPT와 답변 품질을 직접 비교하려면 같은 공개 질문·자료와 동일한 채점 기준으로 양쪽 결과를 평가해야 합니다. 아직 그 비교 결과가 없으므로 품질 동등성 백분율은 제시하지 않습니다.

## 이번 개선의 사용법과 제약

- 대화 메뉴의 **대화 고정 / 고정 해제**, **보관 / 보관 해제**를 사용합니다. 보관은 고정을 해제하고 일반 목록에서만 숨깁니다. 본문 검색에는 보관된 대화도 포함됩니다.
- 사이드바 대화 기록 옆 **보관된 대화 열기**에서 검색·열기·복원·삭제할 수 있습니다. 삭제는 확인 후 수행하며 복구할 수 없습니다. 공유 프로젝트에서 개인의 정리 상태를 다른 구성원에게 공개하지 않습니다.
- 보관한 대화의 URL과 자료는 유지합니다. 새 질문을 보내면 일반 기록으로 돌아오며 기존 답변의 재생성은 보관 상태를 유지합니다. 보관은 개인정보 삭제·기억 사용 중지 기능이 아닙니다.
- **작업 센터**에서 자신의 진행 작업과 완료·중단 이력을 봅니다. 다른 대화로 이동해도 서버 작업은 이어지며 대화로 돌아가 전체 실행 기록을 확인할 수 있습니다.
- 작업 센터는 기존 `GenerationRun`·입장 제어·Trace를 읽습니다. 제목·병렬 하위 모델 호출을 별도 사용자 작업으로 중복 표시하지 않습니다. 중지는 기존 요청 ID를 사용하며 새 시도를 잘못 취소하지 않습니다.
- 서버 중단은 기존 대화 복구와 같은 경로로 오류 처리합니다. 부분 답변은 유지하며 쓰기 작업을 자동 재실행하지 않습니다. 실제 종료 시각을 알 수 없는 중단의 소요 시간은 미확인으로 표시합니다.
- SQLite·PostgreSQL을 위한 별도 보조 테이블 하나를 추가합니다. 기존 대화 열·본문·계정을 바꾸지 않습니다. Docker Compose·개별 실행·Termux 설치 명령과 모델 서버 설정은 그대로입니다.

## 공식 비교 자료

2026-11-24에 확인했습니다. 요금제·지역·조직 설정에 따라 ChatGPT의 제공 기능이 다를 수 있습니다. 이 프로젝트는 명시한 핵심 흐름을 참고하며 OpenAI의 내부 구현을 복제하거나 내부 사고 과정을 추정하지 않습니다.

- [ChatGPT capabilities overview](https://help.openai.com/en/articles/9260256-chatgpt-capabilities-overview)
- [Deleting and archiving chats](https://help.openai.com/en/articles/8809935-deleting-and-archiving-chats-in-chatgpt)
- [ChatGPT release notes — pinned chats](https://help.openai.com/en/articles/6825453-chatgpt-release-notes)
- [ChatGPT Work and Codex](https://help.openai.com/en/articles/20001275-chatgpt-work-and-codex)
- [Projects in ChatGPT](https://help.openai.com/en/articles/10169521-projects-in-chatgpt)
- [Finding chats, projects and files](https://help.openai.com/en/articles/10056348-finding-your-chats-projects-and-files-in-chatgpt)
