import { expect, test, type Page } from "@playwright/test";

const password = "ChatBrowserPass123!";
const conversationRoute = /\/c\/[a-f0-9-]{36}$/;
const messageText = (page: Page) => page.getByRole("article", { name: "내 메시지" }).locator(".cw-human-text");

async function signUp(page: Page, username: string) {
  await page.goto("/user/signup");
  await page.getByLabel("표시 이름", { exact: true }).fill("대화 사용자");
  await page.getByLabel("사용자명", { exact: true }).fill(username);
  await page.getByLabel("이메일", { exact: true }).fill(`${username}@example.com`);
  await page.getByLabel("비밀번호", { exact: true }).fill(password);
  await page.getByLabel("비밀번호 확인", { exact: true }).fill(password);
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "회원가입", exact: true }).click();
  await expect(page.getByRole("heading", { name: "무엇을 함께 해볼까요?" })).toBeVisible();
  await expect(page.getByRole("button", { name: /테스트 모드/ })).toBeVisible();
}

async function sendQuestion(page: Page, question: string) {
  await page.getByLabel("메시지", { exact: true }).fill(question);
  await page.getByRole("button", { name: "메시지 전송", exact: true }).click();
  await expect(messageText(page)).toHaveText(question);
  await expect(page.getByRole("button", { name: "답변 복사", exact: true })).toBeVisible();
  await expect(page.getByText("예시 답변", { exact: true })).toBeVisible();
  await expect(page).toHaveURL(conversationRoute);
}

test("테스트 대화·이전 링크·새로고침·이름 변경·연결 해제", async ({ page }, info) => {
  const failures: string[] = [];
  page.on("pageerror", (error) => failures.push(error.message));
  await signUp(page, `chat_${info.project.name}_${Date.now()}`);
  const question = `다음 작업을 정리해 주세요 ${Date.now()}`;
  await sendQuestion(page, question);
  const conversationUrl = page.url();
  const id = new URL(conversationUrl).pathname.split("/").at(-1);
  await page.goto(`/?chat=${id}`);
  await expect(page).toHaveURL(conversationUrl);
  await expect(messageText(page)).toHaveText(question);
  await page.reload();
  await expect(messageText(page)).toHaveText(question);
  await expect(page.getByRole("button", { name: "답변 복사", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "대화 이름 변경", exact: true }).click();
  await page.getByLabel("대화 이름", { exact: true }).fill("다시 이어갈 대화");
  await page.getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.getByRole("button", { name: "대화 이름 변경", exact: true })).toHaveText("다시 이어갈 대화");
  await page.reload();
  await expect(page.getByRole("button", { name: "대화 이름 변경", exact: true })).toHaveText("다시 이어갈 대화");
  if (await page.getByRole("button", { name: "사이드바 열기" }).isVisible()) await page.getByRole("button", { name: "사이드바 열기" }).click();
  await expect(page.getByRole("button", { name: "다시 이어갈 대화", exact: true })).toBeVisible();
  if (await page.getByRole("button", { name: "사이드바 접기" }).isVisible()) await page.getByRole("button", { name: "사이드바 접기" }).click();
  await page.getByRole("button", { name: /테스트 모드/ }).click();
  const settings = page.getByRole("dialog", { name: "모델 연결", exact: true });
  await expect(settings).toBeVisible();
  await settings.getByRole("button", { name: "연결 해제", exact: true }).click();
  await settings.getByRole("button", { name: "연결 설정 닫기" }).click();
  await page.getByLabel("메시지", { exact: true }).fill("연결 없이 보내지 않기");
  await expect(page.getByRole("button", { name: "메시지 전송", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: /연결 선택/ }).click();
  await settings.getByRole("button", { name: "선택", exact: true }).click();
  await settings.getByRole("button", { name: "연결 설정 닫기" }).click();
  await expect(page.getByRole("button", { name: "메시지 전송", exact: true })).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("chat-workspace.png"), fullPage: true });
  expect(failures).toEqual([]);
});

