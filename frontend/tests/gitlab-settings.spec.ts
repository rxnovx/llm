import { expect, test } from "@playwright/test";

test("같은 서버의 여러 계정과 선택형 웹훅 상태·알림을 관리한다", async ({ page }, info) => {
  const connection = "77777777-7777-4777-8777-777777777777";
  const otherConnection = "88888888-8888-4888-8888-888888888888";
  const subscriptionId = "99999999-9999-4999-8999-999999999999";
  const created = Math.floor(Date.now() / 1000);
  let subscribed = false, notifications = false, seen = false, hasEvent = false;
  const subscription = () => ({ id: subscriptionId, connection_id: connection, project_id: 7, project: "team/service",
    notifications_enabled: notifications, callback_path: `/api/apps/gitlab/webhooks/${subscriptionId}`, created_at: created });
  await page.route("**/api/apps/**", async route => {
    const path = new URL(route.request().url()).pathname, method = route.request().method();
    if (path.endsWith("/catalog")) return route.fulfill({ json: { apps: [{ id: "gitlab", name: "GitLab", description: "사내 코드",
      providers: [{ id: "office", name: "사내 GitLab", base_url: "https://gitlab.example", ready: true, publish_enabled: true }],
      connections: [{ id: connection, provider_id: "office", username: "alice", display_name: "첫 계정", status: "connected", can_publish: true },
        { id: otherConnection, provider_id: "office", username: "bob", display_name: "다른 계정", status: "connected", can_publish: false }] }] } });
    if (path.endsWith(`/connections/${connection}/webhooks`)) return route.fulfill({ json: { items: subscribed ? [subscription()] : [] } });
    if (path.endsWith(`/connections/${otherConnection}/webhooks`)) return route.fulfill({ json: { items: [] } });
    if (path.endsWith("/projects")) return route.fulfill({ json: { items: [{ id: 7, name: "team/service", default_branch: "main" }], has_more: false } });
    if (path.endsWith("/projects/7/webhooks") && method === "POST") {
      notifications = route.request().postDataJSON().notifications_enabled;
      subscribed = true;
      return route.fulfill({ json: { ...subscription(), secret_token: "one-time-webhook-secret", setup: "GitLab에 등록하세요." } });
    }
    if (path.endsWith(`/webhook-subscriptions/${subscriptionId}/seen`)) { seen = true; return route.fulfill({ status: 204 }); }
    if (path.endsWith(`/webhook-subscriptions/${subscriptionId}`)) {
      if (method === "PATCH") { notifications = route.request().postDataJSON().notifications_enabled; return route.fulfill({ json: subscription() }); }
      if (method === "DELETE") { subscribed = false; return route.fulfill({ status: 204 }); }
      return route.fulfill({ json: { ...subscription(), events: hasEvent ? [
        { id: "event-one", kind: "pipeline", object_id: 31, status: "failed", received_at: created, notify: notifications && !seen },
      ] : [] } });
    }
    return route.fulfill({ status: 404, json: { detail: "테스트 경로 없음" } });
  });
  await page.goto("/user/signup");
  const username = `gitsettings_${info.project.name}_${Date.now()}`;
  await page.getByLabel("표시 이름", { exact: true }).fill("GitLab 설정 사용자");
  await page.getByLabel("사용자명", { exact: true }).fill(username);
  await page.getByLabel("이메일", { exact: true }).fill(`${username}@example.com`);
  await page.getByLabel("비밀번호", { exact: true }).fill("GitlabSettingsPass123!");
  await page.getByLabel("비밀번호 확인", { exact: true }).fill("GitlabSettingsPass123!");
  await page.getByRole("checkbox").check();
  await page.getByRole("button", { name: "회원가입", exact: true }).click();
  await expect(page.getByRole("heading", { name: "무엇을 함께 해볼까요?" })).toBeVisible();
  await page.goto("/user/settings/apps");
  const dialog = page.getByRole("dialog", { name: "설정", exact: true });
  await expect(dialog.getByText("@alice", { exact: true })).toBeVisible();
  await expect(dialog.getByText("@bob", { exact: true })).toBeVisible();
  const alice = dialog.locator(".settings-gitlab-account").filter({ has: page.getByText("@alice", { exact: true }) });
  await alice.getByRole("button", { name: "MR·파이프라인 상태", exact: true }).click();
  const panel = alice.getByRole("region", { name: "GitLab MR·파이프라인 상태 구독", exact: true });
  await panel.getByText("새 프로젝트 상태 구독", { exact: true }).click();
  await panel.getByLabel("프로젝트", { exact: true }).selectOption("7");
  await panel.getByRole("checkbox", { name: "MR·파이프라인 변경을 앱에서 알림", exact: true }).check();
  await panel.getByRole("button", { name: "상태 구독 생성", exact: true }).click();
  await expect(panel.getByLabel("웹훅 Secret token", { exact: true })).toHaveValue("one-time-webhook-secret");
  await expect(panel.getByLabel("웹훅 Secret token", { exact: true })).toHaveAttribute("type", "password");
  await expect(panel.getByLabel("수신 URL", { exact: true })).toHaveValue(new URL(`/api/apps/gitlab/webhooks/${subscriptionId}`, page.url()).href);
  await panel.getByRole("button", { name: "등록 정보 닫기", exact: true }).click();
  await expect(panel.getByLabel("웹훅 Secret token", { exact: true })).toHaveCount(0);
  hasEvent = true;
  await dialog.getByRole("button", { name: "설정 닫기", exact: true }).click();
  await page.evaluate(() => window.dispatchEvent(new Event("llm:apps-changed")));
  await expect(page.getByRole("link", { name: "GitLab 상태 업데이트 1개", exact: true })).toBeVisible();
  await page.getByRole("link", { name: "GitLab 상태 업데이트 1개", exact: true }).click();
  await alice.getByRole("button", { name: "MR·파이프라인 상태", exact: true }).click();
  await panel.getByRole("button", { name: "상태 확인", exact: true }).click();
  await expect(panel.getByRole("region", { name: "team/service 최근 상태", exact: true })).toContainText("failed");
  await panel.getByRole("button", { name: "알림 읽음 처리", exact: true }).click();
  await dialog.getByRole("button", { name: "설정 닫기", exact: true }).click();
  await expect(page.getByRole("link", { name: /GitLab 상태 업데이트/ })).toHaveCount(0);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});
