import assert from "node:assert/strict";
import { after, test } from "node:test";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";
import { appendReasoning, createGenerationBuffer, mergeTraceActivities, traceActivities, MAX_REASONING_CHARS } from "../src/lib/generation-trace.ts";
import { readChatStream } from "../src/lib/chat-stream.ts";

const cache = path.resolve("node_modules/.cache");
mkdirSync(cache, { recursive: true });
const directory = mkdtempSync(path.join(cache, "generation-trace-"));
for (const source of ["src/components/chat/GenerationTrace.tsx", "src/components/chat/GitlabWorkProgress.tsx",
  "src/components/chat/activity-labels.ts", "src/lib/generation-trace.ts"]) {
  const { outputText } = ts.transpileModule(readFileSync(source, "utf8"), {
    compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
  });
  writeFileSync(path.join(directory, path.basename(source).replace(/\.tsx?$/, ".mjs")), outputText
    .replaceAll('"@/lib/generation-trace"', '"./generation-trace.mjs"')
    .replaceAll('"./activity-labels"', '"./activity-labels.mjs"')
    .replaceAll('"./GitlabWorkProgress"', '"./GitlabWorkProgress.mjs"'));
}
const { GenerationTrace, hasGenerationTrace } = await import(pathToFileURL(path.join(directory, "GenerationTrace.mjs")).href);
after(() => rmSync(directory, { recursive: true, force: true }));
const base = { id: "answer", role: "assistant", content: "최종 답변", status: "pending" };
const activity = (status, patch = {}) => ({ id: "operation-1", name: "code_search", step: 1, status,
  detail: "src/login.ts:12", started_at_ms: 2000, ...patch });
const render = message => renderToStaticMarkup(createElement(GenerationTrace, { message }));

test("ordinary empty trace stays absent; actual tool activity opens without model reasoning", () => {
  assert.equal(hasGenerationTrace(base), false);
  assert.equal(render(base), "");
  const live = render({ ...base, tools: [activity("running")] });
  assert.match(live, /aria-expanded="true"/);
  assert.match(live, /코드 검색/);
  assert.match(live, /src\/login.ts:12/);
  assert.doesNotMatch(live, /모델이 제공한 추론/);
});

test("saved reasoning and actual timings remain separated from answer and collapse at completion", () => {
  const message = { ...base, status: "complete", generation_trace: { reasoning: "공개된 모델 추론 <script>bad</script>", reasoning_truncated: false,
    steps: [activity("complete", { finished_at_ms: 2432, duration_ms: 432 })],
    metrics: { duration_ms: 2700, first_token_ms: 340, input_tokens: 23, output_tokens: 9, tokens_per_second: 4.1,
      is_estimate: false, timing_source: "client_observed", token_source: "server_reported" } } };
  const collapsed = render(message);
  assert.match(collapsed, /aria-expanded="false"/);
  assert.match(collapsed, /완료 · 1\/1단계 · 2.7초/);
  assert.doesNotMatch(collapsed, /최종 답변|bad|<script/);
  const expanded = render({ ...message, status: "pending" });
  assert.match(expanded, /모델이 제공한 추론/);
  assert.match(expanded, /&lt;script&gt;bad&lt;\/script&gt;/);
  assert.match(expanded, /340ms/);
  assert.match(expanded, /4.1 토큰\/초/);
  assert.match(expanded, /실제 측정/);
  assert.doesNotMatch(expanded, /최종 답변/);
});

test("unreported tokens never become fabricated speed and estimated counts stay labeled", () => {
  const renderMetrics = metrics => render({ ...base, generation_trace: { reasoning: "", reasoning_truncated: false, steps: [], metrics } });
  const unavailable = renderMetrics({ duration_ms: 250, first_token_ms: 100, input_tokens: null, output_tokens: null,
    tokens_per_second: null, token_source: "unavailable", timing_source: "client_observed", is_estimate: true });
  assert.match(unavailable, /서버 토큰 수 미제공/);
  assert.doesNotMatch(unavailable, /토큰\/초/);
  const estimated = renderMetrics({ duration_ms: 250, input_tokens: 40, output_tokens: 10,
    token_source: "utf8_upper_bound", timing_source: "client_observed", is_estimate: true });
  assert.match(estimated, /입력 · 추정/);
  assert.match(estimated, /토큰 수 상한 추정/);
  const reported = renderMetrics({ duration_ms: 250, first_token_ms: 100, server_first_token_ms: 80,
    input_tokens: 20, output_tokens: 4, tokens_per_second: 16, token_source: "server_reported", timing_source: "server_reported", is_estimate: false });
  assert.match(reported, /첫 토큰 · 앱/);
  assert.match(reported, /첫 토큰 · 서버/);
  assert.match(reported, /생성 속도 · 서버/);
  assert.doesNotMatch(reported, /추정/);
});

