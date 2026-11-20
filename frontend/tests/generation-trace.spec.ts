import { expect, test, type Page } from "@playwright/test";

const conversationId = "ef156856-5b47-4683-8fbd-9be098a5de03";
const requestId = "f32ff30e-fdd7-49d8-8a30-d9c414b850cc";
const created = Math.floor(Date.now() / 1000);

async function signup(page: Page, username: string) {
  await page.goto("/user/signup");
  await page.getByLabel("표시 이름", { exact: true }).fill("작업 기록 사용자");
  await page.getByLabel("사용자명", { exact: true }).fill(username);
  await page.getByLabel("이메일", { exact: true }).fill(`${username}@example.com`);
  await page.getByLabel("비밀번호", { exact: true }).fill("GenerationTracePass123!");
  await page.getByLabel("비밀번호 확인", { exact: true }).fill("GenerationTracePass123!");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "회원가입", exact: true }).click();
  await expect(page.getByRole("heading", { name: "무엇을 함께 해볼까요?" })).toBeVisible();
}

test("추론·최종 답변을 구분하고 완료·새로고침 후 실제 작업 기록을 복원한다", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await signup(page, `trace_${info.project.name}_${Date.now()}`);
  let complete = false;
  const snapshot = () => {
    const conversation = { id: conversationId, title: "실시간 작업 과정 검증", model: "gemma-test", connection_id: "demo", busy: !complete,
      created_at: created, updated_at: created };
    return { conversation, generation: complete ? null : { status: "running", position: 0, request_id: requestId },
      message_page: { older_before: null, newer_after: null }, messages: [
        { id: "trace-user", role: "user", content: "로그인 소스를 확인해 줘.", status: "complete", model: "gemma-test", is_test: false, created_at: created },
        { id: "trace-answer", role: "assistant", content: "## 최종 답변\n\n실제 소스에서 로그인을 확인했습니다.", status: complete ? "complete" : "pending", model: "gemma-test", is_test: false, created_at: created,
          generation_trace: { reasoning: "서버가 공개한 추론 텍스트입니다.", reasoning_truncated: false,
            steps: [{ id: "read-one", operation_id: "read-one", request_id: requestId, name: "workspace_read_file", step: 1, status: "complete", detail: "src/auth/login.py:12-36", started_at_ms: created * 1000, finished_at_ms: created * 1000 + 220, duration_ms: 220 },
              { id: "model-one", operation_id: "model-one", request_id: requestId, name: "model", step: 2, status: complete ? "complete" : "running", detail: "조회한 소스를 바탕으로 답변합니다.", started_at_ms: created * 1000 + 220, ...(complete ? { duration_ms: 980, finished_at_ms: created * 1000 + 1200 } : {}) }],
            ...(complete ? { metrics: { duration_ms: 1200, first_token_ms: 320, input_tokens: 120, output_tokens: 24, tokens_per_second: 16.3, is_estimate: false, timing_source: "client_observed", token_source: "server_reported" } } : {}) } },
      ] };
  };
  await page.route(`**/api/chat/conversations/${conversationId}**`, async route => {
    if (route.request().method() !== "GET") return route.fulfill({ json: {} });
    return route.fulfill({ json: snapshot() });
  });
  await page.goto(`/c/${conversationId}`);
  const trace = page.getByRole("region", { name: "AI 작업 과정", exact: true });
  const toggle = trace.getByRole("button", { name: /AI 작업 과정/ });
  await expect(toggle).toHaveAttribute("aria-expanded", "true");
  await expect(trace.getByLabel("모델 추론 출력")).toContainText("서버가 공개한 추론 텍스트입니다.");
  const answer = page.getByRole("article", { name: "모델 답변" }).locator(".cw-markdown");
  await expect(answer).toContainText("실제 소스에서 로그인을 확인했습니다.");
  await expect(answer).not.toContainText("서버가 공개한 추론");
  await trace.getByText("파일 읽기", { exact: true }).click();
  await expect(trace.getByText("src/auth/login.py:12-36", { exact: true })).toBeVisible();
  complete = true;
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await expect(trace.getByLabel("모델 추론 출력")).toHaveCount(0);
  await toggle.click();
  await expect(trace.getByLabel("모델 추론 출력")).toBeVisible();
  await expect(trace).toContainText("16.3 토큰/초");
  await page.reload();
  await expect(toggle).toHaveAttribute("aria-expanded", "false");
  await toggle.click();
  await expect(trace.getByLabel("모델 추론 출력")).toContainText("서버가 공개한 추론 텍스트입니다.");
  await expect(trace).toContainText("320ms");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("generation-trace.png"), fullPage: true });
  expect(errors).toEqual([]);
});

