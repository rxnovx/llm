import { NextRequest, NextResponse } from 'next/server';
import { authServerFetch, requestBody, upstreamResponse } from '@/lib/server/auth-server';

export const runtime = 'nodejs';
export const dynamic = 'force-dynamic';

async function gateway(request: NextRequest, context: { params: Promise<{ path: string[] }> }) {
  const path = (await context.params).path.join('/');
  const allowed = request.method === 'GET' && path === 'models'
    || request.method === 'POST' && path === 'chat/completions';
  if (!allowed) return NextResponse.json({ error: { message: '경로를 찾을 수 없습니다.' } }, { status: 404 });
  const headers = new Headers({ 'content-type': 'application/json' });
  const authorization = request.headers.get('authorization');
  if (authorization) headers.set('authorization', authorization);
  let body: ArrayBuffer | undefined;
  try {
    if (request.method === 'POST') body = await requestBody(request, 256 * 1024);
  } catch {
    return NextResponse.json({ error: { message: '요청 크기 제한을 초과했습니다.' } }, { status: 413 });
  }
  const upstream = await authServerFetch('/v1/' + path,
    { method: request.method, headers, body, signal: request.signal },
    Number(process.env.LLM_REQUEST_TIMEOUT_MS ?? 900000));
  if (upstream.ok && upstream.headers.get('content-type')?.startsWith('text/event-stream')) {
    return new Response(upstream.body, { headers: {
      'Content-Type': 'text/event-stream; charset=utf-8',
      'Cache-Control': 'no-store, no-transform',
      'X-Accel-Buffering': 'no',
    } });
  }
  return upstreamResponse(upstream);
}

export const GET = gateway;
export const POST = gateway;
