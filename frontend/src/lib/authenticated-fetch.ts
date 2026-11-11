import { getAccessToken, notifyAuthExpired, refreshAccessToken } from "./auth";

export class ApiRequestError extends Error {
  readonly status: number;
  constructor(message: string, status: number) { super(message); this.status = status; }
}

export async function authenticatedFetch(url: string, init: RequestInit = {}): Promise<Response> {
  let token = getAccessToken();
  if (!token) token = await refreshAccessToken();
  const request = () => {
    const headers = new Headers(init.headers);
    if (!(typeof FormData !== 'undefined' && init.body instanceof FormData)) headers.set("content-type", "application/json");
    headers.set("x-llm-account", "1");
    if (token) headers.set("authorization", `Bearer ${token}`);
    return fetch(url, { ...init, credentials: "same-origin", headers });
  };
  let response = await request();
  if (response.status === 401 && !init.signal?.aborted) {
    token = await refreshAccessToken();
    if (token) response = await request();
    if (response.status === 401) notifyAuthExpired();
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => null);
    throw new ApiRequestError(typeof payload?.detail === "string" ? payload.detail : "요청을 처리하지 못했습니다. 다시 시도해 주세요.", response.status);
  }
  return response;
}
