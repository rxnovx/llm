import { NextResponse, type NextRequest } from "next/server";
import { conversationPath, normalizeConversationId } from "@/lib/workspace-navigation";

export function proxy(request: NextRequest) {
  const ids = request.nextUrl.searchParams.getAll("chat");
  const id = ids.length === 1 ? normalizeConversationId(ids[0]) : null;
  if (!id) return NextResponse.next();
  return NextResponse.redirect(new URL(conversationPath(id), request.url));
}

export const config = {
  matcher: [{ source: "/", has: [{ type: "query", key: "chat" }] }],
};
