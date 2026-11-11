import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { createHash } from "node:crypto";
import { after, before, test } from "node:test";

const origin = "http://127.0.0.1:3099";
const upstreamOrigin = "http://127.0.0.1:8099";
const refresh = "test-refresh-token-for-proxy-contract-only";
const oauthState = "test-oauth-state-"+"x".repeat(32);
const oauthKey = createHash("sha256").update(oauthState).digest("hex").slice(0,16);
let mode = "tokens";
let received = null;
let server;
let child;
let serverOutput = "";
const csrf = { "x-llm-account": "1", origin };
const cookie = { ...csrf, cookie: `llm_accounts_refresh=${refresh}` };

before(async () => {
  server = createServer(async (request, response) => {
    const chunks = [];
    for await (const chunk of request) chunks.push(chunk);
    received = { path: request.url, method: request.method,
      headers: request.headers, body: Buffer.concat(chunks).toString() };
    if (mode === "chat-stream") {
      response.setHeader("content-type", "text/event-stream");
      response.write('event: start\ndata: {"ready":true}\n\n');
      await new Promise((resolve) => setTimeout(resolve, 250));
      response.write('event: delta\ndata: {"content":"실시간 답변"}\n\n');
      await new Promise((resolve) => setTimeout(resolve, 50));
      response.end('event: done\ndata: {}\n\n'); return;
    }
    response.setHeader("content-type", "application/json");
    if (mode === "timeout") { await new Promise((resolve) => setTimeout(resolve, 350)); }
    if (mode === "invalid-json") { response.end("not-json"); return; }
    if (mode === "invalid-token") { response.end(JSON.stringify({ user: {} })); return; }
    if (mode === "apps-start") { response.end(JSON.stringify({authorize_url:`https://gitlab.example/oauth/authorize?state=${oauthState}`,cookie_key:oauthKey})); return; }
    if (mode === "apps-complete") { response.end(JSON.stringify({connection:{id:"connection-one",username:"alice"},return_path:"/user/settings/apps",popup:true})); return; }
    if (["401", "403", "429", "503"].includes(mode)) {
      response.statusCode = Number(mode);
      if (mode === "429") response.setHeader("retry-after", "60");
      response.end(JSON.stringify({ detail: "simulated upstream error" })); return;
    }
    if (request.url === "/auth/logout") { response.statusCode = 204; response.end(); return; }
    response.end(JSON.stringify({ access_token: "test-access-token", refresh_token: refresh,
      token_type: "bearer", expires_in: 900, user: { subject: "test-user" } }));
  });
  await new Promise((resolve) => server.listen(8099, "127.0.0.1", resolve));
  child = spawn(process.execPath, [".next/standalone/server.js"], {
    env: { ...process.env, PORT: "3099", HOSTNAME: "127.0.0.1", APP_URL: "http://localhost:3099",
      APP_ALLOWED_ORIGINS: origin,
      AUTH_SERVER_URL: upstreamOrigin, AUTH_SERVER_TIMEOUT_MS: "150", AUTH_COOKIE_SECURE: "true" },
    stdio: ["ignore", "pipe", "pipe"],
  });
  for (const stream of [child.stdout, child.stderr]) stream.on("data", (data) => { serverOutput = (serverOutput + data).slice(-3000); });
  for (let i = 0; i < 100; i++) {
    try { if ((await fetch(`${origin}/health`)).ok) return; } catch { /* Wait for Next startup. */ }
    if (child.exitCode !== null) throw new Error(`Next failed to start: ${serverOutput}`);
    await new Promise((resolve) => setTimeout(resolve, 50));
  }
  throw new Error(`Next startup timed out: ${serverOutput}`);
});

after(async () => {
  if (child && child.exitCode === null) {
    const closed = new Promise((resolve) => child.once("exit", resolve));
    child.kill("SIGTERM"); await closed;
  }
  if (server) { server.closeAllConnections(); await new Promise((resolve) => server.close(resolve)); }
});

