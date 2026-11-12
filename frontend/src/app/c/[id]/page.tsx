import { notFound, redirect } from "next/navigation";
import { conversationPath, normalizeConversationId } from "@/lib/workspace-navigation";

export default async function ConversationPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const normalized = normalizeConversationId(id);
  if (!normalized) notFound();
  if (id !== normalized) redirect(conversationPath(normalized));
  // The workspace in the root layout loads this conversation for the signed-in user.
  return null;
}
