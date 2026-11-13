import { authenticatedFetch } from "./authenticated-fetch";
import { conversationLocation, workspaceConversationId } from "./workspace-navigation";

export type AppConnection = { id: string; app: "gitlab"; provider_id: string; username: string; display_name: string; status: "connected" | "needs_reconnect"; can_publish?: boolean };
export type AppProvider = { id: string; name: string; base_url: string; ready: boolean; reason: string | null; publish_enabled?: boolean };
export type AppCatalog = { apps: { id: "gitlab"; name: string; description: string; providers: AppProvider[]; connections: AppConnection[] }[] };
export type GitlabSelection = { app: "gitlab"; connection_id: string; project_id: number; ref: string; paths: string[];
  mode?: "files" | "workspace" | "execute" | "merge_request"; workspace_id?: string; merge_request?: { project_id: number; iid: number } };
export type MergeRequestSummary = {
  iid: number; project: string; url: string; title: string; state: string; source_branch: string; target_branch: string; editable: boolean;
};
export type ResolvedMergeRequest = { selection: GitlabSelection; merge_request: MergeRequestSummary };
export type GitlabSource = { id: string; app: "gitlab"; project: string; path: string; start_line: number; end_line: number; commit: string; url: string; working_copy?: boolean };
export function gitlabMergeRequestLink(text: string): string {
  return text.match(/https?:\/\/[^\s<>"\x60]+\/-\/merge_requests\/[1-9][0-9]{0,11}(?:\/(?:diffs|commits|pipelines))?\/?(?![\w/])/)?.[0] ?? "";
}
export const APPS_CHANGED = "llm:apps-changed";
export const OAUTH_LEAVING = "llm:oauth-leaving";
const DRAFT_KEY = "llm:oauth-draft";
export async function appsJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await authenticatedFetch(`/api/apps${path}`, init);
  return await response.json() as T;
}
export function notifyAppsChanged() {
  window.dispatchEvent(new Event(APPS_CHANGED));
  if (typeof BroadcastChannel !== "undefined") { const channel = new BroadcastChannel(APPS_CHANGED); channel.postMessage("changed"); channel.close(); }
}
export function watchAppsChanged(refresh: () => void) {
  const channel = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel(APPS_CHANGED) : null;
  if (channel) channel.onmessage = refresh;
  window.addEventListener(APPS_CHANGED, refresh); window.addEventListener("focus", refresh);
  return () => { channel?.close(); window.removeEventListener(APPS_CHANGED, refresh); window.removeEventListener("focus", refresh); };
}
export function backupOAuthDraft(owner: string, draft: string, chat: string | null) {
  try { sessionStorage.setItem(DRAFT_KEY, JSON.stringify({ owner, draft, chat, until: Date.now()+600000 })); } catch { /* Private mode may deny storage. */ }
}
export function takeOAuthDraft(owner: string, chat: string | null): string | null {
  try {
    const value = sessionStorage.getItem(DRAFT_KEY);
    if (!value) return null;
    sessionStorage.removeItem(DRAFT_KEY);
    const item = JSON.parse(value);
    return item.owner === owner && item.chat === chat && item.until > Date.now() && typeof item.draft === "string" ? item.draft.slice(0, 16000) : null;
  } catch { return null; }
}
export function oauthReturnPath(owner: string | undefined) {
  const settings = { pathname: "/user/settings/apps", search: "" };
  try {
    const item = JSON.parse(sessionStorage.getItem(DRAFT_KEY) ?? "null");
    if (item && item.owner === owner && item.until > Date.now() && typeof item.chat === "string")
      return conversationLocation(settings, item.chat);
  } catch { /* Storage may be unavailable. */ }
  return settings.pathname;
}
export async function connectGitlab(provider: string, publish = false) {
  const popup = window.open("about:blank", "_blank", "popup,width=620,height=780");
  if (popup) popup.opener = null;
  try {
    const chat = workspaceConversationId(window.location);
    const data = await appsJson<{ authorize_url: string }>(`/gitlab/${encodeURIComponent(provider)}/authorize`, {
      method: "POST", body: JSON.stringify({ chat_id: chat, popup: !!popup, ...(publish ? { publish } : {}) }),
    });
    if (popup) {
      if (popup.closed) throw new Error("인증 창이 닫혔습니다. 다시 연결하세요.");
      popup.location.href = data.authorize_url;
    } else { window.dispatchEvent(new Event(OAUTH_LEAVING)); window.location.assign(data.authorize_url); }
  } catch (error) { popup?.close(); throw error; }
}
