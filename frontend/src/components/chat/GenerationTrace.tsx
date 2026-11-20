"use client";

import { memo, useEffect, useId, useRef, useState } from "react";
import type { ChatMessage, GenerationMetrics } from "@/types/chat";
import { elapsedLabel, traceActivities } from "@/lib/generation-trace";
import { activityLabel } from "./activity-labels";
import { gitlabWebUrl } from "./GitlabWorkProgress";
import type { MergeRequestSummary } from "@/lib/app-connections";

export function hasGenerationTrace(message: ChatMessage) {
  return Boolean(message.generation_trace?.reasoning || message.generation_trace?.steps?.length
    || message.tools?.length || Object.values(message.generation_trace?.metrics ?? {}).some(value => typeof value === "number"));
}

function MetricSummary({ metrics }: { metrics?: GenerationMetrics }) {
  if (!metrics) return null;
  const entries = [
    metrics.duration_ms != null && ["전체 시간", elapsedLabel(metrics.duration_ms)],
    metrics.first_token_ms != null && ["첫 토큰 · 앱", elapsedLabel(metrics.first_token_ms)],
    metrics.server_first_token_ms != null && ["첫 토큰 · 서버", elapsedLabel(metrics.server_first_token_ms)],
    metrics.input_tokens != null && [metrics.is_estimate ? "입력 · 추정" : "입력", `${metrics.input_tokens.toLocaleString()} 토큰`],
    metrics.output_tokens != null && [metrics.is_estimate ? "출력 · 추정" : "출력", `${metrics.output_tokens.toLocaleString()} 토큰`],
    metrics.tokens_per_second != null && [["provider", "server_reported"].includes(metrics.timing_source ?? "") ? "생성 속도 · 서버" : "처리 속도 · 앱", `${metrics.tokens_per_second.toFixed(1)} 토큰/초`],
  ].filter((value): value is string[] => Boolean(value));
  if (!entries.length) return null;
  return <div className="cw-generation-metrics"><span>{metrics.token_source === "unavailable" ? "실제 측정 · 서버 토큰 수 미제공" : metrics.is_estimate ? "토큰 수 추정 포함" : "실제 측정"}
    {metrics.duration_ms != null ? " · 앱 경과 시간" : ""}
    {["provider", "server_reported"].includes(metrics.token_source ?? "") ? " · 서버 토큰 수" : metrics.token_source === "utf8_upper_bound" ? " · 토큰 수 상한 추정" : ""}</span>
    <dl>{entries.map(([name, value]) => <div key={name}><dt>{name}</dt><dd>{value}</dd></div>)}</dl>
  </div>;
}