test("login keeps the refresh token in an HttpOnly cookie and converts the form", async () => {
  mode = "tokens";
  const response = await fetch(`${origin}/api/auth/login`, { method: "POST", headers: { ...csrf, "content-type": "application/json" },
    body: JSON.stringify({ loginId: " alice ", password: "some-password" }) });
  assert.equal(response.status, 200);
  const body = await response.json();
  assert.equal(body.refresh_token, undefined);
  assert.equal(body.access_token, "test-access-token");
  const header = response.headers.get("set-cookie");
  assert.match(header, /HttpOnly/i); assert.match(header, /SameSite=lax/i);
  assert.match(header, /Secure/i); assert.match(header, /Path=\/api\/auth/i);
  assert.equal(new URLSearchParams(received.body).get("username"), "alice");
});

test("refresh uses the cookie, not an attacker-supplied body token", async () => {
  mode = "tokens";
  const response = await fetch(`${origin}/api/auth/refresh`, { method: "POST", headers: cookie,
    body: JSON.stringify({ refresh_token: "attacker-supplied" }) });
  assert.equal(response.status, 200);
  assert.equal(JSON.parse(received.body).refresh_token, refresh);
  assert.equal(received.headers.cookie, undefined);
});

test("an invalid refresh clears its cookie", async () => {
  mode = "401";
  const response = await fetch(`${origin}/api/auth/refresh`, { method: "POST", headers: cookie });
  assert.equal(response.status, 401);
  assert.match(response.headers.get("set-cookie"), /Max-Age=0/i);
});

test("an outage or rate limit preserves the refresh cookie and retry information", async () => {
  for (const status of ["429", "503"]) {
    mode = status;
    const response = await fetch(`${origin}/api/auth/refresh`, { method: "POST", headers: cookie });
    assert.equal(response.status, Number(status));
    assert.equal(response.headers.get("set-cookie"), null);
    if (status === "429") assert.equal(response.headers.get("retry-after"), "60");
  }
});

test("logout reports upstream failure and clears the cookie only after revocation", async () => {
  mode = "503";
  let response = await fetch(`${origin}/api/auth/logout`, { method: "POST", headers: cookie });
  assert.equal(response.status, 503);
  assert.equal(response.headers.get("set-cookie"), null);
  mode = "tokens";
  response = await fetch(`${origin}/api/auth/logout`, { method: "POST", headers: cookie, body: '{"all_sessions":true}' });
  assert.equal(response.status, 204);
  assert.match(response.headers.get("set-cookie"), /Max-Age=0/i);
  assert.equal(JSON.parse(received.body).all_sessions, true);
});

test("an upstream timeout returns 504 without destroying the cookie", async () => {
  mode = "timeout";
  const response = await fetch(`${origin}/api/auth/refresh`, { method: "POST", headers: cookie });
  assert.equal(response.status, 504);
  assert.equal(response.headers.get("set-cookie"), null);
});

test("explicitly allowed localhost and IP origins can both log in", async () => {
  mode = "tokens";
  for (const allowedOrigin of ["http://localhost:3099", origin]) {
    const response = await fetch(`${origin}/api/auth/login`, { method: "POST",
      headers: { ...csrf, origin: allowedOrigin, "content-type": "application/json" },
      body: JSON.stringify({ loginId: "alice", password: "some-password" }) });
    assert.equal(response.status, 200, `${allowedOrigin}: ${await response.text()}`);
  }
});

test("mutations require an allowed origin and a non-simple header", async () => {
  mode = "tokens";
  for (const headers of [{}, { ...csrf, origin: "https://other.example" },
    { ...csrf, origin: "http://127.0.0.1:3100" }, { ...csrf, origin: "https://127.0.0.1:3099" },
    { ...csrf, origin: "http://localhost.evil.example:3099" }, { ...csrf, origin: "null" },
    { ...csrf, origin: "https://other.example", host: "other.example", "x-forwarded-host": "other.example" },
    { ...csrf, "sec-fetch-site": "cross-site" }]) {
    assert.equal((await fetch(`${origin}/api/auth/logout`, { method: "POST", headers })).status, 403);
  }
});

test("proxy routes cannot reach the other application's API", async () => {
  assert.equal((await fetch(`${origin}/api/account/notes`)).status, 404);
  assert.equal((await fetch(`${origin}/api/auth/admin`)).status, 404);
  assert.equal((await fetch(`${origin}/api/auth/config`, { method: "POST", headers: csrf })).status, 404);
});

