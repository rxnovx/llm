import type { Conversation } from "../types/chat";

export type OrganizationChange = { pinned?: boolean; archived?: boolean };

export function mergeConversation(current: Conversation | null | undefined, incoming: Conversation): Conversation {
  if (!current || current.id !== incoming.id) return incoming;
  const result = { ...current, ...incoming };
  if ((current.organization_revision ?? 0) > (incoming.organization_revision ?? 0)) {
    result.pinned = current.pinned; result.archived = current.archived;
    result.pinned_at = current.pinned_at; result.organization_revision = current.organization_revision;
  }
  if ((current.title_generation?.revision ?? -1) > (incoming.title_generation?.revision ?? -1)) {
    result.title = current.title; result.title_generation = current.title_generation;
  }
  return result;
}

export function sortConversations(items: Conversation[]) {
  return [...items].sort((a, b) => Number(!!b.pinned) - Number(!!a.pinned)
    || (b.pinned ? b.pinned_at ?? 0 : 0) - (a.pinned ? a.pinned_at ?? 0 : 0)
    || b.updated_at - a.updated_at || b.id.localeCompare(a.id));
}

export function updateConversationList(items: Conversation[], incoming: Conversation, query = "", projectId = "") {
  const current = items.find(item => item.id === incoming.id);
  const merged = mergeConversation(current, incoming);
  const remaining = items.filter(item => item.id !== incoming.id);
  if (projectId && merged.project_id !== projectId || !query && merged.archived
    || query && !current && !merged.match) return remaining;
  return sortConversations([merged, ...remaining]);
}
