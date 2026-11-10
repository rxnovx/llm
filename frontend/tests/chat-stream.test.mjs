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
