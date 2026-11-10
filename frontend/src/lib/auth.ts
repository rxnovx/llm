import type { AuthSessionResponse, AuthUser } from "@/types/auth";

export const AUTH_EXPIRED_EVENT = "auth:expired";
export const AUTH_TOKEN_UPDATED_EVENT = "auth:token-updated";

const REFRESH_TIMEOUT_MS = 10_000;
const REFRESH_LOCK_KEY = "llm:accounts:auth:refresh-lock";
const AUTH_CHANNEL_NAME = "llm:accounts:auth";
const LOCK_TTL_MS = 12_000;
const instanceId =
  typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : Math.random().toString(36).slice(2);

let accessToken: string | null = null;
let refreshPromise: Promise<string | null> | null = null;
let sessionQueue: Promise<void> = Promise.resolve();
let sharedChannel: BroadcastChannel | null = null;
let refreshedUser: AuthUser | null = null;
let sessionVersion = 0;
let authSessionId = `${instanceId}:${sessionVersion}`;
const authSessionListeners = new Set<() => void>();

/** Changes on login/logout and account-mode changes, not access-token refresh. */
export function getAuthSessionVersion() { return sessionVersion; }

/** Observe explicit session replacement without treating token refresh as logout. */
export function subscribeAuthSession(listener: () => void) {
  authSessionListeners.add(listener);
  return () => { authSessionListeners.delete(listener); };
}

function advanceAuthSession(sharedSessionId?: string) {
  sessionVersion += 1;
  authSessionId = sharedSessionId ?? `${instanceId}:${sessionVersion}`;
  refreshedUser = null;
  for (const listener of Array.from(authSessionListeners)) listener();
}

export function consumeRefreshedUser() {
  const user = refreshedUser;
  refreshedUser = null;
  return user;
}

type AuthChannelMessage =
  | { type: "token"; token: string; source: string; sessionId?: string; sessionReplaced?: boolean }
  | { type: "expired"; source: string };

// Only identifies same-account refresh messages; the API validates the JWT.
function accessTokenSubject(token: string | null): string | null {
  if (!token) return null;
  try {
    const segment = token.split(".")[1];
    if (!segment) return null;
    const payload: unknown = JSON.parse(atob(segment.replace(/-/g, "+").replace(/_/g, "/")));
    return payload && typeof payload === "object" && "sub" in payload && typeof payload.sub === "string" ? payload.sub : null;
  } catch { return null; }
}

function getChannel() {
  if (typeof window === "undefined" || !("BroadcastChannel" in window)) {
    return null;
  }
  if (!sharedChannel) {
    sharedChannel = new BroadcastChannel(AUTH_CHANNEL_NAME);
    sharedChannel.addEventListener(
      "message",
      (event: MessageEvent<AuthChannelMessage>) => {
        if (event.data.source === instanceId) return;
        if (event.data.type === "token") {
          // A newly opened tab bootstraps from the same refresh cookie with its
          // own ID. Preserve pending work for that same-account refresh, while
          // explicit logins replace the session even for the same account.
          if (event.data.sessionId !== authSessionId) {
            const subject = accessTokenSubject(accessToken);
            if (!event.data.sessionReplaced && subject && subject === accessTokenSubject(event.data.token)) {
              authSessionId = event.data.sessionId ?? authSessionId;
            } else {
              advanceAuthSession(event.data.sessionId);
            }
          }
          accessToken = event.data.token;
          window.dispatchEvent(new Event(AUTH_TOKEN_UPDATED_EVENT));
        } else {
          advanceAuthSession();
          accessToken = null;
          window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
        }
      },
    );
  }
  return sharedChannel;
}

function broadcast(message: AuthChannelMessage) {
  const channel = getChannel();
  channel?.postMessage(message);
}

function readRefreshLock(): { owner: string; expiresAt: number } | null {
  if (typeof window === "undefined") return null;
  try {
    const value = JSON.parse(
      window.localStorage.getItem(REFRESH_LOCK_KEY) ?? "null",
    ) as { owner?: string; expiresAt?: number } | null;
    if (
      !value ||
      typeof value.owner !== "string" ||
      typeof value.expiresAt !== "number"
    ) {
      return null;
    }
    return { owner: value.owner, expiresAt: value.expiresAt };
  } catch {
    return null;
  }
}

function acquireRefreshLock() {
  if (typeof window === "undefined") return true;
  const now = Date.now();
  const existing = readRefreshLock();
  if (existing && existing.owner !== instanceId && existing.expiresAt > now) {
    return false;
  }
  try {
    window.localStorage.setItem(
      REFRESH_LOCK_KEY,
      JSON.stringify({ owner: instanceId, expiresAt: now + LOCK_TTL_MS }),
    );
    return readRefreshLock()?.owner === instanceId;
  } catch {
    // Private browsing may deny storage; same-tab requests still use the queue.
    return true;
  }
}

function releaseRefreshLock() {
  if (typeof window === "undefined") return;
  if (readRefreshLock()?.owner === instanceId) {
    try { window.localStorage.removeItem(REFRESH_LOCK_KEY); } catch { /* Storage may have been disabled. */ }
  }
}

