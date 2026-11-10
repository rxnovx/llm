import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { randomUUID } from "node:crypto";
import { test } from "node:test";
import vm from "node:vm";
import ts from "typescript";

// Run the actual browser token module in isolated tabs, with a shared Web Locks
// manager and BroadcastChannel transport. No React state or token logic is copied.
const code = ts.transpileModule(readFileSync("src/lib/auth.ts", "utf8"), {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText;

const tick = () => new Promise((resolve) => setImmediate(resolve));
function environment() {
  const storage = new Map();
  const locks = new Map();
  const channels = new Map();
  const messages = [];
  class Channel extends EventTarget {
    constructor(name) { super(); this.name = name; const list = channels.get(name) ?? []; list.push(this); channels.set(name, list); }
    postMessage(data) {
      messages.push(data);
      for (const other of channels.get(this.name) ?? []) {
        if (other !== this) queueMicrotask(() => other.dispatchEvent(new MessageEvent("message", { data })));
      }
    }
  }
  function tab(fetch) {
    const window = new EventTarget();
    Object.assign(window, { BroadcastChannel: Channel, setTimeout, clearTimeout,
      localStorage: { getItem: (key) => storage.get(key) ?? null,
        setItem: (key, value) => storage.set(key, value), removeItem: (key) => storage.delete(key) } });
    const exports = {};
    const context = vm.createContext({ exports, window, fetch, BroadcastChannel: Channel,
      navigator: { locks: { request: (name, work) => {
        const result = (locks.get(name) ?? Promise.resolve()).then(work);
        locks.set(name, result.then(() => undefined, () => undefined));
        return result;
      } } }, crypto: { randomUUID }, Event, EventTarget, MessageEvent, DOMException,
      AbortController, setTimeout, clearTimeout, atob, console });
    vm.runInContext(code, context);
    return { api: exports, window };
  }
  return { tab, messages, storage };
}

test("concurrent refresh requests share one network operation and send the CSRF header", async () => {
  let calls = 0;
  let finish;
  const { api } = environment().tab((_url, init) => {
    calls++;
    assert.equal(init.headers["x-llm-account"], "1");
    return new Promise((resolve) => { finish = resolve; });
  });
  const first = api.refreshAccessToken();
  const second = api.refreshAccessToken();
  await tick();
  assert.equal(calls, 1);
  finish(Response.json({ access_token: "access-one", user: { subject: "alice" } }));
  assert.deepEqual(await Promise.all([first, second]), ["access-one", "access-one"]);
  assert.equal(api.consumeRefreshedUser().subject, "alice");
});

test("a late refresh response cannot restore a cleared account", async () => {
  let finish;
  const { api } = environment().tab(() => new Promise((resolve) => { finish = resolve; }));
  const pending = api.refreshAccessToken();
  await tick();
  api.clearAccessToken(true);
  finish(Response.json({ access_token: "late-access", user: { subject: "alice" } }));
  assert.equal(await pending, null);
  assert.equal(api.getAccessToken(), null);
});

test("a 503 response does not broadcast session expiration", async () => {
  const env = environment();
  const { api } = env.tab(async () => Response.json({ detail: "unavailable" }, { status: 503 }));
  api.setAccessToken("existing-access", false);
  assert.equal(await api.refreshAccessToken(), null);
  assert.equal(api.getAccessToken(), "existing-access");
  assert.equal(env.messages.some((m) => m.type === "expired"), false);
});

test("refresh and logout in different tabs are serialized and both tabs stay logged out", async () => {
  const env = environment();
  const order = [];
  let finish;
  const first = env.tab(() => { order.push("refresh"); return new Promise((resolve) => { finish = resolve; }); });
  const second = env.tab(async () => { throw new Error("No second refresh expected"); });
  const refreshing = first.api.refreshAccessToken();
  await tick();
  const loggingOut = second.api.withAuthSessionLock(async () => {
    order.push("logout"); second.api.clearAccessToken(true);
  });
  await tick();
  assert.deepEqual(order, ["refresh"]);
  finish(Response.json({ access_token: "rotated-access", user: { subject: "alice" } }));
  await refreshing; await loggingOut; await tick();
  assert.deepEqual(order, ["refresh", "logout"]);
  assert.equal(first.api.getAccessToken(), null);
  assert.equal(second.api.getAccessToken(), null);
  assert.equal([...env.storage.values()].some((v) => v.includes("rotated-access")), false);
});

test("a rejected login mutation releases the common lock for retry", async () => {
  const { api } = environment().tab(async () => Response.json({ access_token: "ok" }));
  await assert.rejects(api.withAuthSessionLock(async () => { throw new Error("login failed"); }), /login failed/);
  assert.equal(await api.refreshAccessToken(), "ok");
});