/** 실제 실행 기록과 모델이 제공한 공개 추론을 별도 채널로 표시합니다. */
export const GenerationTrace = memo(function GenerationTrace({ message, onFollowup }: {
  message: ChatMessage; onFollowup?: (url: string) => void;
}) {
  const panelId = useId();
  const [expanded, setExpanded] = useState<boolean | null>(null);
  const [now, setNow] = useState(0);
  const reasoningBox = useRef<HTMLPreElement>(null);
  const followReasoning = useRef(true);
  const pending = message.status === "pending";
  const open = expanded ?? pending;
  const tools = traceActivities(message);
  const reasoning = message.generation_trace?.reasoning ?? "";
  const current = tools.findLast(tool => tool.status === "running");
  const activeName = pending && current ? activityLabel(current.name, "도구 실행") : "";
  const firstStart = tools.reduce((earliest, item) => item.started_at_ms ? Math.min(earliest, item.started_at_ms) : earliest, Infinity);
  const duration = message.generation_trace?.metrics?.duration_ms
    ?? (!pending && Number.isFinite(firstStart) ? Math.max(...tools.map(item => item.finished_at_ms ?? item.started_at_ms ?? firstStart)) - firstStart : null);
  useEffect(() => {
    if (!pending || !open) return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [pending, open]);
  useEffect(() => {
    if (followReasoning.current && reasoningBox.current) reasoningBox.current.scrollTop = reasoningBox.current.scrollHeight;
  }, [reasoning.length, open]);
  if (!hasGenerationTrace(message)) return null;
  const overview = tools.findLast(tool => tool.name === "gitlab_mr_overview" && tool.status === "complete");
  let mr: MergeRequestSummary | undefined;
  if (overview) { try { mr = JSON.parse(overview.detail) as MergeRequestSummary; } catch { /* 잘못된 JSON은 원문 기록에서 확인합니다. */ } }
  const mrUrl = gitlabWebUrl(mr?.url);
  const complete = tools.filter(tool => tool.status === "complete").length;
  const failed = tools.filter(tool => tool.status === "error").length;
  const label = pending ? activeName || (reasoning ? "모델 추론 출력 수신 중" : "처리 중")
    : message.status === "cancelled" ? "중지됨" : message.status === "error" ? "오류로 중단됨" : "완료";
  return <section className="cw-generation-trace" aria-label="AI 작업 과정" data-pending={pending}>
    <button type="button" className="cw-generation-toggle" aria-expanded={open} aria-controls={panelId}
      onClick={() => setExpanded(!open)}><span className="cw-generation-symbol" aria-hidden="true">{pending ? "◌" : message.status === "cancelled" ? "−" : failed || message.status === "error" ? "!" : "✓"}</span>
      <span><strong>AI 작업 과정</strong><span className="cw-generation-summary">{label}{!pending && tools.length ? ` · ${complete}/${tools.length}단계` : ""}
        {duration != null ? ` · ${elapsedLabel(duration)}` : ""}</span></span><span className="cw-generation-chevron" aria-hidden="true">{open ? "⌃" : "⌄"}</span></button>
    {open && <div className="cw-generation-panel" id={panelId}>
      {mrUrl && mr && <div className="cw-generation-links"><a href={mrUrl} target="_blank" rel="noreferrer">MR !{mr.iid} ↗</a>
        {mr.editable && onFollowup && <button type="button" className="cw-text-button" onClick={() => onFollowup(mrUrl)}>이 MR에서 작업</button>}</div>}
      {tools.length > 0 && <ol className="cw-generation-steps">{tools.map((tool, index) => {
        const status = tool.status === "cancelled" || tool.status === "running" && !pending ? "stopped" : tool.status;
        const elapsed = tool.duration_ms ?? (tool.started_at_ms && tool.finished_at_ms ? tool.finished_at_ms - tool.started_at_ms
          : status === "running" && tool.started_at_ms && now ? Math.max(0, now - tool.started_at_ms) : null);
        return <li key={tool.id || tool.operation_id || `${tool.step}:${tool.name}:${index}`} data-status={status}>
          <span className="cw-generation-step-mark" aria-hidden="true">{status === "complete" ? "✓" : status === "error" ? "!" : status === "stopped" ? "−" : "·"}</span>
          <details><summary><span>{activityLabel(tool.name)}</span><span className="cw-generation-step-meta">{{ running: "진행 중", complete: "완료", error: "오류", stopped: "중단" }[status]}
            {elapsed != null ? ` · ${elapsedLabel(elapsed)}` : ""}</span></summary>
            {tool.detail && <pre>{tool.detail}</pre>}
            {(tool.operation_id || tool.id) && <small>작업 ID · {tool.operation_id || tool.id}</small>}
          </details>
        </li>;
      })}</ol>}
      {reasoning && <div className="cw-generation-reasoning"><h3>모델이 제공한 추론</h3><p>모델 서버가 명시적으로 반환한 텍스트입니다.</p>
        <pre ref={reasoningBox} tabIndex={0} aria-label="모델 추론 출력" onScroll={event => {
          const box = event.currentTarget; followReasoning.current = box.scrollHeight - box.scrollTop - box.clientHeight < 48;
        }}>{reasoning}</pre>{message.generation_trace?.reasoning_truncated && <p>표시 한도에 도달하여 추론 출력의 일부를 생략했습니다.</p>}</div>}
      {(message.sources?.length ?? 0) > 0 && <p className="cw-generation-evidence">확인한 근거 {message.sources!.length}개 · 답변 아래 출처에서 파일·페이지·소스 구간을 확인할 수 있습니다.</p>}
      <MetricSummary metrics={message.generation_trace?.metrics} />
    </div>}
    {pending && <span className="cw-sr-only" role="status">{label} 진행 중</span>}
  </section>;
});
