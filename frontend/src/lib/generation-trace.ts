import type { ChatMessage, GenerationTrace, ToolActivity } from "../types/chat";

export const MAX_REASONING_CHARS = 64_000;
export const MAX_TRACE_STEPS = 240;

/** 서버가 부여한 실행 ID로 갱신하고, 예전 기록은 단계와 도구명으로 묶습니다. */
export function mergeTraceActivities(items: ToolActivity[], incoming: ToolActivity): ToolActivity[] {
  const identity = (item: ToolActivity) => item.id || item.operation_id || `${item.step}:${item.name}`;
  const at = items.findIndex(item => identity(item) === identity(incoming));
  if (at < 0) return [...items, incoming].slice(-MAX_TRACE_STEPS);
  const previous = items[at];
  // 지연된 실행 시작 이벤트가 이미 저장된 완료 결과를 되돌리지 않게 합니다.
  if (previous.status !== "running" && incoming.status === "running") return items;
  const next = [...items];
  next[at] = { ...previous, ...incoming };
  return next;
}

export function traceActivities(message: Pick<ChatMessage, "generation_trace" | "tools">): ToolActivity[] {
  return (message.generation_trace?.steps ?? message.tools ?? []).reduce(mergeTraceActivities, []);
}

export function appendReasoning(trace: GenerationTrace | undefined, content: string, truncated = false): GenerationTrace {
  const text = (trace?.reasoning ?? "") + content;
  return { ...trace, steps: trace?.steps ?? [], reasoning: text.slice(0, MAX_REASONING_CHARS),
    reasoning_truncated: Boolean(trace?.reasoning_truncated || truncated || text.length > MAX_REASONING_CHARS) };
}

export function elapsedLabel(milliseconds: number): string {
  if (milliseconds < 1000) return `${Math.max(0, Math.round(milliseconds))}ms`;
  if (milliseconds < 60_000) return `${(milliseconds / 1000).toFixed(1)}초`;
  return `${Math.floor(milliseconds / 60_000)}분 ${Math.floor(milliseconds % 60_000 / 1000)}초`;
}

/** 토큰별 렌더링 대신 짧은 간격으로 두 출력 채널을 함께 반영합니다. */
export function createGenerationBuffer(onFlush: (value: { answer: string; reasoning: string; truncated: boolean }) => void,
  interval = 80, schedule = setTimeout, unschedule = clearTimeout) {
  let answer = "", reasoning = "", truncated = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const flush = () => {
    if (timer !== undefined) unschedule(timer);
    timer = undefined;
    if (!answer && !reasoning && !truncated) return;
    const value = { answer, reasoning, truncated };
    answer = ""; reasoning = ""; truncated = false;
    onFlush(value);
  };
  return {
    push(channel: "answer" | "reasoning", content: string, isTruncated = false) {
      if (channel === "answer") answer += content;
      else {
        const remaining = MAX_REASONING_CHARS - reasoning.length;
        reasoning += content.slice(0, Math.max(0, remaining));
        truncated ||= content.length > remaining;
      }
      truncated ||= isTruncated;
      if (timer === undefined) timer = schedule(flush, interval);
    },
    flush,
    dispose() { if (timer !== undefined) unschedule(timer); timer = undefined; answer = ""; reasoning = ""; truncated = false; },
  };
}
