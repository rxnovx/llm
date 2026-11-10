import { NextRequest, NextResponse } from "next/server";
import { authServerFetch } from "@/lib/server/auth-server";

export async function GET(_: NextRequest, context: { params: Promise<{ owner: string; name: string }> }) {
  const { owner, name } = await context.params;
  if (!/^[a-f0-9]{24}$/.test(owner) || !/^[a-f0-9]{32}\.png$/.test(name)) return new NextResponse(null, { status: 404 });
  const upstream = await authServerFetch(`/users/avatars/${owner}/${name}`);
  if (!upstream.ok) return new NextResponse(null, { status: upstream.status });
  return new NextResponse(upstream.body, { headers: { "Content-Type": "image/png", "X-Content-Type-Options": "nosniff",
    "Cache-Control": "public, max-age=31536000, immutable" } });
}
