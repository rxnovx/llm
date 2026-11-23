import type { Conversation, TitleGeneration } from "../types/chat";

export type TitlePatch = Pick<Conversation, "id" | "title" | "title_generation">;

export function pendingTitleIds(items: Conversation[], active?: Conversation | null) {
  return [...new Set([...items, ...(active ? [active] : [])]
    .filter(item => !item.read_only && ["queued", "running"].includes(item.title_generation?.status ?? ""))
    .map(item => item.id))].slice(0, 100);
}

export function parseTitlePatches(value: unknown): TitlePatch[] {
  if (!Array.isArray(value)) return [];
  return value.slice(0, 100).filter((item): item is TitlePatch => {
    if (!item || typeof item.id !== "string" || !/^[a-f0-9-]{36}$/i.test(item.id)
      || typeof item.title !== "string" || !item.title.trim() || item.title.length > 120) return false;
    const state = item.title_generation as TitleGeneration | undefined;
    return !!state && ["queued", "running", "complete", "error", "manual"].includes(state.status)
      && Number.isSafeInteger(state.revision) && state.revision >= 0 && Number.isSafeInteger(state.expires_at);
  });
}

export function applyTitlePatch(item: Conversation, patches: TitlePatch[]) {
  const patch = patches.find(value => value.id === item.id);
  if (!patch?.title_generation || item.read_only
    || patch.title_generation.revision < (item.title_generation?.revision ?? -1)) return item;
  if (patch.title === item.title && patch.title_generation.revision === item.title_generation?.revision
    && patch.title_generation.status === item.title_generation?.status) return item;
  return { ...item, title: patch.title, title_generation: patch.title_generation };
}
