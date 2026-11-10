import { expect, test } from "@playwright/test";

test("회원가입부터 프로필·사진·재접속·로그아웃까지", async ({ page, context }, testInfo) => {
  const id = `browser_${testInfo.project.name}_${Date.now()}`;
  const errors: string[] = [];
  page.on("pageerror", (error) => errors.push(error.message));
  await page.goto("/user/signup");
  await page.getByLabel("표시 이름", { exact: true }).fill("브라우저 사용자");
  await page.getByLabel("사용자명", { exact: true }).fill(id);
  await page.getByLabel("이메일", { exact: true }).fill(`${id}@example.com`);
  await page.getByLabel("비밀번호", { exact: true }).fill("BrowserPass123!");
  await page.getByLabel("비밀번호 확인", { exact: true }).fill("BrowserPass123!");
  await page.getByRole("checkbox", { name: "입력한 정보로 LLM 계정을 생성하는 데 동의합니다." }).check();
  await page.getByRole("button", { name: "회원가입", exact: true }).click();
  await expect(page.getByRole("heading", { name: "계정 설정", exact: true })).toBeVisible();
  const cookies = await context.cookies();
  const cookie = cookies.find((c) => c.name === "llm_accounts_refresh");
  expect(cookie?.httpOnly).toBe(true);
  expect(cookie?.sameSite).toBe("Lax");
  expect(cookie?.path).toBe("/api/auth");
  expect(await page.evaluate(() => Object.values(localStorage).some((value) => /eyJ[a-zA-Z0-9_-]+\./.test(value)))).toBe(false);
  const profile = page.locator("section").filter({ has: page.getByRole("heading", { name: "프로필", exact: true }) });
  await profile.getByLabel("표시 이름", { exact: true }).fill("변경된 표시 이름");
  await profile.getByRole("button", { name: "프로필 저장" }).click();
  await expect(page.getByRole("status")).toContainText("프로필을 저장했습니다.");
  await expect(page.locator("header")).toContainText("변경된 표시 이름");
  const pixel = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aDV8AAAAASUVORK5CYII=", "base64");
  await page.locator('input[type="file"]').setInputFiles({ name: "avatar.png", mimeType: "image/png", buffer: pixel });
  await expect(page.getByRole("status")).toContainText("프로필 사진을 업로드했습니다.");
  await expect(page.locator('img[src^="/api/avatars/"]')).toBeVisible();
  await page.reload();
  await expect(page.getByRole("heading", { name: "계정 설정", exact: true })).toBeVisible();
  await expect(page.locator("header")).toContainText("변경된 표시 이름");
  await page.getByRole("button", { name: "계정 및 보안", exact: true }).click();
  await expect(page.getByRole("heading", { name: "로그인 세션", exact: true })).toBeVisible();
  await expect(page.getByText("현재 세션", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "다크 모드", exact: true }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "dark");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  await page.screenshot({ path: testInfo.outputPath("account-security.png"), fullPage: true });
  const second = await context.newPage();
  await second.goto("/user/profile");
  await expect(second.getByRole("heading", { name: "계정 설정", exact: true })).toBeVisible();
  await page.getByRole("button", { name: "로그아웃", exact: true }).click();
  await expect(page).toHaveURL(/\/login$/);
  await expect(second.getByRole("link", { name: "로그인", exact: true })).toBeVisible();
  await second.reload();
  await expect(second.getByRole("link", { name: "로그인", exact: true })).toBeVisible();
  await page.getByLabel("사용자명 또는 이메일", { exact: true }).fill(id);
  await page.getByLabel("비밀번호", { exact: true }).fill("BrowserPass123!");
  await page.getByRole("button", { name: "로그인", exact: true }).click();
  await expect(page.getByRole("heading", { name: "계정 설정", exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("BFF는 출처 검증과 계정 경로 제한을 적용한다", async ({ request }) => {
  expect((await request.post("/api/auth/login", { data: { loginId: "missing", password: "missing" } })).status()).toBe(403);
  expect((await request.post("/api/auth/login", { headers: { "x-llm-account": "1", origin: "https://other.example" }, data: {} })).status()).toBe(403);
  expect((await request.post("/api/auth/login", { headers: { "x-llm-account": "1" }, data: { loginId: 123, password: [] } })).status()).toBe(400);
  expect((await request.get("/api/account/notes")).status()).toBe(404);
  expect((await request.get("/api/account/users/me")).status()).toBe(401);
});

test("갱신 실패 시 클라이언트가 계정을 되살리지 않는다", async ({ page, context, baseURL }) => {
  // Request interception exercises the real UI's response handling without a fake success.
  await page.goto("/login");
  await context.addCookies([{ name: "llm_accounts_refresh", value: "temporary-test-refresh-cookie", domain: new URL(baseURL!).hostname, path: "/api/auth", httpOnly: true, sameSite: "Lax" }]);
  await page.route("**/api/auth/refresh", (route) => route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ detail: "인증 서버에 연결할 수 없습니다." }) }));
  await page.reload();
  expect((await context.cookies()).some((c) => c.name === "llm_accounts_refresh")).toBe(true);
});
