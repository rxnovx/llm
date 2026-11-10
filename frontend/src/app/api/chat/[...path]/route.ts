import { NextRequest, NextResponse } from "next/server";
import { authServerFetch, bodyError, rejectUnsafeMutation, requestBody, upstreamResponse } from "@/lib/server/auth-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";
const uuid = "[a-f0-9-]{36}";
const rules = [
  [/^config$/, ["GET"]],
  [/^connections\/[a-zA-Z0-9_-]{1,64}\/check$/, ["POST"]],
  [/^conversations$/, ["GET", "POST"]],
  [new RegExp(`^conversations/${uuid}$`), ["GET", "PATCH", "DELETE"]],
  [new RegExp(`^conversations/${uuid}/(messages|cancel)$`), ["POST"]],
  [new RegExp(`^conversations/${uuid}/context/preview$`), ["POST"]],
  [new RegExp(`^conversations/${uuid}/context$`), ["PATCH"]],
] as const;

async function handle(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const path = (await context.params).path.join("/");
  if (!rules.some(([pattern, methods]) => pattern.test(path) && (methods as readonly string[]).includes(request.method))) {
    return NextResponse.json({ detail: "경로를 찾을 수 없습니다." }, { status: 404 });
  }
  const rejection = rejectUnsafeMutation(request);
  if (rejection) return rejection;
  const headers = new Headers({ "content-type": "application/json" });
  const authorization = request.headers.get("authorization");
  if (authorization) headers.set("authorization", authorization);
  let body: ArrayBuffer | undefined;
  try { if (request.method !== "GET") body = await requestBody(request); }
  catch (error) { return bodyError(error); }
  const upstream = await authServerFetch(`/chat/${path}${request.nextUrl.search}`, { method: request.method, headers, body, signal: request.signal },
    Number(process.env.LLM_REQUEST_TIMEOUT_MS ?? 180000));
  if (upstream.ok && upstream.headers.get("content-type")?.startsWith("text/event-stream")) {
    return new Response(upstream.body, { status: upstream.status, headers: {
      "Content-Type": "text/event-stream; charset=utf-8", "Cache-Control": "no-store, no-transform", "X-Accel-Buffering": "no",
    } });
  }
  return upstreamResponse(upstream);
}
export const GET = handle;
export const POST = handle;
export const PATCH = handle;
export const DELETE = handle;
