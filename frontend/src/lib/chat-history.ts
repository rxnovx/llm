export const messagePageQuery = 'message_limit=40';

type Cursor = { before_position: number } | { after_position: number } | { around_message_id: string };

export function conversationHistoryPath(conversationId: string, cursor?: Cursor) {
  const query = new URLSearchParams(messagePageQuery);
  if (cursor) {
    const [[key, value]] = Object.entries(cursor);
    query.set(key, String(value));
  }
  return `/conversations/${encodeURIComponent(conversationId)}?${query}`;
}
