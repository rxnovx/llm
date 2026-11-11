import type { ChatEvent } from "../types/chat";

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
          if (data.length && ["start", "queue", "context", "tool", "sources", "activity", "workspace", "artifact", "coverage", "delta", "done", "error", "cancelled"].includes(name)) {
            const payload = JSON.parse(data.join("\n"));
            if (!payload || typeof payload !== "object") throw new Error("응답 형식이 올바르지 않습니다.");
            if (name === "delta" && typeof payload.content !== "string") throw new Error("응답 형식이 올바르지 않습니다.");
            if (name === "tool" && (payload.app !== "gitlab" || payload.status !== "reading" || typeof payload.detail !== "string")) throw new Error("앱 응답 형식이 올바르지 않습니다.");
            if (name === "activity" && (typeof payload.name !== "string" || typeof payload.detail !== "string" || !["running","complete","error"].includes(payload.status) || !Number.isSafeInteger(payload.step))) throw new Error("도구 진행 응답이 올바르지 않습니다.");
            if (name === "workspace" && (typeof payload.id !== "string" || typeof payload.status !== "string")) throw new Error("작업공간 응답이 올바르지 않습니다.");
            if (name === "sources" && (!Array.isArray(payload.sources) || payload.sources.length > 30 || payload.sources.some((source: Record<string, unknown>) =>
              !source || typeof source.id !== "string" || (source.app==='knowledge'
                ? typeof source.document_id!=='string'||typeof source.chunk_id!=='string'||typeof source.filename!=='string'||typeof source.location!=='string'||typeof source.sha256!=='string'||!Number.isSafeInteger(source.revision)
                : source.app==='mcp'?typeof source.evidence_id!=='string'||typeof source.server!=='string'||typeof source.tool!=='string'||typeof source.sha256!=='string'
                : typeof source.path !== "string" || typeof source.url !== "string" || !/^https?:\/\//i.test(source.url) || typeof source.commit !== "string" || !Number.isSafeInteger(source.start_line) || !Number.isSafeInteger(source.end_line))))) throw new Error("출처 응답 형식이 올바르지 않습니다.");
            if (name === 'artifact' && (typeof payload.id!=='string'||typeof payload.name!=='string'||typeof payload.format!=='string'||(!Number.isSafeInteger(payload.size)||payload.size<0||payload.size>10*1024*1024)))throw new Error('결과 파일 형식이 올바르지 않습니다.');
            if (name === "queue" && (!["queued", "running"].includes(payload.status) || !Number.isSafeInteger(payload.position) || payload.position < 0)) throw new Error("대기열 응답 형식이 올바르지 않습니다.");
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
