import "server-only";
import { headers as requestHeaders } from "next/headers";
import { NextRequest, NextResponse } from "next/server";

export const REFRESH_COOKIE_NAME = "llm_accounts_refresh";
const timeout = Number(process.env.AUTH_SERVER_TIMEOUT_MS ?? 8000);
export const MAX_BODY_BYTES = 6 * 1024 * 1024;

function allowedOrigins(requestUrl: string): Set<string> {
  const entries = [process.env.APP_URL?.trim() || new URL(requestUrl).origin,
    ...(process.env.APP_ALLOWED_ORIGINS ?? "").split(",").map((value) => value.trim()).filter(Boolean)];
  return new Set(entries.map((value) => {
    const url = new URL(value);
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password ||
        url.pathname !== "/" || url.search || url.hash) {
      throw new Error("APP_URL and APP_ALLOWED_ORIGINS must be HTTP(S) origins");
    }
    return url.origin;
  }));
}

export function rejectUnsafeMutation(request: NextRequest): NextResponse | null {
  if (["GET", "HEAD", "OPTIONS"].includes(request.method)) return null;
  const origin = request.headers.get("origin");
  let origins: Set<string>;
  try {
    origins = allowedOrigins(request.url);
  } catch {
    return NextResponse.json({ detail: "프론트엔드의 APP_URL / APP_ALLOWED_ORIGINS 설정을 확인해 주세요." }, { status: 503 });
  }
  if (request.headers.get("x-llm-account") !== "1" ||
      request.headers.get("sec-fetch-site") === "cross-site" ||
      (origin !== null && !origins.has(origin))) {
    return NextResponse.json({ detail: "요청 출처를 확인할 수 없습니다." }, { status: 403 });
  }
  return null;
}

export async function requestBody(request: NextRequest, maxBodyBytes = MAX_BODY_BYTES): Promise<ArrayBuffer> {
  const limit = Number(request.headers.get("content-length"));
  if (limit > maxBodyBytes) throw new RangeError("요청 크기가 너무 큽니다.");
  if (!request.body) return new ArrayBuffer(0);
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      size += value.byteLength;
      if (size > maxBodyBytes) {
        await reader.cancel();
        throw new RangeError("요청 크기가 너무 큽니다.");
      }
      chunks.push(value);
    }
  } finally { reader.releaseLock(); }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) { result.set(chunk, offset); offset += chunk.byteLength; }
  return result.buffer;
}

export async function authServerFetch(path: string, init: RequestInit = {}, timeoutMs = timeout): Promise<Response> {
  const outgoing = new Headers(init.headers);
  const incoming = await requestHeaders();
  const userAgent = incoming.get("user-agent");
  if (userAgent) outgoing.set("user-agent", userAgent);
  const secret = process.env.AUTH_PROXY_SECRET;
  const ipHeader = process.env.AUTH_PROXY_CLIENT_IP_HEADER;
  if (secret && ipHeader && /^[a-z0-9-]+$/i.test(ipHeader)) {
    const ip = incoming.get(ipHeader)?.split(",", 1)[0]?.trim();
    if (ip) { outgoing.set("x-gyeop-proxy-secret", secret); outgoing.set("x-gyeop-client-ip", ip); }
  }
  try {
    return await fetch(`${(process.env.AUTH_SERVER_URL ?? "http://127.0.0.1:8000").replace(/\/+$/, "")}${path}`, {
      ...init, headers: outgoing, cache: "no-store",
      signal: AbortSignal.any([AbortSignal.timeout(Number.isFinite(timeoutMs) && timeoutMs > 0 ? timeoutMs : 8000),
        ...(init.signal ? [init.signal] : [])]),
    });
  } catch (error) {
    const timedOut = error instanceof Error && ["TimeoutError", "AbortError"].includes(error.name);
    return Response.json({ detail: timedOut ? "인증 서버 응답 시간이 초과되었습니다." : "인증 서버에 연결할 수 없습니다." },
      { status: timedOut ? 504 : 503 });
  }
}

export function setRefreshCookie(response: NextResponse, token: string, clear = false) {
  response.cookies.set(REFRESH_COOKIE_NAME, token, {
    httpOnly: true, secure: process.env.AUTH_COOKIE_SECURE === "true",
    sameSite: "lax", path: "/api/auth", maxAge: clear ? 0 : Number(process.env.REFRESH_TOKEN_DAYS ?? 14) * 86400,
  });
}

export async function upstreamResponse(upstream: Response): Promise<NextResponse> {
  const response = new NextResponse(upstream.status === 204 ? null : await upstream.arrayBuffer(), {
    status: upstream.status, headers: { "Cache-Control": "no-store",
      "content-type": upstream.headers.get("content-type") ?? "application/json" },
  });
  const retryAfter = upstream.headers.get("retry-after");
  if (retryAfter) response.headers.set("retry-after", retryAfter);
  return response;
}

export function bodyError(error: unknown): NextResponse {
  return NextResponse.json({ detail: error instanceof RangeError ? "요청 크기가 너무 큽니다." : "요청 형식이 올바르지 않습니다." },
    { status: error instanceof RangeError ? 413 : 400 });
}
