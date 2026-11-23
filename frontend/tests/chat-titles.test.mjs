import assert from "node:assert/strict";
import { test } from "node:test";
import { applyTitlePatch, parseTitlePatches, pendingTitleIds } from "../src/lib/chat-titles.ts";

const id = "11111111-1111-4111-8111-111111111111";
const other = "22222222-2222-4222-8222-222222222222";
const state = (status, revision) => ({ status, revision, expires_at: 2000 });
const conversation = { id, title: "원래 질문", connection_id: "office", model: "gemma", updated_at: 100,
  busy: true, title_generation: state("queued", 0) };

test("title completes without replacing model, busy state or conversation ordering timestamp", () => {
  const result = applyTitlePatch(conversation, [{ id, title: "질문 요약 제목", title_generation: state("complete", 2) }]);
  assert.equal(result.title, "질문 요약 제목");
  assert.equal(result.updated_at, 100);
  assert.equal(result.model, "gemma");
  assert.equal(result.busy, true);
  assert.equal(conversation.title, "원래 질문");
});

test("a late automatic title cannot replace a newer manual rename, including the same-text case", () => {
  const manual = { ...conversation, title_generation: state("manual", 3) };
  const result = applyTitlePatch(manual, [{ id, title: "늦게 도착한 결과", title_generation: state("complete", 2) }]);
  assert.equal(result, manual);
});

test("a title from another chat cannot replace the currently opened chat", () => {
  assert.equal(applyTitlePatch(conversation, [{ id: other, title: "다른 대화", title_generation: state("complete", 2) }]), conversation);
});

test("pending IDs survive chat switches, deduplicate and stop polling on completion or failure", () => {
  const second = { ...conversation, id: other, title_generation: state("running", 1) };
  assert.deepEqual(pendingTitleIds([conversation, second], second), [id, other]);
  assert.deepEqual(pendingTitleIds([{ ...conversation, title_generation: state("complete", 2) },
    { ...second, title_generation: state("error", 2) }]), []);
  assert.deepEqual(pendingTitleIds([{ ...conversation, read_only: true }]), []);
  assert.equal(pendingTitleIds(Array.from({ length: 150 }, (_, i) => ({ ...conversation, id: String(i) }))).length, 100);
});

test("legacy backends without title metadata remain compatible and unchanged snapshots reuse the object", () => {
  assert.deepEqual(pendingTitleIds([{ ...conversation, title_generation: undefined }]), []);
  assert.deepEqual(parseTitlePatches(undefined), []);
  assert.equal(applyTitlePatch(conversation, [conversation]), conversation);
});

test("malformed or unversioned patches are rejected while valid server text stays plain data", () => {
  const valid = { id, title: "제목 <script>데이터</script>", title_generation: state("complete", 2) };
  assert.deepEqual(parseTitlePatches([valid]), [valid]);
  for (const patch of [{ ...valid, id: "invalid" }, { ...valid, title: "" }, { ...valid, title: "a".repeat(121) },
    { ...valid, title_generation: state("complete", -1) }, { ...valid, title_generation: state("unknown", 2) },
    { ...valid, title_generation: null }]) assert.deepEqual(parseTitlePatches([patch]), []);
  assert.equal(applyTitlePatch({ ...conversation, read_only: true }, [valid]).title, "원래 질문");
});
