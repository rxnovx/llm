import { expect, test, type Page } from "@playwright/test";

async function accountMenu(page: Page) {
  if (await page.getByRole("button", { name: "사이드바 열기", exact: true }).isVisible()) await page.getByRole("button", { name: "사이드바 열기", exact: true }).click();
  await page.getByRole("button", { name: "계정 메뉴", exact: true }).click();
}

test("채팅에서 로그인·가입·설정 모달을 열고 닫아도 작성 내용과 대화가 유지된다", async ({ page }, info) => {
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "무엇을 함께 해볼까요?" })).toBeVisible();
  const draft = "가입 전 작성한 질문을 이어서 보내기";
  await page.getByLabel("메시지", { exact: true }).fill(draft);
  await page.getByRole("button", { name: "메시지 전송", exact: true }).click();
  const login = page.getByRole("dialog", { name: "로그인", exact: true });
  await expect(login).toBeVisible();
  await expect(page.locator(".workspace-background")).toHaveJSProperty("inert", true);
  await expect(page.locator("#chat-message")).toHaveValue(draft);
  await login.getByRole("link", { name: "회원가입", exact: true }).click();
  const signup = page.getByRole("dialog", { name: "계정 만들기", exact: true });
  await expect(signup).toBeVisible();
  const username = `modal_${info.project.name}_${Date.now()}`;
  await signup.getByLabel("표시 이름", { exact: true }).fill("모달 사용자");
  await signup.getByLabel("사용자명", { exact: true }).fill(username);
  await signup.getByLabel("이메일", { exact: true }).fill(`${username}@example.com`);
  await signup.getByLabel("비밀번호", { exact: true }).fill("ModalBrowserPass123!");
  await signup.getByLabel("비밀번호 확인", { exact: true }).fill("ModalBrowserPass123!");
  await signup.getByRole("checkbox").check();
  await signup.getByRole("button", { name: "회원가입", exact: true }).click();
  await expect(signup).not.toBeVisible();
  await expect(page.getByLabel("메시지", { exact: true })).toHaveValue(draft);
  await page.getByRole("button", { name: "메시지 전송", exact: true }).click();
  await expect(page.getByRole("button", { name: "답변 복사", exact: true })).toBeVisible();
  const conversationUrl = page.url();
  const unsent = "설정을 열어도 사라지면 안 되는 다음 질문";
  await page.getByLabel("메시지", { exact: true }).fill(unsent);
  const composer = await page.locator("#chat-message").elementHandle();
  await accountMenu(page);
  await page.getByRole("link", { name: "설정", exact: true }).click();
  const settings = page.getByRole("dialog", { name: "설정", exact: true });
  await expect(settings).toBeVisible();
  for (let index=0;index<14;index++) {
    await page.keyboard.press("Tab");
    expect(await page.evaluate(() => Boolean(document.activeElement?.closest('dialog[data-workspace-modal="settings"]')))).toBe(true);
  }
  expect(await composer!.evaluate((element) => element.isConnected && element === document.getElementById("chat-message"))).toBe(true);
  await expect(page.locator("#chat-message")).toHaveValue(unsent);
  await settings.getByRole("button", { name: "프로필", exact: true }).click();
  await expect(settings.getByLabel("표시 이름", { exact: true })).toHaveValue("모달 사용자");
  await settings.getByRole("button", { name: "설정 닫기", exact: true }).click();
  await expect(page).toHaveURL(conversationUrl);
  await expect(page.locator(".workspace-background")).toHaveJSProperty("inert", false);
  await expect(page.getByLabel("메시지", { exact: true })).toHaveValue(unsent);
  await expect(page.getByRole("article", { name: "내 메시지" })).toHaveText(draft);
  await accountMenu(page);
  await page.getByRole("link", { name: "설정", exact: true }).click();
  await expect(settings).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(settings).not.toBeVisible();
  await expect(page).toHaveURL(conversationUrl);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await accountMenu(page);
  await page.getByRole("button", { name: "로그아웃", exact: true }).click();
  await expect(page.getByRole("dialog", { name: "로그인", exact: true })).toBeVisible();
  await expect(page.locator("#chat-message")).toHaveValue("");
  await expect(page.locator(".workspace-background .cw-message")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("설정·운영·인증 주소로 직접 진입하면 채팅 위에 모달을 표시한다", async ({ page }) => {
  for (const [path, title] of [["/user/settings", "설정"], ["/user/profile", "설정"], ["/admin", "운영 대시보드"], ["/user/reset-password", "비밀번호 재설정"], ["/user/verify-email", "이메일 인증"], ["/user/recover", "계정 복구"]]) {
    await page.goto(path);
    const modal = page.getByRole("dialog", { name: title, exact: true });
    await expect(modal).toBeVisible();
    await expect(page.locator(".workspace-background")).toHaveJSProperty("inert", true);
    await modal.getByRole("button", { name: `${title} 닫기`, exact: true }).click();
    await expect(page).toHaveURL(/\/$/);
    await expect(page.getByRole("heading", { name: "무엇을 함께 해볼까요?" })).toBeVisible();
  }
});
