const CHAT_ID = /^[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}$/i;
const INTERNAL_ORIGIN = "https://llm.invalid";
export const WORKSPACE_LOCATION_EVENT = "llm:workspace-location";
type WorkspaceLocation = Pick<Location, "pathname" | "search">;
const LOGIN_RETURN_PATHS = new Set(["/", "/admin", "/user/profile", "/user/profile/edit", "/user/settings", "/user/settings/apps"]);
const routes = new Map<string, { kind: "auth" | "settings" | "admin"; title: string }>([
  ["/login", { kind: "auth", title: "로그인" }],
  ["/user/signup", { kind: "auth", title: "계정 만들기" }],
  ["/user/verify-email", { kind: "auth", title: "이메일 인증" }],
  ["/user/reset-password", { kind: "auth", title: "비밀번호 재설정" }],
  ["/user/recover", { kind: "auth", title: "계정 복구" }],
  ["/user/profile", { kind: "settings", title: "설정" }],
  ["/user/profile/edit", { kind: "settings", title: "설정" }],
  ["/user/settings", { kind: "settings", title: "설정" }],
  ["/user/settings/apps", { kind: "settings", title: "설정" }],
  ["/apps/gitlab/callback", { kind: "auth", title: "GitLab 연결" }],
  ["/admin", { kind: "admin", title: "운영 대시보드" }],
]);

export function normalizeConversationId(id: string | null | undefined) {
  return id && CHAT_ID.test(id) ? id.toLowerCase() : null;
}

export function conversationPath(id: string | null) {
  const normalized = normalizeConversationId(id);
  return normalized ? `/c/${normalized}` : "/";
}

export function conversationIdFromPath(pathname: string | null) {
  return pathname?.startsWith("/c/") ? normalizeConversationId(pathname.slice(3)) : null;
}

function modalConversationId(query: URLSearchParams) {
  return query.has("returnTo")
    ? conversationIdFromPath(query.get("returnTo"))
    : normalizeConversationId(query.get("chat")); // Links saved before /c was introduced.
}

export function workspaceConversationId({ pathname, search }: WorkspaceLocation) {
  return routes.has(pathname) ? modalConversationId(new URLSearchParams(search)) : conversationIdFromPath(pathname);
}

export function workspaceModal(pathname: string) {
  return pathname === "/" || conversationIdFromPath(pathname) ? null : routes.get(pathname) ?? { kind: "other" as const, title: "안내" };
}

export function modalContentScope(kind: string, identity: string) {
  // Account/admin state belongs to one identity; verification/login forms must
  // finish and display their result even when that action changes the session.
  return kind === "settings" || kind === "admin" ? identity : "public-flow";
}

export function workspaceReturnUrl(search: string) {
  return conversationPath(modalConversationId(new URLSearchParams(search)));
}

function setModalConversation(url: URL, id: string | null) {
  url.searchParams.delete("chat");
  if (id) url.searchParams.set("returnTo", conversationPath(id));
  else url.searchParams.delete("returnTo");
}

function relativeUrl(url: URL) {
  return url.pathname + url.search + url.hash;
}

function safeLoginTarget(value: string | null | undefined) {
  if (!value || /[\\\r\n\t]/.test(value)) return null;
  const pathname = value.split(/[?#]/, 1)[0];
  const id = conversationIdFromPath(pathname);
  if (id) return conversationPath(id);
  if (!LOGIN_RETURN_PATHS.has(pathname)) return null;
  if (pathname === "/") return "/";
  const url = new URL(value, INTERNAL_ORIGIN);
  setModalConversation(url, modalConversationId(url.searchParams));
  return url.pathname + url.search;
}

export function workspaceHref(href: string, location: WorkspaceLocation) {
  if (href === "/" && routes.has(location.pathname)) return workspaceReturnUrl(location.search);
  const url = new URL(href, INTERNAL_ORIGIN);
  const route = routes.get(url.pathname);
  if (url.origin !== INTERNAL_ORIGIN || !route) return href;
  const explicitContext = url.searchParams.has("returnTo") || url.searchParams.has("chat");
  setModalConversation(url, explicitContext ? modalConversationId(url.searchParams) : workspaceConversationId(location));
  // Continue the auth flow without carrying over verification tokens or email addresses.
  if (route.kind === "auth") {
    const next = safeLoginTarget(url.searchParams.get("next") ?? new URLSearchParams(location.search).get("next"));
    if (next) url.searchParams.set("next", next);
    else url.searchParams.delete("next");
  }
  return relativeUrl(url);
}

export function getSafeNextPath(value: string | null | undefined) {
  return safeLoginTarget(value) ?? "/";
}

export function conversationLoginUrl(id: string) {
  return `/login?${new URLSearchParams({ next: conversationPath(id) })}`;
}

export function loginReturnUrl(search: string) {
  const location = { pathname: "/login", search };
  const next = getSafeNextPath(new URLSearchParams(search).get("next"));
  return next === "/" ? next : workspaceHref(next, location);
}

export function conversationLocation(location: WorkspaceLocation, id: string | null) {
  if (!routes.has(location.pathname)) return conversationPath(id);
  const url = new URL(location.pathname + location.search, INTERNAL_ORIGIN);
  setModalConversation(url, normalizeConversationId(id));
  return relativeUrl(url);
}

export function notifyWorkspaceLocation() {
  window.dispatchEvent(new Event(WORKSPACE_LOCATION_EVENT));
}

export function updateConversationLocation(id: string | null, mode: "push" | "replace") {
  const { location, history } = window;
  const href = conversationLocation(location, id);
  if (href === location.pathname + location.search) return;
  if (mode === "push") history.pushState(null, "", href);
  else history.replaceState(null, "", href);
  notifyWorkspaceLocation();
}
