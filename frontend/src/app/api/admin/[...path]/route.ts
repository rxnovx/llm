import { NextRequest, NextResponse } from "next/server";
import { authServerFetch, rejectUnsafeMutation, upstreamResponse } from "@/lib/server/auth-server";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

async function handle(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const path = (await context.params).path.join("/");
  const permitted = request.method === "GET" && ["overview", "requests", "users", "audit"].includes(path)
    || request.method === "POST" && /^connections\/[a-zA-Z0-9_-]{1,64}\/check$/.test(path);
  if (!permitted) return NextResponse.json({ detail: "경로를 찾을 수 없습니다." }, { status: 404 });
  const rejection = rejectUnsafeMutation(request);
  if (rejection) return rejection;
  const headers = new Headers();
  const authorization = request.headers.get("authorization");
  if (authorization) headers.set("authorization", authorization);
  return upstreamResponse(await authServerFetch(`/admin/${path}${request.nextUrl.search}`,
    { method: request.method, headers, signal: request.signal }, 15000));
}
export const GET = handle;
export const POST = handle;