test("GitLab MR resolution forwards the caller's authenticated selection and applies the origin check", async () => {
  mode = "tokens";
  const url = `${origin}/api/apps/gitlab/merge-request/resolve`;
  const body = JSON.stringify({ reference: "https://gitlab.example/team/app/-/merge_requests/27" });
  const headers = { ...csrf, "content-type": "application/json", authorization: "Bearer test-access-token" };
  assert.equal((await fetch(url, { method: "POST", headers: { ...headers, origin: "https://other.example" }, body })).status, 403);
  assert.equal((await fetch(url, { method: "POST", headers, body })).status, 200);
  assert.equal(received.path, "/apps/gitlab/merge-request/resolve");
  assert.equal(received.headers.authorization, "Bearer test-access-token");
  assert.equal(received.body, body);
  assert.equal((await fetch(url)).status, 404);
});

test("oversized request bodies are rejected before reaching the API", async () => {
  const response = await fetch(`${origin}/api/account/users/me/avatar`, { method: "POST", headers: csrf,
    body: Buffer.alloc(6 * 1024 * 1024 + 1) });
  assert.equal(response.status, 413);
});

test("malformed upstream tokens return 502 without creating or deleting cookies", async () => {
  for (const value of ["invalid-token", "invalid-json"]) {
    mode = value;
    const response = await fetch(`${origin}/api/auth/refresh`, { method: "POST", headers: cookie });
    assert.equal(response.status, 502);
    assert.equal(response.headers.get("set-cookie"), null);
  }
});

test("the current user endpoint forwards its bearer token", async () => {
  mode = "tokens";
  await fetch(`${origin}/api/auth/me`, { headers: { authorization: "Bearer account-access-token" } });
  assert.equal(received.headers.authorization, "Bearer account-access-token");
});

test("chat SSE reaches the client before generation finishes and forwards only the bearer", async () => {
  mode = "chat-stream";
  const response = await fetch(`${origin}/api/chat/conversations/12345678-1234-1234-1234-123456789abc/messages`, {
    method: "POST", headers: { ...csrf, authorization: "Bearer chat-user", cookie: `llm_accounts_refresh=${refresh}` }, body: "{}" });
  assert.equal(response.status, 200);
  assert.match(response.headers.get("content-type"), /text\/event-stream/);
  assert.equal(response.headers.get("x-accel-buffering"), "no");
  const reader = response.body.getReader();
  const first = new TextDecoder().decode((await reader.read()).value);
  assert.match(first, /event: start/);
  assert.doesNotMatch(first, /event: done/);
  let rest = "";
  while (true) { const part = await reader.read(); if (part.done) break; rest += new TextDecoder().decode(part.value); }
  assert.match(rest, /실시간 답변/);
  assert.match(rest, /event: done/);
  assert.equal(received.headers.authorization, "Bearer chat-user");
  assert.equal(received.headers.cookie, undefined);
});

test("chat routes keep origin validation, path boundaries and pagination query", async () => {
  mode = "tokens";
  assert.equal((await fetch(`${origin}/api/chat/conversations`, { method: "POST", body: "{}" })).status, 403);
  assert.equal((await fetch(`${origin}/api/chat/conversations`, { method: "POST", headers: { ...csrf, origin: "https://other.example" }, body: "{}" })).status, 403);
  assert.equal((await fetch(`${origin}/api/chat/admin`)).status, 404);
  await fetch(`${origin}/api/chat/conversations?offset=50&limit=25`);
  assert.equal(received.path, "/chat/conversations?offset=50&limit=25");
});

test("operator proxy preserves permission denials and forwards metadata queries without cookies", async () => {
  mode = "403";
  assert.equal((await fetch(`${origin}/api/admin/overview`, { headers: { authorization: "Bearer member" } })).status, 403);
  mode = "tokens";
  await fetch(`${origin}/api/admin/users?q=alice&offset=20`, { headers: { authorization: "Bearer operator", cookie: "private-cookie=value" } });
  assert.equal(received.path, "/admin/users?q=alice&offset=20");
  assert.equal(received.headers.authorization, "Bearer operator");
  assert.equal(received.headers.cookie, undefined);
});