test("대화 주소를 뒤로·앞으로 이동하고 로그인 후 복귀할 수 있다", async ({ page, browser, baseURL }, info) => {
  const username = `route_${info.project.name}_${Date.now()}`;
  await signUp(page, username);
  const first = "첫 번째 대화 주소 검증";
  const second = "두 번째 대화 주소 검증";
  await sendQuestion(page, first);
  const firstUrl = page.url();
  const sidebar = page.getByRole("button", { name: "사이드바 열기", exact: true });
  if (await sidebar.isVisible()) await sidebar.click();
  await page.getByRole("button", { name: /새 대화/ }).first().click();
  await expect(page).toHaveURL(/\/$/);
  await sendQuestion(page, second);
  const secondUrl = page.url();
  expect(secondUrl).not.toBe(firstUrl);

  await page.goBack();
  await expect(page).toHaveURL(firstUrl);
  await expect(messageText(page)).toHaveText(first);
  await page.goForward();
  await expect(page).toHaveURL(secondUrl);
  await expect(messageText(page)).toHaveText(second);

  const draft = "설정 모달을 닫고 이어서 작성할 질문";
  const composer = page.getByLabel("메시지", { exact: true });
  await composer.fill(draft);
  if (await sidebar.isVisible()) await sidebar.click();
  await page.getByRole("button", { name: "계정 메뉴", exact: true }).click();
  await page.getByRole("link", { name: "설정", exact: true }).click();
  const settings = page.getByRole("dialog", { name: "설정", exact: true });
  await expect(settings).toBeVisible();
  expect(new URL(page.url()).searchParams.get("returnTo")).toBe(new URL(secondUrl).pathname);
  await settings.getByRole("button", { name: "설정 닫기", exact: true }).click();
  await expect(page).toHaveURL(secondUrl);
  await expect(composer).toHaveValue(draft);
  await expect(messageText(page)).toHaveText(second);

  if (await sidebar.isVisible()) await sidebar.click();
  await page.getByRole("button", { name: "계정 메뉴", exact: true }).click();
  await page.getByRole("button", { name: "로그아웃", exact: true }).click();
  await expect(page).toHaveURL("/login");

  const visitor = await browser.newContext({ baseURL });
  try {
    const bookmark = await visitor.newPage();
    const path = new URL(firstUrl).pathname;
    await bookmark.goto(firstUrl);
    await expect(bookmark).toHaveURL(`/login?${new URLSearchParams({ next: path })}`);
    const login = bookmark.getByRole("dialog", { name: "로그인", exact: true });
    await login.getByLabel("사용자명 또는 이메일", { exact: true }).fill(username);
    await login.getByLabel("비밀번호", { exact: true }).fill(password);
    await login.getByRole("button", { name: "로그인", exact: true }).click();
    await expect(bookmark).toHaveURL(firstUrl);
    await expect(messageText(bookmark)).toHaveText(first);

    await visitor.clearCookies();
    await signUp(bookmark, `other_${info.project.name}_${Date.now()}`);
    const denied = bookmark.waitForResponse(response =>
      new URL(response.url()).pathname === `/api/chat/conversations/${path.split("/").at(-1)}`);
    await bookmark.goto(firstUrl);
    expect((await denied).status()).toBe(404);
    await expect(bookmark.locator("main").getByRole("alert")).toBeVisible();
    await expect(bookmark.getByRole("article", { name: "내 메시지" })).toHaveCount(0);
  } finally {
    await visitor.close();
  }
});

