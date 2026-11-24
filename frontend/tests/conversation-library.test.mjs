import assert from "node:assert/strict";
import { test } from "node:test";
import { mergeConversation, sortConversations, updateConversationList } from "../src/lib/conversation-organization.ts";
import { activeJob, elapsedLabel, jobStatus } from "../src/lib/job-center.ts";

const conversation = (id, extra = {}) => ({ id, title: id, connection_id: "office", model: "gemma",
  created_at: 1, updated_at: 1, busy: false, ...extra });
const job = (extra = {}) => ({ id: "attempt", conversation_id: "chat", title: "작업", model: "gemma", is_test: false,
  status: "running", started_at: 1, finished_at: null, elapsed_ms: 5000, position: 0,
  cancel_requested: false, can_cancel: true, error_code: null, activity: null, ...extra });

test("pinning preserves deterministic order across current updates and does not mutate history", () => {
  const items = [conversation("a", { pinned: true, pinned_at: 10 }), conversation("b", { updated_at: 50 }),
    conversation("c", { pinned: true, pinned_at: 20 })];
  assert.deepEqual(sortConversations(items).map(item => item.id), ["c", "a", "b"]);
  assert.deepEqual(items.map(item => item.id), ["a", "b", "c"]);
  assert.deepEqual(updateConversationList(items, conversation("b", { updated_at: 100 })).map(item => item.id), ["c", "a", "b"]);
});

test("late conversation snapshots cannot overwrite personal organization or manual title revisions", () => {
  const current = conversation("a", { archived: true, pinned: false, organization_revision: 5,
    title: "직접 작성한 제목", title_generation: { status: "manual", revision: 4, expires_at: 0 } });
  const incoming = conversation("a", { archived: false, pinned: true, organization_revision: 2,
    title: "늦은 모델 제목", title_generation: { status: "complete", revision: 2, expires_at: 0 }, busy: true });
  const merged = mergeConversation(current, incoming);
  assert.equal(merged.archived, true); assert.equal(merged.pinned, false);
  assert.equal(merged.organization_revision, 5); assert.equal(merged.title, "직접 작성한 제목");
  assert.equal(merged.busy, true);
  assert.equal(mergeConversation(current, conversation("other")).id, "other");
  assert.equal(mergeConversation(null, incoming), incoming);
});

test("sending a new question can return a conversation to history with a newer revision", () => {
  const archived = conversation("a", { archived: true, organization_revision: 2 });
  const restored = conversation("a", { archived: false, organization_revision: 3 });
  assert.equal(mergeConversation(archived, restored).archived, false);
  assert.equal(updateConversationList([], restored)[0].id, "a");
});

test("archive hides normal rows while preserving explicit search results and project scope", () => {
  const a = conversation("a"), b = conversation("b");
  const archived = conversation("a", { archived: true, organization_revision: 1 });
  assert.deepEqual(updateConversationList([a, b], archived).map(item => item.id), ["b"]);
  assert.equal(updateConversationList([a], archived, "배포")[0].archived, true);
  assert.deepEqual(updateConversationList([a], conversation("b"), "배포").map(item => item.id), ["a"]);
  assert.deepEqual(updateConversationList([a], conversation("a", { project_id: "other" }), "", "current"), []);
});

test("legacy payloads remain usable without personal organization fields", () => {
  const legacy = conversation("a");
  assert.equal(updateConversationList([], legacy)[0].id, "a");
  assert.equal(mergeConversation(legacy, conversation("a", { title: "이름 변경" })).title, "이름 변경");
});

test("cancellation stays pending until the recorded job actually finishes", () => {
  assert.equal(jobStatus(job({ cancel_requested: true })), "중지 요청됨");
  assert.equal(jobStatus(job({ cancel_requested: true, status: "complete" })), "완료");
  assert.equal(jobStatus(job({ status: "error", error_code: "interrupted" })), "서버 중단 감지");
  assert.equal(activeJob(job({ status: "tool_waiting" })), true);
  assert.equal(activeJob(job({ status: "error" })), false);
});

test("elapsed labels advance from the server sample and never invent interrupted durations", () => {
  assert.equal(elapsedLabel(job(), 1000, 3000), "7초");
  assert.equal(elapsedLabel(job(), 3000, 1000), "5초");
  assert.equal(elapsedLabel(job({ elapsed_ms: 65000, status: "complete" }), 1000, 100000), "1분 5초");
  assert.equal(elapsedLabel(job({ elapsed_ms: null, error_code: "interrupted" }), 1000, 100000), "소요 시간 미확인");
});
