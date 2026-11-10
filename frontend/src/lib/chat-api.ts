import { getAccessToken, notifyAuthExpired, refreshAccessToken } from "./auth";

export async function chatFetch(path: string, init: RequestInit = {}): Promise<Response> {
  let token = getAccessToken();
  if (!token) token = await refreshAccessToken();
  const request = () => {
    const headers = new Headers(init.headers);
    headers.set("content-type", "application/json"); headers.set("x-llm-account", "1");
    if (token) headers.set("authorization", `Bearer ${token}`);
    return fetch(`/api/chat${path}`, { ...init, credentials: "same-origin", headers });
  };
  let response = await request();
  if (response.status === 401 && !init.signal?.aborted) {
    token = await refreshAccessToken();
    if (token) response = await request();
    if (response.status === 401) notifyAuthExpired();
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new Error(typeof payload?.detail === "string" ? payload.detail : "요청을 처리하지 못했습니다. 다시 시도해 주세요.");
  }
  return response;
}

export async function chatJson<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await chatFetch(path, init);
  return (response.status === 204 ? undefined : await response.json()) as T;
}
