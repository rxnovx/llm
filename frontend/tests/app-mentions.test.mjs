import assert from "node:assert/strict";
import { test } from "node:test";
import { readFileSync } from "node:fs";
import vm from "node:vm";
import ts from "typescript";
import { appMentionAt, hasGitlabMention, insertGitlabMention, removeAppMention } from "../src/lib/app-mentions.ts";
import * as workspaceNavigation from "../src/lib/workspace-navigation.ts";

const chat = "12345678-1234-1234-1234-123456789abc";
const returnTo = `returnTo=${encodeURIComponent(`/c/${chat}`)}`;

test("app mentions match standalone prefixes at the caret and preserve surrounding text", () => {
  for (const value of ["@", "@Git", "이 코드 @gitlab"]) assert.ok(appMentionAt(value,value.length));
  for (const value of ["me@gitlab", "@github", "@gitlab/file", "문장 @gitlab 뒤"]) assert.equal(appMentionAt(value,value.length),null);
  const value = "질문 @Git 다음 내용";
  const range = appMentionAt(value,7);
  assert.deepEqual(range,{ start:3,end:7,query:"Git" });
  assert.equal(removeAppMention(value,range),"질문  다음 내용");
});

test("quoted GitLab mentions do not select an app for attachment uploads", () => {
  for (const value of ['이미지에서 “@gitlab” 문구를 찾아줘', '사진의 `@gitlab` 표기', '> @gitlab\n사진을 봐줘',
    '```md\n@gitlab\n```\n사진을 봐줘', '```md\n@gitlab', 'me@gitlab', '@gitlab.example']) {
    assert.equal(hasGitlabMention(value), false, value);
  }
  assert.equal(hasGitlabMention('“@gitlab” 예시 참고\n@GitLab 코드 검토'), true);
});

test("GitLab replaces the complete typed token and preserves the surrounding draft", () => {
  const prefix = "검토 @Git";
  const value = prefix + "LaB 다음 내용";
  const range = appMentionAt(value, prefix.length);
  assert.deepEqual(range, { start: 3, end: 10, query: "Git" });
  assert.deepEqual(insertGitlabMention(value, range), {
    value: "검토 @gitlab 다음 내용", caret: "검토 @gitlab ".length,
  });
  assert.equal(appMentionAt("검토 @gitlab_extra", "검토 @gitlab".length), null);
});

test("GitLab insertion uses the selected range and only adds the necessary whitespace", () => {
  assert.deepEqual(insertGitlabMention("", { start: 0, end: 0 }), { value: "@gitlab ", caret: 8 });
  assert.deepEqual(insertGitlabMention("앞선택뒤", { start: 1, end: 3 }), {
    value: "앞 @gitlab 뒤", caret: "앞 @gitlab ".length,
  });
  assert.deepEqual(insertGitlabMention("앞 뒤", { start: 2, end: 2 }), {
    value: "앞 @gitlab 뒤", caret: "앞 @gitlab ".length,
  });
  assert.deepEqual(insertGitlabMention("앞 \n뒤", { start: 2, end: 2 }), {
    value: "앞 @gitlab\n뒤", caret: "앞 @gitlab".length,
  });
});

function environment({ popup = null, reply = {}, failure = null } = {}) {
  const exports = {}, storage = new Map(), calls = [], events = [];
  const context = vm.createContext({ exports, URLSearchParams, Date, Event,
    sessionStorage: { getItem: (key) => storage.get(key) ?? null, setItem: (key,value) => storage.set(key,value), removeItem: (key) => storage.delete(key) },
    window: { open: () => popup, location: { pathname: "/user/settings/apps", search: `?${returnTo}`, assign: (url) => calls.push(url) }, dispatchEvent: (event) => events.push(event.type) },
    require: (name) => name === "./workspace-navigation" ? workspaceNavigation
      : { authenticatedFetch: async (url, init) => { calls.push({ url, init }); if (failure) throw failure; return Response.json(reply); } },
  });
  const code = ts.transpileModule(readFileSync("src/lib/app-connections.ts","utf8"),{ compilerOptions:{ module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022 } }).outputText;
  vm.runInContext(code,context);
  return { ...exports, calls, events };
}
test("full-navigation fallback preserves only an expiring owner/chat-scoped draft once", async () => {
  const env = environment({ reply:{ authorize_url:"https://gitlab.example/oauth/authorize" } });
  env.backupOAuthDraft("alice","작성 중인 질문","chat-a");
  assert.equal(env.takeOAuthDraft("bob","chat-a"),null);
  assert.equal(env.takeOAuthDraft("alice","chat-a"),null);
  env.backupOAuthDraft("alice","질문",chat);
  assert.equal(env.oauthReturnPath("alice"),`/user/settings/apps?${returnTo}`);
  assert.equal(env.oauthReturnPath("bob"),"/user/settings/apps");
  assert.equal(env.takeOAuthDraft("alice",chat),"질문");
  env.backupOAuthDraft("alice","작성 중인 질문","chat-a");
  assert.equal(env.takeOAuthDraft("alice","chat-b"),null);
  env.backupOAuthDraft("alice","작성 중인 질문","chat-a");
  assert.equal(env.takeOAuthDraft("alice","chat-a"),"작성 중인 질문");
  assert.equal(env.takeOAuthDraft("alice","chat-a"),null);
  await env.connectGitlab("office");
  assert.equal(JSON.parse(env.calls[0].init.body).popup,false);
  assert.equal(JSON.parse(env.calls[0].init.body).chat_id,chat);
  assert.deepEqual(env.events,["llm:oauth-leaving"]);
  assert.equal(env.calls[1],"https://gitlab.example/oauth/authorize");
});
test("a popup has no opener; a dismissed or failed popup cannot replace the main chat", async () => {
  const popup = { opener:{}, closed:false,location:{ href:"" },close() { this.closed=true; } };
  const env = environment({ popup,reply:{ authorize_url:"https://gitlab.example/oauth/authorize" } });
  await env.connectGitlab("office");
  assert.equal(popup.opener,null);
  assert.equal(popup.location.href,"https://gitlab.example/oauth/authorize");
  assert.deepEqual(env.events,[]);
  assert.equal(JSON.parse(env.calls[0].init.body).popup,true);
  await assert.rejects(() => environment({ popup:{...popup,closed:true},reply:{authorize_url:"https://gitlab.example"} }).connectGitlab("office"),/닫혔/);
  await assert.rejects(() => environment({ popup,failure:new Error("unavailable") }).connectGitlab("office"),/unavailable/);
  assert.equal(popup.closed,true);
});
