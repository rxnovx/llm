import { expect, test, type Page } from "@playwright/test";

async function signup(page: Page, name: string) {
  await page.goto("/user/signup");
  await page.getByLabel("표시 이름", { exact: true }).fill("대화 정리 사용자");
  await page.getByLabel("사용자명", { exact: true }).fill(name);
  await page.getByLabel("이메일", { exact: true }).fill(`${name}@example.com`);
  await page.getByLabel("비밀번호", { exact: true }).fill("LibraryTestPass123!");
  await page.getByLabel("비밀번호 확인", { exact: true }).fill("LibraryTestPass123!");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "회원가입", exact: true }).click();
  await expect(page.getByRole("heading", { name: "무엇을 함께 해볼까요?" })).toBeVisible();
}
async function send(page: Page, question: string) {
  await page.getByLabel("메시지", { exact: true }).fill(question);
  await page.getByRole("button", { name: "메시지 전송", exact: true }).click();
  await expect(page.getByRole("article", { name: "모델 답변" })).toBeVisible();
  await expect(page.getByRole("button", { name: "응답 중지", exact: true })).toHaveCount(0);
}
async function sidebar(page: Page) {
  const button = page.getByRole("button", { name: "사이드바 열기", exact: true });
  if (await button.isVisible()) await button.click();
}

test("대화를 고정·보관·검색·복원하고 모달에서 작성 중인 질문을 보존한다", async ({ page }, info) => {
  await signup(page, `lib_${info.project.name}_${Date.now()}`);
  const first = "먼저 만든 대화의 정리 검증";
  await send(page, first);
  await sidebar(page);
  await page.getByRole("button", { name: "새 대화", exact: true }).click();
  await send(page, "두 번째 대화의 정리 검증");
  await page.getByLabel("메시지", { exact: true }).fill("아직 보내지 않은 초안");
  await sidebar(page);
  const row = page.locator(".cw-history-row").filter({ hasText: first });
  await row.getByRole("button", { name: `${first} 메뉴`, exact: true }).click();
  await row.getByRole("button", { name: "대화 고정", exact: true }).click();
  await expect(page.locator(".cw-history-row").first()).toContainText(first);
  await row.getByRole("button", { name: `${first} 메뉴`, exact: true }).click();
  await row.getByRole("button", { name: "보관", exact: true }).click();
  await expect(row).toHaveCount(0);
  await page.getByRole("button", { name: "대화 찾기" }).click();
  await page.getByLabel("대화 제목·본문 검색").fill(first);
  await expect(row).toContainText("보관된 대화");
  await page.getByRole("button", { name: "보관된 대화 열기", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "보관된 대화", exact: true });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "보관 해제", exact: true }).click();
  await expect(dialog.getByRole("listitem")).toHaveCount(0);
  await dialog.getByRole("button", { name: "닫기", exact: true }).click();
  await expect(page.getByLabel("메시지", { exact: true })).toHaveValue("아직 보내지 않은 초안");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: info.outputPath("conversation-library.png"), fullPage: true });
});

test("완료된 작업을 다시 열고 작업 센터를 중앙에서 닫아도 초안이 유지된다", async ({ page }, info) => {
  await signup(page, `job_${info.project.name}_${Date.now()}`);
  const question = "완료된 작업 기록을 확인하는 질문";
  await send(page, question);
  await page.getByLabel("메시지", { exact: true }).fill("다음 질문 초안");
  await sidebar(page);
  await page.getByRole("button", { name: "작업 센터", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "작업 센터", exact: true });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "완료·중단 기록", exact: true }).click();
  await expect(dialog.getByRole("listitem")).toContainText(question);
  const box = await dialog.boundingBox(), viewport = page.viewportSize();
  expect(box && viewport).toBeTruthy();
  if (box && viewport) {
    expect(Math.abs(box.x + box.width / 2 - viewport.width / 2)).toBeLessThan(4);
    expect(Math.abs(box.y + box.height / 2 - viewport.height / 2)).toBeLessThan(4);
  }
  await page.mouse.click(2, 2);
  await expect(dialog).toHaveCount(0);
  await expect(page.getByLabel("메시지", { exact: true })).toHaveValue("다음 질문 초안");
});