test("trace snapshots coalesce legacy activities and retain independent repeated operations", () => {
  const first = activity("running");
  const complete = activity("complete", { detail: "실제 파일을 확인했습니다." });
  const other = activity("running", { id: "operation-2" });
  let merged = [first].reduce(mergeTraceActivities, []);
  merged = mergeTraceActivities(merged, complete);
  merged = mergeTraceActivities(merged, first);
  merged = mergeTraceActivities(merged, other);
  assert.equal(merged.length, 2);
  assert.equal(merged[0].status, "complete");
  const legacyStart = { ...first }, legacyDone = { ...complete };
  delete legacyStart.id; delete legacyDone.id;
  assert.equal(traceActivities({ tools: [legacyStart, legacyDone] }).length, 1);
  assert.equal(traceActivities({ generation_trace: { steps: [complete] }, tools: [first] })[0].status, "complete");
});

test("reasoning has a bounded independent buffer and does not change answer text", () => {
  let trace = appendReasoning(undefined, "근거 ");
  trace = appendReasoning(trace, "x".repeat(MAX_REASONING_CHARS));
  assert.equal(trace.reasoning.length, MAX_REASONING_CHARS);
  assert.equal(trace.reasoning_truncated, true);
  assert.deepEqual(trace.steps, []);
});

test("one thousand tokens flush in a batch and disposing drops stale navigation updates", () => {
  const callbacks = new Map();
  let sequence = 0;
  const output = [];
  const buffer = createGenerationBuffer(value => output.push(value), 80,
    callback => { const id = ++sequence; callbacks.set(id, callback); return id; }, id => callbacks.delete(id));
  for (let index = 0; index < 1000; index++) buffer.push(index % 2 ? "answer" : "reasoning", "한");
  assert.equal(callbacks.size, 1);
  assert.equal(output.length, 0);
  buffer.flush();
  assert.equal(output.length, 1);
  assert.equal(output[0].answer.length, 500);
  assert.equal(output[0].reasoning.length, 500);
  assert.equal(callbacks.size, 0);
  buffer.push("answer", "이전 대화");
  buffer.dispose();
  assert.equal(callbacks.size, 0);
  buffer.flush();
  assert.equal(output.length, 1);
});

test("reasoning, phase updates, cancellation and metrics survive one-byte Korean SSE", async () => {
  const events = [
    ["reasoning_delta", { content: "서버 공개 추론", call_id: "call-one", connection_id: "local", model: "gemma" }],
    ["activity", activity("complete", { finished_at_ms: 2222, duration_ms: 222 })],
    ["generation_metrics", { metrics: { input_tokens: 2, output_tokens: 3, tokens_per_second: 3.25, is_estimate: false,
      timing_source: "server_reported", token_source: "server_reported", server_first_token_ms: 45 } }],
    ["activity", activity("cancelled")],
    ["delta", { content: "별도 답변" }],
    ["cancelled", { message: { ...base, status: "cancelled" } }],
  ];
  const bytes = new TextEncoder().encode(events.map(([name, data]) => `event: ${name}\ndata: ${JSON.stringify(data)}\n\n`).join(""));
  let index = 0;
  const stream = new ReadableStream({ pull(controller) { if (index < bytes.length) controller.enqueue(bytes.slice(index, ++index)); else controller.close(); } });
  const received = [];
  await readChatStream(stream, event => received.push(event));
  assert.deepEqual(received.map(event => event.event), events.map(([name]) => name));
  assert.equal(received[0].data.content, "서버 공개 추론");
  assert.equal(received[4].data.content, "별도 답변");
});

test("invalid reasoning and timing payloads fail without accepting invented metric formats", async () => {
  for (const [name, data] of [
    ["reasoning_delta", { content: 5 }], ["reasoning_delta", { content: "x".repeat(MAX_REASONING_CHARS + 1) }],
    ["generation_metrics", { metrics: { first_token_ms: -1 } }], ["generation_metrics", { metrics: { input_tokens: "unknown" } }],
    ["generation_metrics", { metrics: { is_estimate: "false" } }], ["generation_metrics", { metrics: [] }],
    ["generation_metrics", { metrics: { token_source: "imagined" } }], ["generation_metrics", { metrics: { timing_source: "imagined" } }],
    ["activity", activity("running", { started_at_ms: -4 })],
  ]) {
    const stream = new ReadableStream({ start(controller) { controller.enqueue(new TextEncoder().encode(
      `event: ${name}\ndata: ${JSON.stringify(data)}\n\nevent: done\ndata: {}\n\n`)); controller.close(); } });
    await assert.rejects(() => readChatStream(stream, () => {}));
    assert.equal(stream.locked, false);
  }
});
