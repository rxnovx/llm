import assert from "node:assert/strict";
import { test } from "node:test";
import { readChatStream } from "../src/lib/chat-stream.ts";

function stream(bytes, size = bytes.length) {
  let position = 0;
  return new ReadableStream({ pull(controller) {
    if (position >= bytes.length) { controller.close(); return; }
    controller.enqueue(bytes.slice(position, position + size)); position += size;
  } });
}
const encoder = new TextEncoder();
const events = 'event: start\ndata: {"conversation":{}}\n\nevent: delta\ndata: {"content":"한글 👋"}\n\nevent: done\ndata: {"message":{}}\n\n';

test("SSE preserves UTF-8 characters and complete events across one-byte chunks", async () => {
  const received = [];
  await readChatStream(stream(encoder.encode(events), 1), (event) => received.push(event));
  assert.deepEqual(received.map((item) => item.event), ["start", "delta", "done"]);
  assert.equal(received[1].data.content, "한글 👋");
});
test("SSE handles CRLF split between chunks and ignores comments", async () => {
  const received = [];
  await readChatStream(stream(encoder.encode(`: keepalive\n\n${events}`.replaceAll("\n", "\r\n")), 3), (event) => received.push(event));
  assert.equal(received.length, 3);
});
test("a truncated stream preserves delivered text and reports interruption", async () => {
  const received = [];
  await assert.rejects(() => readChatStream(stream(encoder.encode('event: delta\ndata: {"content":"부분 답변"}\n\n')),
    (event) => received.push(event)), /중단/);
  assert.equal(received[0].data.content, "부분 답변");
});
test("an error or cancellation is an explicit terminal event", async () => {
  for (const name of ["error", "cancelled"]) {
    const received = [];
    await readChatStream(stream(encoder.encode(`event: ${name}\ndata: {"message":{}}\n\n`)), (event) => received.push(event));
    assert.equal(received[0].event, name);
  }
});
test("malformed JSON and oversized unfinished events fail and release the reader", async () => {
  for (const value of ['event: delta\ndata: nope\n\n', `data: ${"x".repeat(1024 * 1024 + 1)}`]) {
    const source = stream(encoder.encode(value));
    await assert.rejects(() => readChatStream(source, () => {}));
    assert.equal(source.locked, false);
  }
});

test("queue and estimated context events survive split UTF-8 and remain nonterminal", async () => {
  const received = [];
  const value = 'event: queue\ndata: {"status":"queued","position":2,"request_id":"one"}\n\n'
    + 'event: context\ndata: {"is_estimate":true,"summary":"이전 요약"}\n\n' + events;
  await readChatStream(stream(encoder.encode(value), 2), (event) => received.push(event));
  assert.deepEqual(received.map((item) => item.event), ["queue", "context", "start", "delta", "done"]);
  assert.equal(received[1].data.summary, "이전 요약");
  await assert.rejects(() => readChatStream(stream(encoder.encode(value.replace(events, ""))), () => {}), /중단/);
});

test("invalid queue positions and states fail and release the reader", async () => {
  for (const data of [{status:"queued",position:-1}, {status:"queued",position:1.5}, {status:"unknown",position:0}]) {
    const source = stream(encoder.encode(`event: queue\ndata: ${JSON.stringify(data)}\n\n`));
    await assert.rejects(() => readChatStream(source, () => {}), /대기열/);
    assert.equal(source.locked, false);
  }
});

test("app reading and bounded source events arrive before a terminal answer", async () => {
  const source = { id:"GL1",path:"app/main.py",url:"https://gitlab.example/team/app/-/blob/abc/app/main.py",commit:"abc",start_line:1,end_line:4 };
  const value = 'event: tool\ndata: {"app":"gitlab","status":"reading","detail":"파일을 읽는 중"}\n\n'
    + `event: sources\ndata: ${JSON.stringify({sources:[source]})}\n\n`+events;
  const received=[];
  await readChatStream(stream(encoder.encode(value),1),(event) => received.push(event));
  assert.deepEqual(received.map((item) => item.event),["tool","sources","start","delta","done"]);
  assert.equal(received[1].data.sources[0].path,"app/main.py");
  for (const invalid of [null,[{...source,url:"javascript:alert(1)"}],Array(6).fill(source)]) {
    await assert.rejects(() => readChatStream(stream(encoder.encode(`event: sources\ndata: ${JSON.stringify({sources:invalid})}\n\n`)),() => {}),/출처/);
  }
});
