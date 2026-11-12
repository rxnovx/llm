import assert from "node:assert/strict";
import { test } from "node:test";
import {
  conversationLocation, conversationLoginUrl, conversationPath, getSafeNextPath, loginReturnUrl,
  modalContentScope, updateConversationLocation, workspaceConversationId, workspaceHref, workspaceModal,
  workspaceReturnUrl, WORKSPACE_LOCATION_EVENT,
} from "../src/lib/workspace-navigation.ts";

const id = "12345678-1234-1234-1234-123456789abc";
const chat = `/c/${id}`;
const returnTo = `returnTo=${encodeURIComponent(chat)}`;
const location = (pathname, search = "") => ({ pathname, search });
const url = (href) => new URL(href, "https://llm.invalid");

test("the workspace has a new-chat route and a canonical conversation route", () => {
  assert.equal(conversationPath(id.toUpperCase()), chat);
  assert.equal(conversationPath("bad"), "/");
  assert.equal(workspaceConversationId(location(chat, "?chat=ignored")), id);
  assert.equal(workspaceConversationId(location("/", `?${returnTo}`)), null);
  for (const path of ["/", chat]) assert.equal(workspaceModal(path), null);
  for (const path of ["/login", "/user/signup", "/user/verify-email", "/user/reset-password", "/user/recover"]) {
    assert.equal(workspaceModal(path).kind, "auth");
  }
  for (const path of ["/user/settings", "/user/profile", "/user/profile/edit"]) {
    assert.equal(workspaceModal(path).kind, "settings");
  }
  assert.equal(workspaceModal("/admin").kind, "admin");
  assert.equal(workspaceModal("/c/invalid").kind, "other");
});

test("settings and auth links carry the conversation path while excluding source action tokens", () => {
  const from = location(chat, "?token=private-token&email=private%40example.com&action=email-change");
  for (const href of ["/user/settings", "/admin", "/login", "/user/signup"]) {
    const target = workspaceHref(href, from);
    assert.equal(url(target).searchParams.get("returnTo"), chat);
    assert.doesNotMatch(target, /private|token|email-change|chat=/);
  }
  assert.equal(workspaceHref("/", location("/admin", `?${returnTo}`)), chat);
  assert.equal(workspaceHref("/", from), "/");
  assert.equal(workspaceHref("https://help.example.com/guide", from), "https://help.example.com/guide");
});

test("legacy modal links resolve to the same conversation and are rewritten on navigation", () => {
  const legacy = location("/user/settings", `?chat=${id}`);
  assert.equal(workspaceConversationId(legacy), id);
  assert.equal(workspaceReturnUrl(legacy.search), chat);
  assert.equal(workspaceHref("/user/settings/apps", legacy), `/user/settings/apps?${returnTo}`);
  assert.equal(getSafeNextPath(`/user/settings/apps?chat=${id}`), `/user/settings/apps?${returnTo}`);
});

test("an explicit chat login target survives signup and resolves to its canonical path", () => {
  const login = url(conversationLoginUrl(id));
  assert.equal(login.searchParams.get("next"), chat);
  assert.equal(login.searchParams.has("returnTo"), false);
  const signup = url(workspaceHref("/user/signup", login));
  assert.equal(signup.searchParams.get("next"), chat);
  assert.equal(loginReturnUrl(signup.search), chat);
  assert.equal(loginReturnUrl(`?next=${encodeURIComponent(chat + "?token=discard")}`), chat);
});

test("ordinary login opens a fresh chat and a protected modal can request a return", () => {
  const search = `?chat=${id}`;
  assert.equal(loginReturnUrl(search), "/");
  assert.equal(loginReturnUrl(`?${returnTo}`), "/");
  assert.equal(loginReturnUrl(`${search}&next=${encodeURIComponent(`/?chat=${id}`)}`), "/");
  assert.equal(loginReturnUrl(`${search}&next=%2Fuser%2Fsettings%2Fapps`), `/user/settings/apps?${returnTo}`);
  const signup = url(workspaceHref("/user/signup", location("/login", `?${returnTo}&next=%2Fadmin&token=secret`)));
  assert.equal(signup.searchParams.get("next"), "/admin");
  assert.equal(signup.searchParams.get("returnTo"), chat);
  assert.equal(loginReturnUrl(signup.search), `/admin?${returnTo}`);
});

test("modal closing and login reject destinations outside the allowed routes", () => {
  assert.equal(workspaceReturnUrl(`?${returnTo}&next=https://evil.example&token=secret`), chat);
  for (const next of ["https://evil.example", "//evil.example", "/admin\\evil", "/admin\n",
    "/user/recover?token=secret", "/c/bad", "/missing/../admin", chat + "/extra"]) {
    const encoded = encodeURIComponent(next);
    assert.equal(url(workspaceHref("/login", location("/login", `?next=${encoded}`))).searchParams.has("next"), false);
    assert.equal(loginReturnUrl(`?next=${encoded}`), "/");
    assert.equal(workspaceReturnUrl(`?returnTo=${encoded}`), "/");
  }
  assert.equal(workspaceReturnUrl(`?returnTo=//evil.example&chat=${id}`), "/");
});

test("conversation promotion updates the address while keeping an open modal and its own query", () => {
  assert.equal(conversationLocation(location("/"), id), chat);
  assert.equal(conversationLocation(location(chat), null), "/");
  const target = url(conversationLocation(location("/user/settings", "?section=security"), id));
  assert.equal(target.pathname, "/user/settings");
  assert.equal(target.searchParams.get("section"), "security");
  assert.equal(target.searchParams.get("returnTo"), chat);
  assert.equal(conversationLocation(location("/user/verify-email", `?token=keep&chat=${id}`), null),
    "/user/verify-email?token=keep");
});

test("explicit navigation adds history and first creation replaces the current entry", () => {
  const previous = globalThis.window;
  const events = [];
  globalThis.window = {
    location: location("/"),
    history: {
      pushState(_state, _unused, href) { events.push(["push", href]); },
      replaceState(_state, _unused, href) { events.push(["replace", href]); },
    },
    dispatchEvent(event) { events.push(event.type); },
  };
  try {
    updateConversationLocation(id, "push");
    updateConversationLocation(id, "replace");
    updateConversationLocation(null, "replace");
    assert.deepEqual(events, [["push", chat], WORKSPACE_LOCATION_EVENT, ["replace", chat], WORKSPACE_LOCATION_EVENT]);
  } finally {
    if (previous === undefined) delete globalThis.window;
    else globalThis.window = previous;
  }
});

test("protected modal content resets on account change while confirmation results survive logout", () => {
  for (const kind of ["settings", "admin"]) assert.notEqual(modalContentScope(kind, "alice"), modalContentScope(kind, "bob"));
  assert.equal(modalContentScope("auth", "alice"), modalContentScope("auth", "guest"));
});
