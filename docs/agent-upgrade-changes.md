# 에이전트 통합 고도화 변경 파일

기준 원격 main: `e4ad6a9d9410e1ba519dd0959b28b465a3b893ea`
작업 브랜치: `feat/offline-agent-trace-20261120`

기존 SSE·GenerationJobs·ToolTrace·DB 대기열·OAuth·Terminal MCP·문서 RAG·파일 생성·Canvas를 재사용했습니다. 새 테이블은 공개 추론/작업 메타데이터·모델 측정/검사·GitLab 영수증/상태·자동 제목 상태를 보완합니다. 제목은 같은 모델로 백그라운드에서 생성하며 설치·실행은 기존 명령을 유지합니다.

실행 결과는 [검증 기록](../VALIDATION.md), 구성과 제약은 [운영 설명](../AGENT_SYSTEM.md)에 정리했습니다. 브라우저 54개 시나리오는 현재 실행 환경의 Chromium 바이너리 부재·설치 제한으로 미검증이며 실제 폐쇄망 모델·GitLab·GPU 성능을 검증했다고 주장하지 않습니다.

| 파일 | 변경 목적 |
| --- | --- |
| `.env.example` | 공개 추론·자동 제목 설정을 한글 주석으로 안내 |
| `AGENT_SYSTEM.md` | 구현·설정·배포·검증 범위·미검증 환경 문서화 |
| `GITLAB_APPS.md` | 구현·설정·배포·검증 범위·미검증 환경 문서화 |
| `LLM_CONNECTIONS.md` | 구현·설정·배포·검증 범위·미검증 환경 문서화 |
| `OPERATIONS.md` | 구현·설정·배포·검증 범위·미검증 환경 문서화 |
| `README.md` | 구현·설정·배포·검증 범위·미검증 환경 문서화 |
| `VALIDATION.md` | 구현·설정·배포·검증 범위·미검증 환경 문서화 |
| `backend/.env.example` | 개별 실행의 공개 추론·자동 제목 설정을 한글 주석으로 안내 |
| `backend/app/main.py` | 기존 GitLab 계정 제약 업그레이드 시작 훅 |
| `backend/app/modules/admin/router.py` | 운영자 전용 실제 기능 검사·측정 API |
| `backend/app/modules/assistant/automatic.py` | API 탐색과 코드 작업 라우팅·단순 인사 빠른 처리·쓰기 권한 전달 |
| `backend/app/modules/assistant/context.py` | GitLab 탐색 요청의 일반 채팅 통합 |
| `backend/app/modules/assistant/engine.py` | GitLab 탐색 도구·실행 기록·자료 권한 재검사·도구 실패 복구 |
| `backend/app/modules/assistant/gitlab.py` | 명시적 빌드 요청 처리·현재 사용자 권한 파서 재사용 |
| `backend/app/modules/assistant/gitlab_tools.py` | 자연어 GitLab API 요청 인식과 공통 엔진 진입점 |
| `backend/app/modules/assistant/parallel.py` | 병렬 작업이 추론 슬롯 획득 전후 자료 권한 재검사 |
| `backend/app/modules/assistant/recovery.py` | 일시적인 읽기 전송 실패에만 한 차례 복구 |
| `backend/app/modules/assistant/repositories.py` | 최대 세 저장소 읽기 전용 정적 분석과 실제 소스 인용 |
| `backend/app/modules/assistant/routing.py` | API/여러 작업공간 출처 검증·SHA 근거 조회 |
| `backend/app/modules/chat/diagnostics.py` | 실제 모델 기능 검사·결과 보관·설정 변경 시 무효화 |
| `backend/app/modules/chat/jobs.py` | 기존 작업 관리자에서 제목의 비동기 실행·예외·종료 관리 |
| `backend/app/modules/chat/providers.py` | 추론 채널 분리·도구/JSON SSE 처리, 본문과 전송 외피 크기 제한 분리 |
| `backend/app/modules/chat/registry.py` | 기존 연결 설정과 추론 공개 설정 호환 |
| `backend/app/modules/chat/router.py` | 제목·정리 상태·작업 조회 API, 공통 중단 복구와 입력 경계 |
| `backend/app/modules/chat/scheduler.py` | 종료 작업 재진입 차단·추론 슬롯 일관성 |
| `backend/app/modules/chat/trace.py` | 공통 작업 ID·시간·보안 필터·추론 체크포인트 |
| `backend/app/modules/chat/trace_history.py` | 답변 버전·분기·오류 복원의 기록 일관성과 조회 시 비밀값 제거 |
| `backend/app/modules/chat/usage.py` | 서버 보고 토큰과 앱/서버 시간의 구분·운영 통계 |
| `backend/app/modules/integrations/actions.py` | 고정 GitLab 조회/이슈/리뷰 API·현재 요청 권한·영수증 |
| `backend/app/modules/integrations/config.py` | 비밀 파일의 이름 변경 Diff 우회 차단 |
| `backend/app/modules/integrations/merge_requests.py` | MR Diff 양쪽 경로의 비밀 파일 검사 |
| `backend/app/modules/integrations/models.py` | 다중 계정·쓰기 영수증·웹훅 상태 모델 |
| `backend/app/modules/integrations/router.py` | GitLab 탐색·웹훅 API와 소유자의 만료된 로컬 구독 해제 |
| `backend/app/modules/integrations/schema.py` | 기존 SQLite/PostgreSQL 계정 제약 업그레이드 |
| `backend/app/modules/integrations/service.py` | 기존 OAuth/API와 새 탐색·다중 계정·웹훅 연결 |
| `backend/app/modules/integrations/webhooks.py` | 인증된 이벤트·중복 방지, 오래된 구독 해제만 세대 검사 예외 적용 |
| `backend/app/modules/knowledge/providers.py` | 언어·식별자·파일명 검색과 선택형 외부 검색 장애 복구·응답 검증 |
| `backend/app/modules/knowledge/service.py` | 언어·식별자·파일명 검색과 선택형 외부 검색 장애 복구·응답 검증 |
| `backend/app/modules/workflows/conversations.py` | 보관 검색·공유 상태 비공개·고정 우선 정렬 후 페이지 조회 |
| `backend/app/modules/workflows/router.py` | 답변 버전·분기·쓰기 복구·작업 기록의 호환 조회 |
| `backend/app/modules/workspaces/service.py` | 원격 소스 변경과 같은 초 스냅샷 경합에서도 올바른 SHA 재사용 |
| `backend/terminal_mcp/analysis.py` | 본문/파일명 탐색·선택적 AST 색인·변경 해시·캐시 통계 |
| `backend/terminal_mcp/codeintel.py` | 본문/파일명 탐색·선택적 AST 색인·변경 해시·캐시 통계 |
| `backend/tests/test_agent_recovery.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `backend/tests/test_chat_tool_composition.py` | 기존 보안·오류·결과 검증을 유지하며 모의 모델을 실제 SSE/NDJSON 계약으로 갱신 |
| `backend/tests/test_codeintel_incremental.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `backend/tests/test_generation_diagnostics.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `backend/tests/test_generation_trace_integration.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `backend/tests/test_gitlab_agent_discovery.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `backend/tests/test_gitlab_discovery_actions.py` | GitLab 권한·쓰기에 더해 재인증/해제/서버 제거 후 구독 복구 회귀 검증 |
| `backend/tests/test_gitlab_merge_requests.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `backend/tests/test_image_generation.py` | 기존 보안·오류·결과 검증을 유지하며 모의 모델을 실제 SSE/NDJSON 계약으로 갱신 |
| `backend/tests/test_multimodal_workflows.py` | 기존 보안·오류·결과 검증을 유지하며 모의 모델을 실제 SSE/NDJSON 계약으로 갱신 |
| `backend/tests/test_parallel_and_gateway.py` | 기존 보안·오류·결과 검증을 유지하며 모의 모델을 실제 SSE/NDJSON 계약으로 갱신 |
| `backend/tests/test_reasoning_trace_provider.py` | 추론·답변 분리와 1,400조각 긴 응답·인수/추론 과다 출력 차단 회귀 검증 |
| `backend/tests/test_result_files.py` | 기존 보안·오류·결과 검증을 유지하며 모의 모델을 실제 SSE/NDJSON 계약으로 갱신 |
| `backend/tests/test_retrieval_incremental.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `backend/tests/test_ten_user_queue.py` | 10명 동시 처리·DB 한도, HTTPX 명시적 종료를 따르는 스트림 계측·전체 자원 반환 검증 |
| `backend/tests/test_trace_jobs_recovery.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `backend/tests/test_workspace_freshness.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `deploy/nginx.conf` | 모델 기능 전체 검사에 필요한 프록시 제한 시간 |
| `docs/agent-upgrade-changes.md` | 구현·설정·배포·검증 범위·미검증 환경 문서화 |
| `frontend/package.json` | 채팅 검사 명령에 작업 과정·자동 제목 계약 검사 포함 |
| `frontend/playwright.config.ts` | 브라우저 시나리오만 수집하여 Node 테스트 중복 실행 방지 |
| `frontend/src/app/api/admin/[...path]/route.ts` | 운영 진단 프록시 허용 경로·제한 시간 |
| `frontend/src/app/api/apps/[...path]/route.ts` | 서명 검증 웹훅의 공개 수신 경로와 일반 설정 인증 분리 |
| `frontend/src/components/account/AppConnections.tsx` | 여러 GitLab 계정 표시·재인증·웹훅 설정 통합 |
| `frontend/src/components/account/GitlabWebhookSettings.tsx` | 수동 웹훅 등록 정보·알림·수신 상태 설정 |
| `frontend/src/components/admin/AdminDashboard.tsx` | 기존 운영 모달에 실제 모델 진단·추론 공개 설정 통합 |
| `frontend/src/components/admin/CapabilityDiagnostics.tsx` | 모델 기능 선택 검사·실제 저장 결과 표시 |
| `frontend/src/components/admin/ConnectionManager.tsx` | 기존 운영 모달에 실제 모델 진단·추론 공개 설정 통합 |
| `frontend/src/components/admin/ModelTelemetry.tsx` | 모델별 실제 호출 시간·오류·도구 결과 표시 |
| `frontend/src/components/admin/admin.css` | 기존 디자인 체계에 맞춘 반응형 작업 과정·운영·연결 화면 |
| `frontend/src/components/chat/ChatWorkspace.tsx` | 실행 기록·제목·고정/보관·보관함·작업 센터의 기존 채팅 흐름 연결 |
| `frontend/src/components/chat/GenerationTrace.tsx` | 추론·도구·GitLab·측정 정보를 통합한 접이식 화면 |
| `frontend/src/components/chat/GitlabNotifications.tsx` | 사용자가 켠 웹훅 상태의 앱 내 알림 |
| `frontend/src/components/chat/GitlabWorkProgress.tsx` | 통합 작업 과정·재연결 초안·원격 쓰기 재시도 연결 |
| `frontend/src/components/chat/MessageWorkflow.tsx` | 통합 작업 과정·재연결 초안·원격 쓰기 재시도 연결 |
| `frontend/src/components/chat/activity-labels.ts` | 통합 작업 과정·재연결 초안·원격 쓰기 재시도 연결 |
| `frontend/src/components/chat/workspace.css` | 기존 디자인 체계에 맞춘 반응형 작업 과정·운영·연결 화면 |
| `frontend/src/components/workspace/workspace-shell.css` | 기존 디자인 체계에 맞춘 반응형 작업 과정·운영·연결 화면 |
| `frontend/src/lib/chat-stream.ts` | 공통 SSE·메시지·측정 타입 확장과 기존 형식 호환 |
| `frontend/src/lib/generation-trace.ts` | 고정 ID 갱신·출력 병합·실측 출처 처리 |
| `frontend/src/types/admin.ts` | 공통 SSE·메시지·측정 타입 확장과 기존 형식 호환 |
| `frontend/src/types/chat.ts` | 공개 추론·작업 측정과 선택형 제목 상태의 기존 API 호환 타입 |
| `frontend/tests/generation-trace.spec.ts` | 브라우저 시나리오 정의(화면 실행 미검증) |
| `frontend/tests/generation-trace.test.mjs` | 스트리밍·출력 병합·보안 프록시 계약 검사 |
| `frontend/tests/gitlab-settings.spec.ts` | 브라우저 시나리오 정의(화면 실행 미검증) |
| `frontend/tests/proxy.test.mjs` | 스트리밍·출력 병합·보안 프록시 계약 검사 |
| `nginx.config.file` | 독립 배포 Nginx의 같은 진단 제한 시간 |
| `backend/app/core/config.py` | 선택형 제목 생성·대기 포함 제한 시간 설정 |
| `backend/app/modules/chat/models.py` | 기존 대화 열을 보존하는 제목 작업·개인 정리 상태 보조 테이블 |
| `backend/app/modules/chat/titles.py` | 동일 모델 제목 생성·검증·권한 재검사·수동 수정 경합·실패 복구 |
| `backend/evaluate_titles.py` | 실제 모델·외부 응답·기준 제목 평가 명령, 자동 규칙과 의미 정확도 구분 |
| `backend/evaluations/chat_titles.ko.json` | 한글 중심 대표 질문·사람이 작성한 기준 제목·주제 단서 16개 |
| `backend/tests/conftest.py` | 다른 기능 계약 검사의 모델 호출 수에서 선택형 제목 기능 분리 |
| `backend/tests/test_chat_titles.py` | 제목 HTTP 계약·SSE 분리·실패·권한·수동 수정·삭제·평가 정직성 검증 |
| `frontend/src/lib/chat-titles.ts` | 소유한 제목 일괄 갱신·완료 상태·오래된 응답 버전 처리 |
| `frontend/tests/chat-titles.test.mjs` | 제목 갱신의 수동 수정 버전·다른 대화·기존 상태 보존 검사 |
| `scripts/smoke_standalone.py` | 실제 Python/Next에서 제목·고정/보관·작업 이력과 기존 사용자 흐름 BFF 검증 |
| `docs/chat-title-evaluation.md` | 실제 모델 평가 방법·사람 검토 기준·자동 지표 한계·자원 사용 안내 |
| `USER_WORKFLOWS.md` | 대화 고정·보관·복원·백그라운드 작업 센터 사용법 |
| `backend/app/modules/chat/organization.py` | 소유자의 고정·보관·수정 버전·새 질문 시 복원 정책 |
| `backend/app/modules/chat/runs.py` | 기존 실행 기록·대기열·Trace를 읽는 작업 센터와 공통 중단 복구 |
| `backend/tests/test_conversation_organization.py` | 고정·보관·검색·경합·본문 보존·공유 권한·삭제 정리 검증 |
| `backend/tests/test_job_center.py` | 작업 소유권·실제 단계·대기·취소 경합·중단 복구·비밀 미노출 검증 |
| `frontend/src/app/api/chat/[...path]/route.ts` | 작업 조회·개인 대화 정리의 BFF 허용 경로와 기존 CSRF 경계 |
| `frontend/src/components/chat/Icons.tsx` | 공통 SVG 아이콘 체계의 고정·보관 표시 |
| `frontend/src/components/chat/ArchivedChatsDialog.tsx` | 중앙 보관함의 검색·복원·삭제 확인·실패·로딩 상태 |
| `frontend/src/components/chat/JobCenterDialog.tsx` | 진행·완료/중단 작업, 실제 단계·대기·소요 시간·대화 링크·기존 취소 연결 |
| `frontend/src/components/chat/conversation-library.css` | 공통 토큰을 사용하는 반응형 작업/보관 모달과 정리 상태 |
| `frontend/src/lib/conversation-organization.ts` | 정리/제목 수정 버전 보호·고정 정렬·검색/프로젝트 목록 일관성 |
| `frontend/src/lib/job-center.ts` | 실제 작업 상태·중지 요청·경과 시간·미확인 표시 |
| `frontend/tests/conversation-library.test.mjs` | 정리 버전 경합·목록 보존·작업 시간·취소 표시 계약 검증 |
| `frontend/tests/conversation-library.spec.ts` | 데스크톱/모바일 보관·복원·작업 모달·초안 시나리오(실제 화면 미검증) |
| `docs/chatgpt-benchmark.md` | 공식 자료 기반 20항목 기능 대응표·65%/70% 산정 범위·미측정 품질 분리 |
