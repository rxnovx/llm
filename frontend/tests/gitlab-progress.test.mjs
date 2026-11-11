import assert from "node:assert/strict";
import { after, test } from "node:test";
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from "node:fs";
import path from "node:path";
import { pathToFileURL } from "node:url";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import ts from "typescript";

const cache = path.resolve("node_modules/.cache");
mkdirSync(cache, { recursive: true });
const directory = mkdtempSync(path.join(cache, "gitlab-progress-"));
const output = path.join(directory, "GitlabWorkProgress.mjs");
const { outputText } = ts.transpileModule(readFileSync("src/components/chat/GitlabWorkProgress.tsx", "utf8"), {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022, jsx: ts.JsxEmit.ReactJSX },
});
writeFileSync(output, outputText);
const { GitlabWorkProgress } = await import(pathToFileURL(output).href);
after(() => rmSync(directory, { recursive: true, force: true }));

const activity = (name, status, step = 0, detail = "") => ({ name, status, step, detail });
const render = (status, tools) => renderToStaticMarkup(createElement(GitlabWorkProgress, { message: { status, tools } }));

test("MR progress shows observed stages and distinguishes running, errors and stopped work", () => {
  const tools = [
    activity("gitlab_mr_reviews", "complete"),
    activity("gitlab_mr_reviews", "running"), // Reloaded events can have the same timestamp.
    activity("gitlab_mr_ci", "complete", 0, "파이프라인 #58 · failed"),
    activity("terminal_exec", "error", 3),
    activity("terminal_exec", "running", 4),
  ];
  const live = render("pending", tools);
  assert.match(live, /data-status="complete"[^<]*><span[^>]*><\/span><span>리뷰 조회/);
  assert.match(live, /파이프라인 #58 · failed/);
  assert.match(live, /role="status">명령·테스트 실행 진행 중/);
  assert.doesNotMatch(live, /기존 MR에 커밋/);
  assert.match(render("cancelled", tools), /data-status="stopped"/);
});

test("an authoritative saved commit completes the stage if streaming stopped afterward", () => {
  const url = "https://gitlab.example/team/app/-/merge_requests/27";
  const html = render("cancelled", [
    activity("gitlab_mr_overview", "complete", 0, JSON.stringify({ iid: 27, url, editable: true })),
    activity("gitlab_update_merge_request", "running", 6),
    activity("gitlab_publication", "complete", 6),
  ]);
  assert.match(html, /href="https:\/\/gitlab\.example\/team\/app\/-\/merge_requests\/27"/);
  assert.match(html, /기존 MR에 커밋<\/span><span[^>]*>완료/);
  assert.doesNotMatch(render("complete", [activity("gitlab_mr_overview", "complete", 0,
    JSON.stringify({ iid: 27, url: "javascript:alert(1)" }))]), /href=/);
});
