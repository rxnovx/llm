const CHAT_ID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
export const WORKSPACE_LOCATION_EVENT = "llm:workspace-location";
const LOGIN_RETURN_PATHS = new Set(["/", "/admin", "/user/profile", "/user/profile/edit", "/user/settings", "/user/settings/apps"]);
const routes: Record<string, { kind: "auth" | "settings" | "admin"; title: string }> = {
  "/login": { kind: "auth", title: "로그인" },
  "/user/signup": { kind: "auth", title: "계정 만들기" },
  "/user/verify-email": { kind: "auth", title: "이메일 인증" },
  "/user/reset-password": { kind: "auth", title: "비밀번호 재설정" },
  "/user/recover": { kind: "auth", title: "계정 복구" },
  "/user/profile": { kind: "settings", title: "설정" },
  "/user/profile/edit": { kind: "settings", title: "설정" },
  "/user/settings": { kind: "settings", title: "설정" },
  "/user/settings/apps": { kind: "settings", title: "설정" },
  "/apps/gitlab/callback": { kind: "auth", title: "GitLab 연결" },
  "/admin": { kind: "admin", title: "운영 대시보드" },
};
export function workspaceModal(pathname: string) {
  return pathname === "/" ? null : routes[pathname] ?? { kind: "other" as const, title: "안내" };
}
export function modalContentScope(kind: string, identity: string) {
  // Account/admin state belongs to one identity; verification/login forms must
  // finish and display their result even when that action changes the session.
  return kind === "settings" || kind === "admin" ? identity : "public-flow";
}
export function workspaceReturnUrl(search: string) {
  const id = new URLSearchParams(search).get("chat");
  return id && CHAT_ID.test(id) ? `/?chat=${id}` : "/";
}
export function workspaceHref(href: string, search: string) {
  const url = new URL(href, "https://llm.invalid");
  if (url.origin !== "https://llm.invalid") return href;
  const current = new URLSearchParams(search);
  const id = current.get("chat");
  if (id && CHAT_ID.test(id) && !url.searchParams.has("chat")) url.searchParams.set("chat", id);
  // Continue a login/signup/recovery flow without copying verification tokens.
  if (routes[url.pathname]?.kind === "auth" && !url.searchParams.has("next") && current.has("next")) {
    const next = current.get("next")!;
    if (/^\/(?:\?|$)|^\/(?:admin|user\/(?:profile(?:\/edit)?|settings(?:\/apps)?))(?:\?|$)/.test(next)
        && !/[\\\r\n]/.test(next)) url.searchParams.set("next", next);
  }
  return url.pathname + url.search + url.hash;
}
export function getSafeNextPath(value: string | null | undefined) {
  if (!value || !value.startsWith("/") || value.startsWith("//") || /[\\\r\n]/.test(value)) return "/";
  const url = new URL(value, "https://llm.invalid");
  return url.origin === "https://llm.invalid" && LOGIN_RETURN_PATHS.has(url.pathname) ? url.pathname + url.search : "/";
}
export function loginReturnUrl(search: string) {
  const next = getSafeNextPath(new URLSearchParams(search).get("next"));
  return next === "/" || next.startsWith("/?") ? "/" : workspaceHref(next, search);
}
export function conversationLocation(pathname: string, search: string, id: string | null) {
  const query = new URLSearchParams(search);
  if (id && CHAT_ID.test(id)) query.set("chat", id);
  else query.delete("chat");
  const suffix = query.toString();
  return `${pathname}${suffix ? `?${suffix}` : ""}`;
}
export function replaceConversationLocation(id: string | null) {
  window.history.replaceState(null, "", conversationLocation(window.location.pathname, window.location.search, id));
  window.dispatchEvent(new Event(WORKSPACE_LOCATION_EVENT));
}
