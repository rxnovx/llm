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
test("canvas opens between text chunks and closes its range without finishing the reply", async () => {
  const value = 'event: delta\ndata: {"content":"📋 설명"}\n\n'
    + 'event: canvas\ndata: {"start":4,"end":null}\n\n'
    + 'event: delta\ndata: {"content":"# 초안"}\n\n'
    + 'event: canvas\ndata: {"start":4,"end":8}\n\n' + events;
  const received = [];
  await readChatStream(stream(encoder.encode(value), 1), event => received.push(event));
  assert.deepEqual(received.filter(event => event.event === "canvas").map(event => event.data),
    [{ start: 4, end: null }, { start: 4, end: 8 }]);
  await assert.rejects(() => readChatStream(stream(encoder.encode(value.replace('"end":8', '"end":3'))), () => {}), /캔버스/);
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
  for (const data of [{status:"queued",position:-1}, {status:"queued",position:1.5}, {status:"unknown",position:0}, {status:"queued",position:1,kind:"unknown"}]) {
    const source = stream(encoder.encode(`event: queue\ndata: ${JSON.stringify(data)}\n\n`));
    await assert.rejects(() => readChatStream(source, () => {}), /대기열/);
    assert.equal(source.locked, false);
  }
});

test("image admission preserves queue kind through the running event", async () => {
  const queued = { status: "queued", position: 2, request_id: "image-request", kind: "image" };
  const running = { ...queued, status: "running", position: 0 };
  const value = [queued, running].map(data => `event: queue\ndata: ${JSON.stringify(data)}\n\n`).join("") + events;
  const received = [];
  await readChatStream(stream(encoder.encode(value), 5), event => received.push(event));
  assert.deepEqual(received.filter(event => event.event === "queue").map(event => event.data), [queued, running]);
});

test("app reading and bounded source events arrive before a terminal answer", async () => {
  const source = { id:"GL1",path:"app/main.py",url:"https://gitlab.example/team/app/-/blob/abc/app/main.py",commit:"abc",start_line:1,end_line:4 };
  const value = 'event: tool\ndata: {"app":"gitlab","status":"reading","detail":"파일을 읽는 중"}\n\n'
    + `event: sources\ndata: ${JSON.stringify({sources:[source]})}\n\n`+events;
  const received=[];
  await readChatStream(stream(encoder.encode(value),1),(event) => received.push(event));
  assert.deepEqual(received.map((item) => item.event),["tool","sources","start","delta","done"]);
  assert.equal(received[1].data.sources[0].path,"app/main.py");
  for (const invalid of [null,[{...source,url:"javascript:alert(1)"}],Array(31).fill(source)]) {
    await assert.rejects(() => readChatStream(stream(encoder.encode(`event: sources\ndata: ${JSON.stringify({sources:invalid})}\n\n`)),() => {}),/출처/);
  }
});

test("workspace and tool activity events remain distinct from answer text", async()=>{
  const value='event: workspace\ndata: {"id":"workspace-one","status":"ready"}\n\n'
    +'event: activity\ndata: {"name":"repo_map","status":"complete","detail":"파일 조회","step":1}\n\n'+events;
  const received=[];
  await readChatStream(stream(encoder.encode(value),2),event=>received.push(event));
  assert.deepEqual(received.map(event=>event.event),['workspace','activity','start','delta','done']);
  assert.equal(received[1].data.detail,'파일 조회');
});

test('knowledge sources preserve document version and location without an external URL',async()=>{
  const events=[];
  const source={id:'KS1',app:'knowledge',document_id:'document',chunk_id:'chunk',filename:'매뉴얼.pdf',location:'3쪽',sha256:'abc',revision:2};
  const stream=new ReadableStream({start(controller){controller.enqueue(new TextEncoder().encode(`event: sources\ndata: ${JSON.stringify({sources:[source]})}\n\nevent: done\ndata: {}\n\n`));controller.close();}});
  await readChatStream(stream,event=>events.push(event));assert.deepEqual(events[0].data.sources,[source]);
});

test('automatic workspace selection and final routing survive split SSE data',async()=>{
  const selection={app:'gitlab',connection_id:'account',project_id:7,ref:'main',paths:[],mode:'workspace',workspace_id:'workspace'};
  const value='event: workspace\ndata: '+JSON.stringify({id:'workspace',status:'ready',automatic:true,selection})+'\n\n'
    +'event: activity\ndata: '+JSON.stringify({name:'evidence_review',status:'running',detail:'근거 점검',step:0})+'\n\n'
    +'event: done\ndata: '+JSON.stringify({message:{routing:{automatic:true,automatic_project:true,route:'gitlab',status:'complete'}}})+'\n\n';
  const received=[];
  await readChatStream(stream(encoder.encode(value),1),event=>received.push(event));
  assert.deepEqual(received[0].data.selection,selection);
  assert.equal(received[0].data.automatic,true);
  assert.equal(received.at(-1).data.message.routing.automatic_project,true);
});

test('MCP evidence, actual artifact and coverage survive byte boundaries',async()=>{
  const source={id:'MS1',app:'mcp',evidence_id:'evidence',server:'문서 검색',tool:'lookup',sha256:'hash'};
  const file={id:'file',name:'결과.pdf',format:'pdf',size:1234};
  const value=`event: sources\ndata: ${JSON.stringify({sources:[source]})}\n\nevent: artifact\ndata: ${JSON.stringify(file)}\n\nevent: coverage\ndata: {"excerpts":1,"app_results":1}\n\n`+events;
  const received=[];await readChatStream(stream(encoder.encode(value),1),e=>received.push(e));
  assert.deepEqual(received.slice(0,3).map(e=>e.event),['sources','artifact','coverage']);assert.equal(received[1].data.name,'결과.pdf');
  await assert.rejects(()=>readChatStream(stream(encoder.encode('event: artifact\ndata: {"id":"fake","name":"bad","format":"pdf","size":-0.5}\n\n')),()=>{}),/파일/);
});
