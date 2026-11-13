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
  await expect(page.getByRole("button", { name: "모델 선택 · 테스트 모드", exact: true })).toBeEnabled();
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
  await page.getByRole("button", { name: "모델 선택 · 테스트 모드", exact: true }).click();
  const models = page.getByRole("group", { name: "모델 목록", exact: true });
  await expect(models).toBeVisible();
  await models.getByRole("button", { name: "연결 해제", exact: true }).click();
  await expect(models).toHaveCount(0);
  await page.getByLabel("메시지", { exact: true }).fill("연결 없이 보내지 않기");
  await expect(page.getByRole("button", { name: "메시지 전송", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "모델 선택", exact: true }).click();
  await models.getByRole("button", { name: "테스트 모드", exact: true }).click();
  await expect(models).toHaveCount(0);
  await expect(page.getByRole("button", { name: "메시지 전송", exact: true })).toBeEnabled();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("chat-workspace.png"), fullPage: true });
  expect(failures).toEqual([]);
});

test("입력창 오른쪽의 드롭다운에서 등록 모델과 조회한 모델을 선택한다", async ({ page }, info) => {
  const profile = { name: "gemma-4-31b-it", context_window: 8192, max_output_tokens: 1024, max_concurrent: 2 };
  await page.route("**/api/chat/config", async route => {
    const response = await route.fetch();
    const config = await response.json();
    await route.fulfill({ json: { ...config, connections: [
      ...config.connections,
      { id: "office", name: "사내 vLLM", provider: "vllm", default_model: profile.name, is_test: false,
        models: [profile, { ...profile, name: "gpt-oss-120b" }] },
      { id: "ollama", name: "Ollama", provider: "ollama", default_model: "", is_test: false, models: [] },
    ] } });
  });
  await page.route("**/api/chat/connections/ollama/check", route => route.fulfill({ json: { models: ["gemma4:31b"] } }));
  await signUp(page, `picker_${info.project.name}_${Date.now()}`);
  const editor = page.getByLabel("메시지", { exact: true });
  await editor.fill("모델을 바꾸는 동안 유지할 질문");
  const trigger = page.getByRole("button", { name: /^모델 선택 · / });
  const add = page.getByRole("button", { name: "도구 추가", exact: true });
  const send = page.getByRole("button", { name: "메시지 전송", exact: true });
  const [toolBox, modelBox, sendBox] = await Promise.all([add.boundingBox(), trigger.boundingBox(), send.boundingBox()]);
  expect(toolBox!.x).toBeLessThan(modelBox!.x);
  expect(sendBox!.x - modelBox!.x - modelBox!.width).toBeLessThanOrEqual(8);
  expect(Math.abs(modelBox!.y + modelBox!.height / 2 - sendBox!.y - sendBox!.height / 2)).toBeLessThanOrEqual(1);

  await add.click();
  await expect(page.getByRole("group", { name: "채팅 도구" })).toBeVisible();
  await trigger.click();
  const dropdown = page.getByRole("group", { name: "모델 목록" });
  await expect(dropdown).toBeVisible();
  await expect(page.getByRole("group", { name: "채팅 도구" })).toHaveCount(0);
  await expect(page.locator("dialog:modal")).toHaveCount(0);
  const box = (await dropdown.boundingBox())!;
  const viewport = page.viewportSize()!;
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
  expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
  await dropdown.getByRole("button", { name: "gpt-oss-120b", exact: true }).click();
  await expect(trigger).toHaveAccessibleName("모델 선택 · gpt-oss-120b");
  await expect(trigger).toBeFocused();

  await trigger.click();
  const ollama = dropdown.getByRole("region", { name: "Ollama" });
  await ollama.getByRole("button", { name: "연결 확인", exact: true }).click();
  await expect(ollama.getByRole("status")).toHaveText("모델 1개 확인됨");
  await ollama.getByRole("button", { name: "gemma4:31b", exact: true }).click();
  await expect(trigger).toHaveAccessibleName("모델 선택 · gemma4:31b");
  await trigger.click();
  await ollama.getByRole("button", { name: "모델 ID 직접 입력" }).click();
  const custom = ollama.getByRole("textbox", { name: "모델 ID" });
  await custom.fill("gemma4:custom");
  await custom.press("Enter");
  await expect(trigger).toHaveAccessibleName("모델 선택 · gemma4:custom");
  await expect(dropdown).toHaveCount(0);
  await expect(editor).toHaveValue("모델을 바꾸는 동안 유지할 질문");
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

test("프롬프트로 캔버스를 열어 Markdown 원문을 편집·복사·저장한다", async ({ page, context }, info) => {
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await signUp(page, `canvas_${info.project.name}_${Date.now()}`);
  await expect(page.getByRole("button", { name: "다음 답변 캔버스로 받기" })).toHaveCount(0);
  await page.evaluate(() => {
    const observer = new MutationObserver(() => {
      if (document.querySelector(".cw-canvas [role=status]")?.textContent === "답변 작성 중…") {
        document.body.dataset.canvasStreaming = "true";
        observer.disconnect();
      }
    });
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  });
  await page.getByLabel("메시지", { exact: true }).fill("이 초안을 캔버스로 작성해 줘");
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
});

test("캔버스 요청을 채팅에서 읽어 자동으로 열고 카드를 복원한다", async ({ page }, info) => {
  await signUp(page, `canvas_auto_${info.project.name}_${Date.now()}`);
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

test("채팅 답변에서 웹·이전 대화·표 계산 출처를 확인한다", async ({ page }, info) => {
  await signUp(page, `combined_sources_${info.project.name}_${Date.now()}`);
  const earlier = "전에 결정한 정산 기준을 남겨 줘";
  await sendQuestion(page, earlier);
  const earlierUrl = new URL(page.url());
  const conversationId = earlierUrl.pathname.split("/").at(-1)!;
  const messageId = (await page.getByRole("article", { name: "내 메시지" }).getAttribute("id"))!.slice("message-".length);
  const chart = { id: "697fc506-4d35-4205-bf92-c7603e773197", name: "정산 차트.html", format: "html", size: 120,
    sha256: "e6bced00c5f0b60eb349f9fd9f1edb6eb08b2ac5573283bd6a5b1383dbbf1fd2", created_at: 1_800_000_000 };
  await page.route(`**/api/workflows/artifacts/${chart.id}`, route => route.fulfill({ json: {
    ...chart, kind: "table_analysis", columns: [], rows: [], content: "", preview_truncated: false,
    html_preview: "<!doctype html><html lang='ko'><body><h1>월별 정산 차트</h1></body></html>",
  } }));

  await page.route(/\/api\/chat\/conversations\/[^/?]+\/messages$/, async route => {
    const upstream = await route.fetch();
    const sources = [
      { id: "WS1", app: "web", title: "공식 정산 안내", url: "https://example.com/guide", excerpt: "기준 날짜를 확인합니다." },
      { id: "CH1", app: "chat_history", title: earlier, conversation_id: conversationId, message_id: messageId },
      { id: "TA1", app: "table_analysis", title: "정산 자료 계산", excerpt: "1월 합계 125", documents: [
        { document_id: "fixture", filename: "정산.xlsx", sha256: "fixture", revision: 1, sheet: "월별" },
      ] },
    ];
    const body = (await upstream.text()).replace(/event: done\ndata: ([^\n]+)\n\n/, (_event, json: string) => {
      const final = JSON.parse(json);
      final.message.sources = sources;
      final.message.artifacts = [chart];
      return `event: sources\ndata: ${JSON.stringify({ sources })}\n\nevent: artifact\ndata: ${JSON.stringify(chart)}\n\nevent: done\ndata: ${JSON.stringify(final)}\n\n`;
    });
    await route.fulfill({ response: upstream, body });
  });
  const sidebar = page.getByRole("button", { name: "사이드바 열기" });
  if (await sidebar.isVisible()) await sidebar.click();
  await page.getByRole("button", { name: "새 대화 ↗", exact: true }).click();
  await sendQuestion(page, "이전 정산 기준과 자료를 확인해 줘");

  const sources = page.getByRole("complementary", { name: "답변 출처" });
  const web = sources.getByRole("link", { name: /WS1.*공식 정산 안내/ });
  await expect(web).toHaveAttribute("href", "https://example.com/guide");
  await expect(web).toHaveAttribute("rel", "noopener noreferrer");
  await sources.getByRole("button", { name: /TA1.*정산 자료 계산/ }).click();
  const evidence = page.getByRole("dialog", { name: "첨부 데이터 분석 근거" });
  await expect(evidence).toContainText("정산.xlsx · 월별 · 버전 1");
  await expect(evidence).toContainText("1월 합계 125");
  await evidence.getByRole("button", { name: "닫기", exact: true }).click();
  await page.getByRole("button", { name: /정산 차트\.html.*미리보기/ }).click();
  const preview = page.getByRole("dialog", { name: "결과 파일 미리보기" });
  await expect(preview.frameLocator("iframe").getByRole("heading", { name: "월별 정산 차트" })).toBeVisible();
  await expect(preview.getByText(/슬라이드의 스크립트/)).toHaveCount(0);
  await preview.getByRole("button", { name: "닫기", exact: true }).click();

  const around = page.waitForRequest(request => {
    const url = new URL(request.url());
    return url.pathname === `/api/chat/conversations/${conversationId}` && url.searchParams.get("around_message_id") === messageId;
  });
  await sources.getByRole("link", { name: /CH1.*전에 결정한 정산 기준/ }).click();
  await around;
  await expect(page).toHaveURL(earlierUrl.href);
  await expect(messageText(page)).toHaveText(earlier);
  await expect(page.getByRole("article", { name: "내 메시지" })).toBeFocused();
});

test("예약 작업을 열고 실행 대화를 확인하거나 다음 실행을 중지한다", async ({ page }, info) => {
  await signUp(page, `schedules_${info.project.name}_${Date.now()}`);
  const runQuestion = "정기 업무의 마지막 실행";
  await sendQuestion(page, runQuestion);
  const runUrl = page.url();
  const conversationId = new URL(runUrl).pathname.split("/").at(-1)!;
  const scheduleId = "95d1e9ac-321f-4637-88d1-0cc4abdf3b28";
  const lastRun = { id: "c314a12a-cc40-4861-a476-104c950b5a2d", status: "complete", conversation_id: conversationId,
    scheduled_at: 1_800_000_000, error: null };
  const schedule = { id: scheduleId, title: "주간 정산 확인", repeat: "weekly", time_zone: "Asia/Seoul",
    status: "active", next_run_at: 1_800_000_000 + 7 * 86_400, last_run: lastRun };
  await page.route("**/api/workflows/schedules", route => route.fulfill({ json: { items: [schedule] } }));
  await page.route(`**/api/workflows/schedules/${scheduleId}/runs`, route => route.fulfill({ json: { items: [lastRun] } }));
  await page.route(`**/api/workflows/schedules/${scheduleId}`, route => {
    expect(route.request().method()).toBe("DELETE");
    schedule.status = "cancelled";
    return route.fulfill({ json: { schedule: { ...schedule, next_run_at: null } } });
  });

  const sidebar = page.getByRole("button", { name: "사이드바 열기" });
  if (await sidebar.isVisible()) await sidebar.click();
  await page.getByRole("button", { name: "새 대화 ↗", exact: true }).click();
  const editor = page.getByLabel("메시지", { exact: true });
  await editor.fill("작성 중인 질문");
  const openSchedules = async () => {
    await page.getByRole("button", { name: "도구 추가", exact: true }).click();
    await page.getByRole("group", { name: "채팅 도구" }).getByRole("button", { name: "예약 작업", exact: true }).click();
  };
  await openSchedules();
  const dialog = page.getByRole("dialog", { name: "예약 작업", exact: true });
  await expect(dialog.getByRole("heading", { name: schedule.title })).toBeVisible();
  await expect(dialog).toContainText("Asia/Seoul · 다음 실행");
  const box = (await dialog.boundingBox())!;
  const viewport = page.viewportSize()!;
  expect(Math.abs(box.x + box.width / 2 - viewport.width / 2)).toBeLessThanOrEqual(1);
  expect(Math.abs(box.y + box.height / 2 - viewport.height / 2)).toBeLessThanOrEqual(1);

  await page.mouse.click(3, 3);
  await expect(dialog).toHaveCount(0);
  await expect(editor).toHaveValue("작성 중인 질문");
  await openSchedules();
  await dialog.getByRole("button", { name: "실행 기록", exact: true }).click();
  await expect(dialog.getByRole("link", { name: "결과 대화 열기", exact: true })).toHaveAttribute("href", new URL(runUrl).pathname);
  await dialog.getByRole("button", { name: "예약 중지", exact: true }).click();
  await dialog.getByRole("button", { name: "중지 확인", exact: true }).click();
  await expect(dialog.getByText("예약 중지", { exact: true })).toBeVisible();
  await dialog.getByRole("link", { name: "결과 대화 열기", exact: true }).click();
  await expect(page).toHaveURL(runUrl);
  await expect(dialog).toHaveCount(0);
  await expect(messageText(page)).toHaveText(runQuestion);
});

test("채팅 요청으로 만든 예약을 목록에서 확인하고 채팅으로 취소한다", async ({ page }, info) => {
  await signUp(page, `chat_schedule_${info.project.name}_${Date.now()}`);
  const question = "매주 월요일 오전 9시에 Asia/Seoul 기준으로 사내 규정 변경을 확인해 줘";
  await sendQuestion(page, question);
  const answers = page.getByRole("article", { name: "모델 답변" });
  await expect(answers).toContainText("예약했습니다.");
  const id = (await answers.locator("code").textContent())!;
  expect(id).toMatch(/^[a-f0-9-]{36}$/);

  await page.getByRole("button", { name: "도구 추가", exact: true }).click();
  await page.getByRole("group", { name: "채팅 도구" }).getByRole("button", { name: "예약 작업", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "예약 작업", exact: true });
  await expect(dialog.getByRole("heading", { name: question })).toBeVisible();
  await expect(dialog).toContainText("매주 · Asia/Seoul · 다음 실행");
  await dialog.getByRole("button", { name: "닫기", exact: true }).click();

  await page.getByLabel("메시지", { exact: true }).fill(`예약 작업 ${id} 취소해 줘`);
  await page.getByRole("button", { name: "메시지 전송", exact: true }).click();
  await expect(answers.last()).toContainText("예약을 취소했습니다.");
  await page.getByRole("button", { name: "도구 추가", exact: true }).click();
  await page.getByRole("group", { name: "채팅 도구" }).getByRole("button", { name: "예약 작업", exact: true }).click();
  await expect(dialog.getByText("예약 중지", { exact: true })).toBeVisible();
});

test("GitLab을 멘션한 메시지에 원본 이미지와 문서를 함께 전송한다", async ({ page }, info) => {
  const profile = { name: "image-and-tools", context_window: 8192, max_output_tokens: 1024, max_concurrent: 2,
    supports_vision: true, supports_tools: true };
  await page.route("**/api/chat/config", async route => {
    const response = await route.fetch();
    const config = await response.json();
    await route.fulfill({ json: { ...config, connections: [...config.connections,
      { id: "vision", name: "이미지 모델", provider: "vllm", default_model: profile.name, is_test: false, models: [profile] },
    ] } });
  });
  await page.route("**/api/chat/conversations", async route => {
    if (route.request().method() !== "POST") return route.continue();
    const body = { ...route.request().postDataJSON(), connection_id: "demo", model: "demo" };
    const response = await route.fetch({ postData: body });
    await route.fulfill({ response });
  });
  await page.route(/\/api\/chat\/conversations\/[^/?]+\/messages$/, route => route.fulfill({
    status: 422, json: { detail: "이 브라우저 시나리오에서는 전송된 첨부까지만 확인합니다." },
  }));
  await signUp(page, `combined_input_${info.project.name}_${Date.now()}`);
  await page.getByRole("button", { name: "모델 선택 · 테스트 모드", exact: true }).click();
  await page.getByRole("group", { name: "모델 목록" }).getByRole("button", { name: profile.name, exact: true }).click();
  const editor = page.getByLabel("메시지", { exact: true });
  await editor.fill("@gitlab team/ui 첨부한 화면과 문서를 비교해 줘");
  const pixels = await page.evaluate(() => {
    const image = document.createElement("canvas");
    image.width = image.height = 16;
    return image.toDataURL().split(",")[1];
  });
  await page.locator('input[type="file"]').setInputFiles([
    { name: "screen.png", mimeType: "image/png", buffer: Buffer.from(pixels, "base64") },
    { name: "notes.md", mimeType: "text/markdown", buffer: Buffer.from("첨부 문서") },
  ]);
  await expect(page.getByRole("list", { name: "첨부 미리보기" })).toContainText("screen.png");
  const turn = page.waitForRequest(/\/api\/chat\/conversations\/[^/?]+\/messages$/, { timeout: 30_000 });
  const send = page.getByRole("button", { name: "메시지 전송", exact: true });
  await expect(send).toBeEnabled();
  await send.click();
  const payload = (await turn).postDataJSON();
  expect(payload.content).toContain("@gitlab team/ui");
  expect(payload.assistant.image_document_ids).toHaveLength(1);
  expect(payload.assistant.document_ids).toHaveLength(1);
  expect(payload.assistant.image_document_ids[0]).not.toBe(payload.assistant.document_ids[0]);
});