test("다음 답변이 캔버스에 표시되고 Markdown 원문을 편집·복사·저장할 수 있다", async ({ page, context }, info) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await signUp(page, `canvas_${info.project.name}_${Date.now()}`);
  const toggle = page.getByRole("button", { name: "다음 답변 캔버스로 받기" });
  await toggle.click();
  await expect(toggle).toHaveAttribute("aria-pressed", "true");
  await page.evaluate(() => {
    const observer = new MutationObserver(() => {
      if (document.querySelector(".cw-canvas [role=status]")?.textContent === "답변 작성 중…") {
        document.body.dataset.canvasStreaming = "true";
        observer.disconnect();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  });
  await page.getByLabel("메시지", { exact: true }).fill("캔버스에 표시할 답변");
  await page.getByRole("button", { name: "메시지 전송", exact: true }).click();

  const canvas = page.getByRole("complementary", { name: "캔버스", exact: true });
  const editor = canvas.getByRole("textbox", { name: "캔버스 Markdown" });
  await expect(canvas).toBeVisible();
  await expect(editor).toBeEditable();
  await expect(page.locator("body")).toHaveAttribute("data-canvas-streaming", "true");
  const original = await editor.inputValue();
  expect(original.trim().length).toBeGreaterThan(0);
  const viewport = page.viewportSize()!;
  const box = (await canvas.boundingBox())!;
  if (viewport.width >= 1120) {
    const chat = (await page.getByRole("main").boundingBox())!;
    expect(box.x).toBeGreaterThanOrEqual(chat.x + chat.width - 1);
  } else {
    expect(box.width).toBe(viewport.width);
    await expect(page.getByRole("main")).toBeHidden();
  }

  const markdown = '# 캔버스 제목\n\n**원문**  \n\n```python\nprint("안녕")\n```\n';
  await editor.fill(markdown);
  const sourceField = await editor.elementHandle();
  await canvas.getByRole("button", { name: "미리보기", exact: true }).click();
  await expect(canvas.getByRole("heading", { name: "캔버스 제목" })).toBeVisible();
  await canvas.getByRole("button", { name: "Markdown 복사", exact: true }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(markdown);
  await canvas.getByRole("button", { name: "Markdown", exact: true }).click();
  expect(await sourceField!.evaluate(element => element.isConnected && !element.hasAttribute("hidden"))).toBe(true);

  const open = page.getByRole("button", { name: "캔버스에서 열기", exact: true });
  await canvas.getByRole("button", { name: "캔버스 닫기" }).click();
  await expect(open).toBeVisible();
  await open.click();
  await expect(editor).toHaveValue(markdown);
  await canvas.getByRole("button", { name: "저장", exact: true }).click();
  await expect(canvas.getByRole("status")).toHaveText("저장됨");
  await page.reload();
  await open.click();
  await expect(editor).toHaveValue(markdown);

  await canvas.getByRole("button", { name: "캔버스 닫기" }).click();
  await page.evaluate(() => {
    Object.defineProperty(navigator.clipboard, "writeText", {
      value: () => Promise.reject(new DOMException("Unavailable", "NotAllowedError")),
    });
  });
  await page.getByRole("button", { name: "답변 복사", exact: true }).click();
  await expect.poll(() => page.evaluate(() => navigator.clipboard.readText())).toBe(original);
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
});

test("캔버스 요청을 채팅에서 읽어 자동으로 열고 카드를 복원한다", async ({ page }, info) => {
  await signUp(page, `canvas_auto_${info.project.name}_${Date.now()}`);
  const toggle = page.getByRole("button", { name: "다음 답변 캔버스로 받기" });
  await expect(toggle).toHaveAttribute("aria-pressed", "false");
  const editor = page.getByLabel("메시지", { exact: true });
  await editor.fill("초안을 캔버스로 작성해 줘");
  await page.getByRole("button", { name: "메시지 전송", exact: true }).click();

  const canvas = page.getByRole("complementary", { name: "캔버스", exact: true });
  await expect(canvas).toBeVisible();
  const markdown = canvas.getByRole("textbox", { name: "캔버스 Markdown" });
  await expect(markdown).toBeEditable();
  const original = await markdown.inputValue();
  const card = page.locator(".cw-canvas-card");
  await canvas.getByRole("button", { name: "캔버스 닫기" }).click();
  await expect(card).toHaveAttribute("aria-expanded", "false");
  await page.reload();
  await card.click();
  await expect(markdown).toHaveValue(original);
  await canvas.getByRole("button", { name: "캔버스 닫기" }).click();

  await editor.fill("간단한 인사를 해 줘");
  await page.getByRole("button", { name: "메시지 전송", exact: true }).click();
  await expect(page.getByRole("button", { name: "답변 복사", exact: true })).toHaveCount(2);
  await expect(card).toHaveCount(1);
  await expect(canvas).toHaveCount(0);
});
