import assert from "node:assert/strict";
import { test } from "node:test";
import { conversationLocation, modalContentScope, replaceConversationLocation, workspaceHref, workspaceModal, workspaceReturnUrl,
  WORKSPACE_LOCATION_EVENT } from "../src/lib/workspace-navigation.ts";
import { getSafeNextPath } from "../src/lib/auth-router.ts";

const chat = "12345678-1234-1234-1234-123456789abc";
test("chat is the root surface; all account/auth/operator routes select a modal", () => {
  assert.equal(workspaceModal("/"), null);
  for (const path of ["/login","/user/signup","/user/verify-email","/user/reset-password","/user/recover"]) assert.equal(workspaceModal(path).kind,"auth");
  for (const path of ["/user/settings","/user/profile","/user/profile/edit"]) assert.equal(workspaceModal(path).kind,"settings");
  assert.equal(workspaceModal("/admin").kind,"admin");
  assert.equal(workspaceModal("/missing").kind,"other");
});
test("settings and auth navigation preserve the conversation without copying action tokens", () => {
  const search=`?chat=${chat}&token=private-token&email=private%40example.com&action=email-change`;
  for (const href of ["/user/settings","/admin","/login","/user/signup"]) {
    const value=workspaceHref(href,search);
    assert.equal(new URL(value,"https://llm.invalid").searchParams.get("chat"),chat);
    assert.doesNotMatch(value,/private|token|email-change/);
  }
});
test("auth-to-auth links and successful login retain a safe return target", () => {
  const search=`?chat=${chat}&next=%2Fadmin&token=secret`;
  const signup=new URL(workspaceHref("/user/signup",search),"https://llm.invalid");
  assert.equal(signup.searchParams.get("next"),"/admin");
  assert.equal(signup.searchParams.get("chat"),chat);
  const destination=workspaceHref(getSafeNextPath(signup.searchParams.get("next")),signup.search);
  assert.equal(destination,`/admin?chat=${chat}`);
  for (const next of ["https://evil.example","//evil.example","/admin\\evil","/admin\n","/user/recover?token=secret"]) {
    assert.equal(new URL(workspaceHref("/login",`?next=${encodeURIComponent(next)}`),"https://llm.invalid").searchParams.has("next"),false);
    assert.equal(getSafeNextPath(next),"/");
  }
});
test("closing a modal returns only to a validated conversation in the workspace", () => {
  assert.equal(workspaceReturnUrl(`?chat=${chat}&next=https://evil.example&token=secret`),`/?chat=${chat}`);
  for (const value of ["", "?chat=../admin", "?chat=bad", "?next=/admin"]) assert.equal(workspaceReturnUrl(value),"/");
});
test("a first reply can update its conversation URL while a settings modal stays open", () => {
  assert.equal(conversationLocation("/user/settings","?section=security",chat),`/user/settings?section=security&chat=${chat}`);
  const url=new URL(conversationLocation("/user/verify-email",`?token=keep&chat=${chat}`,null),"https://llm.invalid");
  assert.equal(url.pathname,"/user/verify-email"); assert.equal(url.searchParams.get("token"),"keep"); assert.equal(url.searchParams.has("chat"),false);
  assert.equal(conversationLocation("/","",null),"/");
});
test("location replacement publishes the URL update without navigating away from the modal", () => {
  const previous=globalThis.window; let replaced; let event;
  globalThis.window={ location:{pathname:"/user/settings",search:"?section=profile"},
    history:{replaceState(...args){replaced=args;}},dispatchEvent(value){event=value;} };
  try {
    replaceConversationLocation(chat);
    assert.deepEqual(replaced,[null,"",`/user/settings?section=profile&chat=${chat}`]);
    assert.equal(event.type,WORKSPACE_LOCATION_EVENT);
  } finally { if(previous===undefined) delete globalThis.window; else globalThis.window=previous; }
});
test("external links never receive a private conversation query", () => {
  assert.equal(workspaceHref("https://help.example.com/guide",`?chat=${chat}`),"https://help.example.com/guide");
  assert.equal(workspaceHref("/login?next=%2Fuser%2Fsettings",`?chat=${chat}&next=%2Fadmin`),`/login?next=%2Fuser%2Fsettings&chat=${chat}`);
});
test("protected modal content resets on account change while confirmation results survive logout", () => {
  for (const kind of ["settings","admin"]) assert.notEqual(modalContentScope(kind,"alice"),modalContentScope(kind,"bob"));
  assert.equal(modalContentScope("auth","alice"),modalContentScope("auth","guest"));
});