test("operator mutations still require same-origin CSRF protection and cannot reach other paths", async () => {
  mode = "tokens";
  const endpoint = `${origin}/api/admin/connections/demo/check`;
  for (const headers of [{}, { ...csrf, origin: "https://other.example" }]) {
    assert.equal((await fetch(endpoint, { method: "POST", headers })).status, 403);
  }
  assert.equal((await fetch(endpoint, { method: "POST", headers: csrf })).status, 200);
  assert.equal(received.path, "/admin/connections/demo/check");
  assert.equal((await fetch(`${origin}/api/admin/database`)).status, 404);
  assert.equal((await fetch(`${origin}/api/admin/users`, { method: "POST", headers: csrf })).status, 404);
});

test("connection edits forward JSON and authorization, enforce CSRF and bound the body", async () => {
  mode = "tokens";
  for (const [method,path] of [["POST","connections"],["PATCH","connections/office"]]) {
    const endpoint = `${origin}/api/admin/${path}`;
    assert.equal((await fetch(endpoint,{method,body:"{}"})).status,403);
    assert.equal((await fetch(endpoint,{method,headers:{...csrf,origin:"https://elsewhere.example"},body:"{}"})).status,403);
    const body = JSON.stringify({id:"office",name:"내부 모델",revision:2});
    assert.equal((await fetch(endpoint,{method,headers:{...csrf,authorization:"Bearer operator",cookie:"private=value"},body})).status,200);
    assert.equal(received.method,method); assert.equal(received.body,body);
    assert.equal(received.path,`/admin/${path}`); assert.equal(received.headers.cookie,undefined);
    assert.equal(received.headers.authorization,"Bearer operator");
    assert.equal(received.headers["content-type"],"application/json");
  }
  const before = received;
  assert.equal((await fetch(`${origin}/api/admin/connections`,{method:"POST",headers:csrf,body:"x".repeat(256*1024+1)})).status,413);
  assert.equal(received,before);
});

test("context preview and summary edits keep chat route and CSRF boundaries", async () => {
  mode = "tokens";
  const path = "conversations/12345678-1234-1234-1234-123456789abc/context";
  for (const [method,suffix] of [["POST","/preview"],["PATCH",""]]) {
    const body = JSON.stringify({summary:"이전 대화",revision:1});
    assert.equal((await fetch(`${origin}/api/chat/${path}${suffix}`,{method,body})).status,403);
    assert.equal((await fetch(`${origin}/api/chat/${path}${suffix}`,{method,headers:{...csrf,authorization:"Bearer member"},body})).status,200);
    assert.equal(received.path,`/chat/${path}${suffix}`); assert.equal(received.body,body);
  }
  assert.equal((await fetch(`${origin}/api/chat/${path}/unknown`,{method:"POST",headers:csrf})).status,404);
});

test("GitLab OAuth BFF binds independent HttpOnly state cookies and hides backend browser metadata", async () => {
  mode="apps-start";
  const headers={...csrf,origin:"http://localhost:3099",authorization:"Bearer alice",cookie:"private=value","x-oauth-browser":"attacker-nonce"};
  const response=await fetch(`${origin}/api/apps/gitlab/office/authorize`,{method:"POST",headers,body:'{"popup":true}'});
  assert.equal(response.status,200);
  const data=await response.json(),setCookie=response.headers.get("set-cookie");
  assert.equal(data.cookie_key,undefined);
  assert.match(setCookie,new RegExp(`llm_gitlab_oauth_${oauthKey}=`));
  assert.match(setCookie,/HttpOnly/i);assert.match(setCookie,/Secure/i);assert.match(setCookie,/SameSite=lax/i);
  assert.match(setCookie,/Path=\/api\/apps\/gitlab\/oauth\/complete/);
  assert.equal(received.headers.cookie,undefined);
  assert.notEqual(received.headers["x-oauth-browser"],"attacker-nonce");
  const browser=received.headers["x-oauth-browser"];
  mode="apps-complete";
  const complete=await fetch(`${origin}/api/apps/gitlab/oauth/complete`,{method:"POST",headers:{...headers,cookie:setCookie.split(";",1)[0]},body:JSON.stringify({state:oauthState,code:"one-use-code"})});
  assert.equal(complete.status,200);
  assert.equal(received.headers["x-oauth-browser"],browser);
  assert.equal(received.headers.cookie,undefined);
  assert.match(complete.headers.get("set-cookie"),/Max-Age=0/i);
});