/** Serialize cookie-changing login, refresh, and logout across tabs where supported. */
export function withAuthSessionLock<T>(work: () => Promise<T>): Promise<T> {
  const run = async () => {
    if (typeof navigator !== "undefined" && navigator.locks) {
      return navigator.locks.request("llm:accounts:session", work);
    }
    const started = Date.now();
    while (!acquireRefreshLock()) {
      if (Date.now() - started > LOCK_TTL_MS) throw new Error("다른 창의 로그인 처리가 끝난 뒤 다시 시도해주세요.");
      await new Promise((resolve) => setTimeout(resolve, 50));
    }
    try { return await work(); } finally { releaseRefreshLock(); }
  };
  const result = sessionQueue.then(run, run);
  sessionQueue = result.then(() => undefined, () => undefined);
  return result;
}

function waitForTokenFromAnotherTab(
  signal?: AbortSignal,
): Promise<string | null> {
  const channel = getChannel();
  if (!channel) {
    return new Promise((resolve) => window.setTimeout(() => resolve(null), 800));
  }
  return new Promise((resolve) => {
    const finish = (token: string | null) => {
      window.clearTimeout(timer);
      signal?.removeEventListener("abort", handleAbort);
      channel.removeEventListener("message", handleMessage);
      resolve(token);
    };
    const handleAbort = () => finish(null);
    const timer = window.setTimeout(() => finish(null), 3_000);
    const handleMessage = (event: MessageEvent<AuthChannelMessage>) => {
      if (event.data.source === instanceId) return;
      if (event.data.type === "token") {
        finish(event.data.token);
      } else if (event.data.type === "expired") {
        finish(null);
      }
    };
    channel.addEventListener("message", handleMessage);
    signal?.addEventListener("abort", handleAbort, { once: true });
  });
}

if (typeof window !== "undefined") {
  getChannel();
}

async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit = {},
  timeoutMs = REFRESH_TIMEOUT_MS,
) {
  const controller = new AbortController();
  const externalSignal = init.signal;
  const handleExternalAbort = () => controller.abort(externalSignal?.reason);

  if (externalSignal?.aborted) {
    handleExternalAbort();
  } else {
    externalSignal?.addEventListener("abort", handleExternalAbort, {
      once: true,
    });
  }

  const timeoutId = setTimeout(
    () =>
      controller.abort(
        new DOMException("요청 시간이 초과되었습니다.", "TimeoutError"),
      ),
    timeoutMs,
  );

  try {
    return await fetch(input, {
      ...init,
      credentials: "same-origin",
      signal: controller.signal,
    });
  } finally {
    clearTimeout(timeoutId);
    externalSignal?.removeEventListener("abort", handleExternalAbort);
  }
}

export function getAccessToken() {
  return accessToken;
}

export function setAccessToken(token: string | null, share = true) {
  advanceAuthSession();
  applyAccessToken(token, share, true);
}

function applyAccessToken(token: string | null, share: boolean, sessionReplaced = false) {
  accessToken = token;
  if (share && token) {
    broadcast({ type: "token", token, source: instanceId, sessionId: authSessionId, sessionReplaced });
  }
}

export function clearAccessToken(share = false) {
  advanceAuthSession();
  accessToken = null;
  if (share) {
    broadcast({ type: "expired", source: instanceId });
  }
}


export function notifyAuthExpired() {
  clearAccessToken(true);
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(AUTH_EXPIRED_EVENT));
  }
}

export async function refreshAccessToken(
  signal?: AbortSignal,
): Promise<string | null> {
  if (refreshPromise) return refreshPromise;

  refreshPromise = withAuthSessionLock(async () => {
    const requestVersion = sessionVersion;
    let ownsLock = acquireRefreshLock();
    if (!ownsLock) {
      const sharedToken = await waitForTokenFromAnotherTab(signal);
      if (signal?.aborted) return null;
      if (sharedToken) return accessToken === sharedToken ? sharedToken : null;
      if (requestVersion !== sessionVersion) return null;
      ownsLock = acquireRefreshLock();
      if (!ownsLock) return null;
    }

    try {
      const response = await fetchWithTimeout(
        "/api/auth/refresh",
        { method: "POST", cache: "no-store", signal, headers: { "x-llm-account": "1" } },
        REFRESH_TIMEOUT_MS,
      );
      if (signal?.aborted || requestVersion !== sessionVersion) return null;
      if (!response.ok) {
        if (response.status === 401 || response.status === 403) {
          notifyAuthExpired();
        }
        return null;
      }
      const body = (await response.json()) as AuthSessionResponse;
      if (signal?.aborted || requestVersion !== sessionVersion) return null;
      if (!body.access_token) {
        // A malformed server response does not prove the session was revoked.
        return null;
      }
      applyAccessToken(body.access_token, true);
      refreshedUser = body.user ?? null;
      return body.access_token;
    } catch {
      return null;
    } finally {
      releaseRefreshLock();
    }
  }).finally(() => {
    refreshPromise = null;
  });

  return refreshPromise;
}
