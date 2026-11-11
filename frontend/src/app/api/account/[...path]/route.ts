import { NextRequest, NextResponse } from "next/server";
import { authServerFetch, bodyError, rejectUnsafeMutation, requestBody, upstreamResponse } from "@/lib/server/auth-server";

export const runtime = "nodejs";
type Context = { params: Promise<{ path: string[] }> };
const rules = [
  [/^users\/me$/, ["GET", "PATCH", "DELETE"]],
  [/^users\/me\/(avatar|password|password\/set|deactivate|oauth\/google)$/, ["POST"]],
  [/^users\/me\/oauth\/google$/, ["DELETE"]],
  [/^users\/me\/sessions$/, ["GET"]],
  [/^users\/me\/sessions\/[a-f0-9-]{36}$/, ["DELETE"]],
  [/^auth\/email-verification\/request$/, ["POST"]],
  [/^auth\/me\/locale$/, ["PATCH"]],
] as const;
async function handle(request: NextRequest, context: Context) {
  const path = (await context.params).path.join("/");
  if (!rules.some(([pattern, methods]) => pattern.test(path) && (methods as readonly string[]).includes(request.method))) {
    return NextResponse.json({ detail: "경로를 찾을 수 없습니다." }, { status: 404 });
  }
  const rejection = rejectUnsafeMutation(request);
  if (rejection) return rejection;
  const headers = new Headers();
  const auth = request.headers.get("authorization");
  if (auth) headers.set("authorization", auth);
  const contentType = request.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  let body: ArrayBuffer | undefined;
  try { if (request.method !== "GET") body = await requestBody(request); }
  catch (error) { return bodyError(error); }
  return upstreamResponse(await authServerFetch(`/${path}`, { method: request.method, headers, body }));
}
export const GET = handle;
export const POST = handle;
export const PATCH = handle;
export const DELETE = handle;
