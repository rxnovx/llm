import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";

const code = ts.transpileModule(readFileSync("src/lib/authenticated-fetch.ts", "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;
function environment(replies, refreshed = "new-token") {
  const calls = [];
  let refreshes = 0, expirations = 0;
  const exports = {};
  const context = vm.createContext({ exports, Headers,FormData,
    require: () => ({ getAccessToken: () => "old-token", refreshAccessToken: async () => { refreshes++; return refreshed; },
      notifyAuthExpired: () => { expirations++; } }),
    fetch: async (url, init) => { calls.push({ url, ...init }); return replies.shift(); },
  });
  vm.runInContext(code, context);
  return { ...exports, calls, refreshes: () => refreshes, expirations: () => expirations };
}
test("authenticated API requests preserve headers and use the current bearer and CSRF header", async () => {
  const env = environment([Response.json({ ok: true })]);
  await env.authenticatedFetch("/api/admin/users", { headers: new Headers({ "x-extra": "value", authorization: "untrusted" }) });
  assert.equal(env.calls[0].headers.get("authorization"), "Bearer old-token");
  assert.equal(env.calls[0].headers.get("x-llm-account"), "1");
  assert.equal(env.calls[0].headers.get("x-extra"), "value");
  assert.equal(env.calls[0].credentials, "same-origin");
});
test('file uploads leave multipart boundaries to the browser while retaining bearer and CSRF headers',async()=>{
  const env=environment([Response.json({ok:true})]);const form=new FormData();form.set('file',new Blob(['text']),'a.txt');
  await env.authenticatedFetch('/api/knowledge/documents',{method:'POST',body:form});
  assert.equal(env.calls[0].headers.has('content-type'),false);
  assert.equal(env.calls[0].headers.get('authorization'),'Bearer old-token');
  assert.equal(env.calls[0].headers.get('x-llm-account'),'1');assert.equal(env.calls[0].body,form);
});
test("401 refreshes once and retries with the new bearer", async () => {
  const env = environment([Response.json({}, { status: 401 }), Response.json({ ok: true })]);
  assert.equal((await env.authenticatedFetch("/api/admin/overview")).status, 200);
  assert.equal(env.refreshes(), 1); assert.equal(env.expirations(), 0);
  assert.equal(env.calls[1].headers.get("authorization"), "Bearer new-token");
});
test("an unrecoverable 401 expires the session while 403 preserves its status for the caller", async () => {
  const failed = environment([Response.json({ detail: "expired" }, { status: 401 })], null);
  await assert.rejects(() => failed.authenticatedFetch("/api/admin/overview"), (error) => error.status === 401);
  assert.equal(failed.expirations(), 1);
  const denied = environment([Response.json({ detail: "운영자 권한이 필요합니다." }, { status: 403 })]);
  await assert.rejects(() => denied.authenticatedFetch("/api/admin/overview"), (error) => error.status === 403);
  assert.equal(denied.refreshes(), 0); assert.equal(denied.expirations(), 0);
});
test("outages do not expire sessions and interrupted response bodies are never automatically replayed", async () => {
  const outage = environment([Response.json({ detail: "unavailable" }, { status: 503 })]);
  await assert.rejects(() => outage.authenticatedFetch("/api/admin/overview"), (error) => error.status === 503);
  assert.equal(outage.refreshes(), 0); assert.equal(outage.expirations(), 0);
  const source = new ReadableStream({ start(controller) { controller.error(new Error("stream broke")); } });
  const stream = environment([new Response(source)]);
  const response = await stream.authenticatedFetch("/api/chat/conversations/example/messages", { method: "POST" });
  await assert.rejects(() => response.text(), /stream broke/);
  assert.equal(stream.calls.length, 1);
});
