import type { ChatMessage } from "@/types/chat";

export function splitCanvas(message: Pick<ChatMessage, "content" | "routing">) {
  const selection = message.routing?.canvas;
  if (!selection) return { before: "", body: message.content, after: "" };
  // The server sends Unicode code point offsets, including for emoji.
  const chars = Array.from(message.content);
  const end = selection.end ?? chars.length;
  return {
    before: chars.slice(0, selection.start).join(""),
    body: chars.slice(selection.start, end).join(""),
    after: chars.slice(end).join(""),
  };
}
