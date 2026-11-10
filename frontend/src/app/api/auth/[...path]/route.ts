import { NextRequest, NextResponse } from "next/server";
import { authServerFetch, bodyError, REFRESH_COOKIE_NAME, rejectUnsafeMutation,
  requestBody, setRefreshCookie, upstreamResponse } from "@/lib/server/auth-server";

export const runtime = "nodejs";
const routes: Record<string, string> = {
  config: "GET", me: "GET", register: "POST", login: "POST", google: "POST", refresh: "POST", logout: "POST",
  "email-verification/resend": "POST", "email-verification/confirm": "POST",
  "password-reset/request": "POST", "password-reset/confirm": "POST",
  "account-recovery/request": "POST", "account-recovery/confirm": "POST",
};
type Context = { params: Promise<{ path: string[] }> };

async function handle(request: NextRequest, context: Context) {
  const name = (await context.params).path.join("/");
  if (routes[name] !== request.method) return NextResponse.json({ detail: "경로를 찾을 수 없습니다." }, { status: 404 });
  const rejection = rejectUnsafeMutation(request);
  if (rejection) return rejection;
  let body: BodyInit | undefined;
  const headers = new Headers();
  const authorization = request.headers.get("authorization");
  if (authorization) headers.set("authorization", authorization);
  try {
    if (request.method !== "GET") {
      const raw = await requestBody(request);
      if (["refresh", "logout"].includes(name)) {
        const refresh = request.cookies.get(REFRESH_COOKIE_NAME)?.value;
        if (!refresh) {
          const response = name === "logout" ? new NextResponse(null, { status: 204 })
            : NextResponse.json({ detail: "로그인 세션이 없습니다." }, { status: 401 });
          if (name === "logout") setRefreshCookie(response, "", true);
          return response;
        }
        const data = raw.byteLength ? JSON.parse(new TextDecoder().decode(raw)) : {};
        body = JSON.stringify({ refresh_token: refresh, all_sessions: data.all_sessions === true });
        headers.set("content-type", "application/json");
      } else if (name === "login") {
        const data = JSON.parse(new TextDecoder().decode(raw));
        if (typeof data.loginId !== "string" || typeof data.password !== "string") return bodyError(null);
        body = new URLSearchParams({ username: data.loginId.trim(), password: data.password });
        headers.set("content-type", "application/x-www-form-urlencoded");
      } else {
        body = raw;
        headers.set("content-type", "application/json");
      }
    }
  } catch (error) { return bodyError(error); }
  const upstream = await authServerFetch(`/auth/${name}`, { method: request.method, headers, body });
  if (!upstream.ok) {
    const response = await upstreamResponse(upstream);
    // An outage or rate limit does not invalidate a refresh cookie.
    if (name === "refresh" && [400, 401, 403].includes(upstream.status)) setRefreshCookie(response, "", true);
    return response;
  }
  if (name === "logout") {
    const response = new NextResponse(null, { status: 204 });
    setRefreshCookie(response, "", true);
    return response;
  }
  if (["login", "refresh", "google"].includes(name)) {
    let token;
    try { token = await upstream.json(); }
    catch { return NextResponse.json({ detail: "인증 서버의 응답이 올바르지 않습니다." }, { status: 502 }); }
    if (!token || typeof token.access_token !== "string" || typeof token.refresh_token !== "string" || !token.user) {
      return NextResponse.json({ detail: "인증 서버의 응답이 올바르지 않습니다." }, { status: 502 });
    }
    const response = NextResponse.json({ access_token: token.access_token, token_type: token.token_type,
      expires_in: token.expires_in, user: token.user }, { headers: { "Cache-Control": "no-store" } });
    setRefreshCookie(response, token.refresh_token);
    return response;
  }
  return upstreamResponse(upstream);
}
export const GET = handle;
export const POST = handle;
