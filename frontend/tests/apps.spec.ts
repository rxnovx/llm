import { expect, test } from "@playwright/test";

test("@GitLab 파일 선택·출처와 설정 모달이 채팅을 유지한다", async ({ page }, info) => {
  const connection = "77777777-7777-4777-8777-777777777777";
  await page.route("**/api/apps/**", async (route) => {
    const url = new URL(route.request().url());
    if (url.pathname.endsWith("/catalog")) return route.fulfill({ json: { apps: [{ id: "gitlab", name: "GitLab", description: "코드 조회",
      providers: [{ id: "office", name: "사내 GitLab", base_url: "https://gitlab.example", ready: true }],
      connections: [{ id: connection, provider_id: "office", username: "alice", status: "connected" }] }] } });
    if (url.pathname.endsWith("/branches")) return route.fulfill({ json: { items: [{ name: "main" }], has_more: false } });
    if (url.pathname.endsWith("/tree")) return route.fulfill({ json: { items: [
      { name: "main.py", path: "main.py", type: "blob", selectable: true }, { name: ".env", path: ".env", type: "blob", selectable: false },
    ], has_more: false } });
    return route.fulfill({ json: { items: [{ id: 7, name: "team/service", default_branch: "main" }], has_more: false } });
  });
  await page.goto("/user/signup");
  const username = `app_${info.project.name}_${Date.now()}`;
  await page.getByLabel("표시 이름", { exact: true }).fill("앱 사용자");
  await page.getByLabel("사용자명", { exact: true }).fill(username);
  await page.getByLabel("이메일", { exact: true }).fill(`${username}@example.com`);
  await page.getByLabel("비밀번호", { exact: true }).fill("BrowserAppsPass123!");
  await page.getByLabel("비밀번호 확인", { exact: true }).fill("BrowserAppsPass123!");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "회원가입", exact: true }).click();
  await expect(page.getByRole("heading", { name: "무엇을 함께 해볼까요?" })).toBeVisible();
  await page.getByLabel("메시지", { exact: true }).fill("함수 분석 @Git");
  await page.getByLabel("메시지", { exact: true }).press("Enter");
  const dialog = page.getByRole("dialog", { name: "GitLab에서 가져오기" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: /team\/service/ }).click();
  await dialog.getByRole("checkbox", { name: ".env", exact: true }).isDisabled().then((disabled) => expect(disabled).toBe(true));
  await dialog.getByRole("checkbox", { name: "main.py", exact: true }).check();
  await dialog.getByRole("button", { name: "이 질문에 추가", exact: true }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.getByLabel("메시지", { exact: true })).toHaveValue("함수 분석 ");
  await expect(page.locator(".cw-app-selection")).toContainText("1개 파일");
  await page.route("**/api/chat/conversations/*/messages", async (route) => {
    const request = route.request().postDataJSON();
    expect(request.apps[0]).toEqual({ app: "gitlab", connection_id: connection, project_id: 7, ref: "main", paths: ["main.py"] });
    const conversation = { id: new URL(route.request().url()).pathname.split("/").at(-2), title: "함수 분석", model: "demo", connection_id: "demo", busy: false };
    const human = { id: "user-one", role: "user", content: "함수 분석", status: "complete" };
    const assistant = { id: "answer-one", role: "assistant", content: "", status: "pending", apps: request.apps, sources: [] };
    const source = { id: "GL1", app: "gitlab", project: "team/service", path: "main.py", commit: "a".repeat(40), start_line: 1, end_line: 4, url: "https://gitlab.example/team/service/-/blob/"+"a".repeat(40)+"/main.py#L1-4" };
    const event = (name: string, data: unknown) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`;
    await route.fulfill({ contentType: "text/event-stream", body: event("start", { conversation, user_message: human, assistant_message: assistant })
      + event("tool", { app: "gitlab", status: "reading", detail: "파일을 읽고 있습니다." }) + event("sources", { sources: [source] })
      + event("delta", { content: "함수를 분석했습니다. [GL1]" }) + event("done", { conversation, message: { ...assistant, status: "complete", content: "함수를 분석했습니다. [GL1]", sources: [source] } }) });
  });
  await page.getByRole("button", { name: "메시지 전송", exact: true }).click();
  await expect(page.getByRole("link", { name: /\[GL1\] main.py/ })).toBeVisible();
  await page.getByLabel("메시지", { exact: true }).fill("다음 질문 유지");
  await page.getByRole("button", { name: "앱 추가", exact: true }).click();
  await page.getByRole("option", { name: /GitLab/ }).click();
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "GitLab 선택 닫기" }).click();
  await expect(page.getByLabel("메시지", { exact: true })).toHaveValue("다음 질문 유지");
});
