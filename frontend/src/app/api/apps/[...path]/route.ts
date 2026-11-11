import { createHash, randomBytes } from "node:crypto";
import { NextRequest, NextResponse } from "next/server";
import { authServerFetch, bodyError, rejectUnsafeMutation, requestBody, upstreamResponse } from "@/lib/server/auth-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const uuid = "[a-fA-F0-9-]{36}";
const rules = [
  [/^catalog$/, ["GET"]],
  [/^gitlab\/[a-zA-Z0-9_-]{1,64}\/authorize$/, ["POST"]],
  [/^gitlab\/oauth\/complete$/, ["POST"]],
  [/^gitlab\/merge-request\/resolve$/, ["POST"]],
  [new RegExp(`^connections/${uuid}$`), ["DELETE"]],
  [new RegExp(`^connections/${uuid}/projects(?:/[1-9][0-9]*/(?:tree|branches))?$`), ["GET"]],
] as const;
const cookiePath = "/api/apps/gitlab/oauth/complete";
const cookieName = (key: string) => `llm_gitlab_oauth_${key}`;

async function handle(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const path = (await context.params).path.join("/");
  if (!rules.some(([pattern, methods]) => pattern.test(path) && (methods as readonly string[]).includes(request.method)))
    return NextResponse.json({ detail: "경로를 찾을 수 없습니다." }, { status: 404 });
  const rejection = rejectUnsafeMutation(request);
  if (rejection) return rejection;
  const headers = new Headers({ "content-type": "application/json" });
  const authorization = request.headers.get("authorization");
  if (authorization) headers.set("authorization", authorization);
  let body: ArrayBuffer | undefined, key = "", browser = "";
  const start = path.endsWith("/authorize"), complete = path === "gitlab/oauth/complete";
  if (start && process.env.APP_URL && request.headers.get("origin") !== new URL(process.env.APP_URL).origin) {
    return NextResponse.json({ detail: `GitLab 인증은 ${new URL(process.env.APP_URL).origin} 주소에서 시작하세요. 인증 후에도 같은 브라우저 주소를 사용해야 합니다.` }, { status: 400 });
  }
  try {
    if (request.method !== "GET") body = await requestBody(request, 65536);
    if (start) { browser = randomBytes(32).toString("base64url"); headers.set("x-oauth-browser", browser); }
    if (complete) {
      const value = JSON.parse(new TextDecoder().decode(body));
      if (typeof value.state !== "string" || value.state.length < 32 || value.state.length > 200) throw new Error();
      key = createHash("sha256").update(value.state).digest("hex").slice(0, 16);
      headers.set("x-oauth-browser", request.cookies.get(cookieName(key))?.value ?? "");
    }
  } catch (error) { return bodyError(error); }
  const upstream = await authServerFetch(`/apps/${path}${request.nextUrl.search}`, {
    method: request.method, headers, body, signal: request.signal,
  }, 95000); // Up to three bounded backend operations, each at most 30 seconds.
  let response: NextResponse;
  if (start && upstream.ok) {
    const data = await upstream.json().catch(() => null);
    if (!data || typeof data.authorize_url !== "string" || !/^[a-f0-9]{16}$/.test(data.cookie_key))
      return NextResponse.json({ detail: "OAuth 인증 응답이 올바르지 않습니다." }, { status: 502 });
    key = data.cookie_key;
    response = NextResponse.json({ authorize_url: data.authorize_url }, { headers: { "Cache-Control": "no-store" } });
  } else response = await upstreamResponse(upstream);
  // A 401 can be retried after our application access-token refresh.
  if (start && upstream.ok || complete && upstream.status !== 401) response.cookies.set(cookieName(key), start ? browser : "", {
    httpOnly: true, secure: process.env.AUTH_COOKIE_SECURE === "true", sameSite: "lax", path: cookiePath, maxAge: start ? 600 : 0,
  });
  return response;
}
export const GET = handle;
export const POST = handle;
export const DELETE = handle;