test("app routes enforce CSRF, callback origin, bounded bodies and path boundaries", async () => {
  mode="tokens";
  const path="/api/apps/gitlab/office/authorize";
  assert.equal((await fetch(origin+path,{method:"POST",body:"{}"})).status,403);
  assert.equal((await fetch(origin+path,{method:"POST",headers:csrf,body:"{}"})).status,400);
  assert.equal((await fetch(origin+path,{method:"POST",headers:{...csrf,origin:"http://localhost:3099"},body:"x".repeat(65537)})).status,413);
  assert.equal((await fetch(`${origin}/api/apps/admin`)).status,404);
  const endpoint="/api/apps/connections/12345678-1234-1234-1234-123456789abc/projects/7/tree?ref=feature%2Fmain&path=app";
  assert.equal((await fetch(origin+endpoint,{headers:{authorization:"Bearer alice",cookie:"private=value"}})).status,200);
  assert.equal(received.path,endpoint.replace("/api/","/"));
  assert.equal(received.headers.cookie,undefined);
  assert.equal(received.headers.authorization,"Bearer alice");
});

test("workspace proxy isolates internal credentials and validates mutation origins and body size",async()=>{
  mode='tokens';
  const id='12345678-1234-1234-1234-123456789abc';
  assert.equal((await fetch(`${origin}/api/workspaces/internal/checkout`,{method:'POST',headers:csrf,body:'{}'})).status,404);
  assert.equal((await fetch(`${origin}/api/workspaces/${id}/tools`,{method:'POST',body:'{}'})).status,403);
  assert.equal((await fetch(`${origin}/api/workspaces/${id}/tools`,{method:'POST',headers:csrf,body:'x'.repeat(65537)})).status,413);
  await fetch(`${origin}/api/workspaces?conversation_id=${id}`,{headers:{authorization:'Bearer owner',cookie:'private=value'}});
  assert.equal(received.path,`/workspaces?conversation_id=${id}`);assert.equal(received.headers.cookie,undefined);
  assert.equal(received.headers.authorization,'Bearer owner');
  const response=await fetch(`${origin}/api/workspaces/${id}/tools`,{method:'POST',headers:{...csrf,authorization:'Bearer owner'},body:JSON.stringify({name:'repo_map',arguments:{}})});
  assert.equal(response.status,200);assert.equal(received.path,`/workspaces/${id}/tools`);
});

test('knowledge multipart proxy preserves bytes, isolates cookies, and enforces origin and path limits',async()=>{
  mode='tokens';const id='12345678-1234-1234-1234-123456789abc';
  assert.equal((await fetch(`${origin}/api/knowledge/documents`,{method:'POST',body:'{}'})).status,403);
  assert.equal((await fetch(`${origin}/api/knowledge/internal/credentials`,{method:'POST',headers:csrf,body:'{}'})).status,404);
  const form=new FormData();form.set('conversation_id',id);form.set('file',new Blob(['문서 원문']), 'manual.md');
  const r=await fetch(`${origin}/api/knowledge/documents`,{method:'POST',headers:{...csrf,authorization:'Bearer owner',cookie:'secret=cookie'},body:form});
  assert.equal(r.status,200);assert.equal(received.path,'/knowledge/documents');
  assert.match(received.headers['content-type'],/^multipart\/form-data; boundary=/);
  assert.match(received.body,/문서 원문/);assert.equal(received.headers.cookie,undefined);
  assert.equal(received.headers.authorization,'Bearer owner');
  await fetch(`${origin}/api/knowledge/documents/${id}/chunks/${id}`,{headers:{authorization:'Bearer owner'}});
  assert.equal(received.path,`/knowledge/documents/${id}/chunks/${id}`);
});

test('assistant proxy protects registry mutations and preserves upstream authorization failures',async()=>{
  mode='403';const endpoint='/api/assistant/mcp/servers/browser';
  assert.equal((await fetch(origin+endpoint,{method:'PUT',body:'{}'})).status,403);
  const response=await fetch(origin+endpoint,{method:'PUT',headers:{...csrf,authorization:'Bearer user'},body:'{}'});
  assert.equal(response.status,403);assert.equal(received.path,'/assistant/mcp/servers/browser');
  assert.equal(received.headers.authorization,'Bearer user');
  assert.equal((await fetch(origin+'/api/assistant/admin/secrets',{headers:csrf})).status,404);
  assert.equal((await fetch(origin+'/api/assistant/preferences',{method:'PUT',headers:csrf,body:'x'.repeat(65537)})).status,413);
});
