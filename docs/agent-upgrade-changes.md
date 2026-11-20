# 에이전트 통합 고도화 변경 파일

기준 원격 main: `e4ad6a9d9410e1ba519dd0959b28b465a3b893ea`
작업 브랜치: `feat/offline-agent-trace-20261120`

기존 SSE·GenerationJobs·ToolTrace·DB 대기열·OAuth·Terminal MCP·문서 RAG·파일 생성·Canvas를 재사용했습니다. 새 테이블은 공개 추론/작업 메타데이터·모델 측정/검사·GitLab 영수증/상태를 보완합니다. 설치·실행은 기존 명령을 유지합니다.

실행 결과는 [검증 기록](../VALIDATION.md), 구성과 제약은 [운영 설명](../AGENT_SYSTEM.md)에 정리했습니다. 브라우저 50개 시나리오는 실행 환경의 Chromium 소켓 제한으로 미검증이며 실제 폐쇄망 모델·GitLab·GPU 성능을 검증했다고 주장하지 않습니다.

| 파일 | 변경 목적 |
| --- | --- |
| `.env.example` | 추론 공개의 실제 설정 키와 기본값을 한글 주석으로 안내 |
| `AGENT_SYSTEM.md` | 구현·설정·배포·검증 범위·미검증 환경 문서화 |
| `GITLAB_APPS.md` | 구현·설정·배포·검증 범위·미검증 환경 문서화 |
| `LLM_CONNECTIONS.md` | 구현·설정·배포·검증 범위·미검증 환경 문서화 |
| `OPERATIONS.md` | 구현·설정·배포·검증 범위·미검증 환경 문서화 |
| `README.md` | 구현·설정·배포·검증 범위·미검증 환경 문서화 |
| `VALIDATION.md` | 구현·설정·배포·검증 범위·미검증 환경 문서화 |
| `backend/.env.example` | 추론 공개의 실제 설정 키와 기본값을 한글 주석으로 안내 |
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
| `backend/app/modules/chat/jobs.py` | 미처리 예외에서 민감 원문 로그 제거 |
| `backend/app/modules/chat/providers.py` | vLLM/Ollama 공개 추론·구조화/도구 SSE 계약 |
| `backend/app/modules/chat/registry.py` | 기존 연결 설정과 추론 공개 설정 호환 |
| `backend/app/modules/chat/router.py` | 백그라운드 SSE에 작업 과정 연결·중단 복구·쓰기 재시도 검사 |
| `backend/app/modules/chat/scheduler.py` | 종료 작업 재진입 차단·추론 슬롯 일관성 |
| `backend/app/modules/chat/trace.py` | 공통 작업 ID·시간·보안 필터·추론 체크포인트 |
| `backend/app/modules/chat/trace_history.py` | 답변 버전·분기·오류 복원의 기록 일관성과 조회 시 비밀값 제거 |
| `backend/app/modules/chat/usage.py` | 서버 보고 토큰과 앱/서버 시간의 구분·운영 통계 |
| `backend/app/modules/integrations/actions.py` | 고정 GitLab 조회/이슈/리뷰 API·현재 요청 권한·영수증 |
| `backend/app/modules/integrations/config.py` | 비밀 파일의 이름 변경 Diff 우회 차단 |
| `backend/app/modules/integrations/merge_requests.py` | MR Diff 양쪽 경로의 비밀 파일 검사 |
| `backend/app/modules/integrations/models.py` | 다중 계정·쓰기 영수증·웹훅 상태 모델 |
| `backend/app/modules/integrations/router.py` | 기존 OAuth/API와 새 탐색·다중 계정·웹훅 연결 |
| `backend/app/modules/integrations/schema.py` | 기존 SQLite/PostgreSQL 계정 제약 업그레이드 |
| `backend/app/modules/integrations/service.py` | 기존 OAuth/API와 새 탐색·다중 계정·웹훅 연결 |
| `backend/app/modules/integrations/webhooks.py` | 인증·중복 방지·프로젝트 범위를 검사하는 상태 수신 |
| `backend/app/modules/knowledge/providers.py` | 언어·식별자·파일명 검색과 선택형 외부 검색 장애 복구·응답 검증 |
| `backend/app/modules/knowledge/service.py` | 언어·식별자·파일명 검색과 선택형 외부 검색 장애 복구·응답 검증 |
| `backend/app/modules/workflows/conversations.py` | 답변 버전·분기·쓰기 복구·작업 기록의 호환 조회 |
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
| `backend/tests/test_gitlab_discovery_actions.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `backend/tests/test_gitlab_merge_requests.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `backend/tests/test_image_generation.py` | 기존 보안·오류·결과 검증을 유지하며 모의 모델을 실제 SSE/NDJSON 계약으로 갱신 |
| `backend/tests/test_multimodal_workflows.py` | 기존 보안·오류·결과 검증을 유지하며 모의 모델을 실제 SSE/NDJSON 계약으로 갱신 |
| `backend/tests/test_parallel_and_gateway.py` | 기존 보안·오류·결과 검증을 유지하며 모의 모델을 실제 SSE/NDJSON 계약으로 갱신 |
| `backend/tests/test_reasoning_trace_provider.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `backend/tests/test_result_files.py` | 기존 보안·오류·결과 검증을 유지하며 모의 모델을 실제 SSE/NDJSON 계약으로 갱신 |
| `backend/tests/test_retrieval_incremental.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `backend/tests/test_ten_user_queue.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `backend/tests/test_trace_jobs_recovery.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `backend/tests/test_workspace_freshness.py` | 기능·권한·경합·실패·회귀 시나리오의 로컬 계약 검증 |
| `deploy/nginx.conf` | 모델 기능 전체 검사에 필요한 프록시 제한 시간 |
| `docs/agent-upgrade-changes.md` | 구현·설정·배포·검증 범위·미검증 환경 문서화 |
| `frontend/package.json` | 채팅 검사 명령에 새 작업 과정 계약 검사 포함 |
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
| `frontend/src/components/chat/ChatWorkspace.tsx` | 통합 작업 과정·재연결 초안·원격 쓰기 재시도 연결 |
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
| `frontend/src/types/chat.ts` | 공통 SSE·메시지·측정 타입 확장과 기존 형식 호환 |
| `frontend/tests/generation-trace.spec.ts` | 브라우저 시나리오 정의(화면 실행 미검증) |
| `frontend/tests/generation-trace.test.mjs` | 스트리밍·출력 병합·보안 프록시 계약 검사 |
| `frontend/tests/gitlab-settings.spec.ts` | 브라우저 시나리오 정의(화면 실행 미검증) |
| `frontend/tests/proxy.test.mjs` | 스트리밍·출력 병합·보안 프록시 계약 검사 |
| `nginx.config.file` | 독립 배포 Nginx의 같은 진단 제한 시간 |
