import type { ChatEvent } from "../types/chat";

export function sourceWebUrl(value: unknown) {
  if (typeof value !== "string" || !/^https?:\/\//i.test(value)) return undefined;
  try {
    const url = new URL(value);
    return url.username || url.password ? undefined : url.href;
  } catch {
    return undefined;
  }
}

function validSource(value: unknown) {
  if (!value || typeof value !== "object") return false;
  const source = value as Record<string, unknown>;
  if (typeof source.id !== "string") return false;
  switch (source.app) {
    case "knowledge":
      return typeof source.document_id === "string" && typeof source.chunk_id === "string"
        && typeof source.filename === "string" && typeof source.location === "string"
        && typeof source.sha256 === "string" && Number.isSafeInteger(source.revision);
    case "mcp":
      return typeof source.evidence_id === "string" && typeof source.server === "string"
        && typeof source.tool === "string" && typeof source.sha256 === "string";
    case "web":
      return typeof source.title === "string" && Boolean(sourceWebUrl(source.url))
        && (source.excerpt === undefined || typeof source.excerpt === "string");
    case "chat_history":
      return typeof source.title === "string" && typeof source.conversation_id === "string"
        && typeof source.message_id === "string" && (source.excerpt === undefined || typeof source.excerpt === "string");
    case "table_analysis":
      return typeof source.title === "string" && typeof source.excerpt === "string" && Array.isArray(source.documents)
        && source.documents.length > 0 && source.documents.length <= 2 && source.documents.every(document => document
          && typeof document.document_id === "string" && typeof document.filename === "string"
          && typeof document.sha256 === "string" && Number.isSafeInteger(document.revision)
          && (document.sheet == null || typeof document.sheet === "string"));
    case "gitlab":
    case undefined:
      return typeof source.path === "string" && Boolean(sourceWebUrl(source.url)) && typeof source.commit === "string"
        && Number.isSafeInteger(source.start_line) && Number.isSafeInteger(source.end_line);
    default:
      return false;
  }
}

/** Parse SSE by lines, preserving UTF-8 and CRLF across arbitrary network chunks. */
export async function readChatStream(stream: ReadableStream<Uint8Array>, onEvent: (event: ChatEvent) => void) {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let buffer = "", name = "", data: string[] = [], finished = false;
  try {
    while (true) {
      const { value, done } = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, { stream: true });
      if (buffer.length + data.join("\n").length > 1024 * 1024) throw new Error("응답 크기가 너무 큽니다.");
      let index;
      while ((index = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, index).replace(/\r$/, "");
        buffer = buffer.slice(index + 1);
        if (line.startsWith("event:")) name = line.slice(6).trim();
        else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
        else if (!line) {
          if (data.length && ["start", "queue", "context", "tool", "sources", "activity", "workspace", "artifact", "coverage", "canvas", "delta", "done", "error", "cancelled"].includes(name)) {
            const payload = JSON.parse(data.join("\n"));
            if (!payload || typeof payload !== "object") throw new Error("응답 형식이 올바르지 않습니다.");
            if (name === "delta" && typeof payload.content !== "string") throw new Error("응답 형식이 올바르지 않습니다.");
            if (name === "canvas" && (!Number.isSafeInteger(payload.start) || payload.start < 0 || payload.start > 200_000
              || (payload.end !== null && (!Number.isSafeInteger(payload.end) || payload.end <= payload.start || payload.end > 200_000)))) {
              throw new Error("캔버스 응답 형식이 올바르지 않습니다.");
            }
            if (name === "tool" && (payload.app !== "gitlab" || payload.status !== "reading" || typeof payload.detail !== "string")) throw new Error("앱 응답 형식이 올바르지 않습니다.");
            if (name === "activity" && (typeof payload.name !== "string" || typeof payload.detail !== "string" || !["running","complete","error"].includes(payload.status) || !Number.isSafeInteger(payload.step))) throw new Error("도구 진행 응답이 올바르지 않습니다.");
            if (name === "workspace" && (typeof payload.id !== "string" || typeof payload.status !== "string")) throw new Error("작업공간 응답이 올바르지 않습니다.");
            if (name === "sources" && (!Array.isArray(payload.sources) || payload.sources.length > 30 || !payload.sources.every(validSource))) throw new Error("출처 응답 형식이 올바르지 않습니다.");
            if (name === 'artifact' && (typeof payload.id!=='string'||typeof payload.name!=='string'||typeof payload.format!=='string'||(!Number.isSafeInteger(payload.size)||payload.size<0||payload.size>10*1024*1024)))throw new Error('결과 파일 형식이 올바르지 않습니다.');
            if (name === "queue" && (!["queued", "running"].includes(payload.status) || !Number.isSafeInteger(payload.position) || payload.position < 0
              || (payload.kind !== undefined && !["image", "model"].includes(payload.kind)))) throw new Error("대기열 응답 형식이 올바르지 않습니다.");
            onEvent({ event: name, data: payload } as ChatEvent);
            if (["done", "error", "cancelled"].includes(name)) finished = true;
          }
          name = ""; data = [];
        }
      }
      if (done) break;
    }
    if (!finished) throw new Error("응답 연결이 중단되었습니다. 다시 시도해 주세요.");
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