test("추론 미지원 모델의 도구 기록은 취소 후에도 남고 내부 사고를 만들어 내지 않는다", async ({ page }, info) => {
  await signup(page, `trace_stop_${info.project.name}_${Date.now()}`);
  let stopped = false;
  await page.route(`**/api/chat/conversations/${conversationId}**`, async route => {
    if (new URL(route.request().url()).pathname.endsWith("/cancel")) { stopped = true; return route.fulfill({ status: 204 }); }
    if (route.request().method() !== "GET") return route.fulfill({ json: {} });
    const conversation = { id: conversationId, title: "도구 실행 취소", model: "no-reasoning", connection_id: "demo", busy: !stopped,
      created_at: created, updated_at: created };
    return route.fulfill({ json: { conversation, generation: stopped ? null : { status: "running", position: 0, request_id: requestId },
      message_page: { older_before: null, newer_after: null }, messages: [
        { id: "tool-user", role: "user", content: "테스트 명령을 실행해 줘.", status: "complete", created_at: created },
        { id: "tool-answer", role: "assistant", content: "", status: stopped ? "cancelled" : "pending", model: "no-reasoning", is_test: false, created_at: created,
          generation_trace: { reasoning: "", reasoning_truncated: false, steps: [
            { id: "terminal-one", name: "terminal_exec", step: 1, status: stopped ? "cancelled" : "running", detail: "python -m pytest tests/test_auth.py", started_at_ms: created * 1000,
              ...(stopped ? { finished_at_ms: created * 1000 + 1500, duration_ms: 1500 } : {}) },
          ] } },
      ] } });
  });
  await page.goto(`/c/${conversationId}`);
  const trace = page.getByRole("region", { name: "AI 작업 과정", exact: true });
  await expect(trace).toContainText("명령 실행");
  await expect(trace.getByText("모델이 제공한 추론")).toHaveCount(0);
  await page.getByRole("button", { name: "응답 중지", exact: true }).click();
  await expect(page.getByText("응답이 중지되었습니다.", { exact: true })).toBeVisible();
  await expect(trace.getByRole("button", { name: /AI 작업 과정/ })).toHaveAttribute("aria-expanded", "false");
  await trace.getByRole("button", { name: /AI 작업 과정/ }).click();
  await trace.getByText("명령 실행", { exact: true }).click();
  await expect(trace.getByText("python -m pytest tests/test_auth.py", { exact: true })).toBeVisible();
  await expect(trace).toContainText("중단");
  await expect(trace.getByLabel("모델 추론 출력")).toHaveCount(0);
});

test("GitLab 미연결 오류에서 질문을 복원하고 연결 모달에서도 작성 내용을 유지한다", async ({ page }, info) => {
  await signup(page, `trace_auth_${info.project.name}_${Date.now()}`);
  const question = "@gitlab team/service의 로그인 오류를 분석해 줘.";
  await page.route(`**/api/chat/conversations/${conversationId}**`, route => route.fulfill({ json: {
    conversation: { id: conversationId, title: "GitLab 연결 필요", model: "demo", connection_id: "demo", busy: false },
    message_page: { older_before: null, newer_after: null }, messages: [
      { id: "auth-user", role: "user", content: question, status: "complete", created_at: created },
      { id: "auth-answer", role: "assistant", content: "", status: "error", model: "demo", is_test: false, error: "GitLab 계정을 연결하세요.", created_at: created,
        recovery: { code: "app_connection", phase: "gitlab_resolve", requires_confirmation: false, can_retry: true, can_resume_execution: false, actions: ["check_docs"] } },
    ],
  } }));
  await page.goto(`/c/${conversationId}`);
  await page.getByRole("link", { name: "GitLab 연결", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "설정", exact: true });
  await expect(dialog).toBeVisible();
  await expect(page.locator("#chat-message")).toHaveValue(question);
  await dialog.getByRole("button", { name: "설정 닫기", exact: true }).click();
  const editor = page.getByLabel("메시지", { exact: true });
  await editor.fill("이미 작성 중인 다른 질문");
  await page.getByRole("link", { name: "GitLab 연결", exact: true }).click();
  await expect(dialog).toBeVisible();
  await expect(page.locator("#chat-message")).toHaveValue("이미 작성 중인 다른 질문");
});
