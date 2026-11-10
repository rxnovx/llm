import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { createServer } from "node:http";
import { after, before, test } from "node:test";

const origin = "http://127.0.0.1:3099";
const upstreamOrigin = "http://127.0.0.1:8099";
const refresh = "test-refresh-token-for-proxy-contract-only";
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
    response.setHeader("content-type", "application/json");
    if (mode === "timeout") { await new Promise((resolve) => setTimeout(resolve, 350)); }
    if (mode === "invalid-json") { response.end("not-json"); return; }
    if (mode === "invalid-token") { response.end(JSON.stringify({ user: {} })); return; }
    if (["401", "429", "503"].includes(mode)) {
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
