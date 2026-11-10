export type Connection = { id: string; name: string; provider: "test" | "vllm" | "ollama"; default_model: string; is_test: boolean };
export type Conversation = { id: string; title: string; connection_id: string; model: string; created_at: number; updated_at: number; busy: boolean };
export type ChatMessage = { id: string; role: "user" | "assistant"; content: string; status: "pending" | "complete" | "cancelled" | "error";
  model: string; is_test: boolean; error: string | null; created_at: number };
export type ConversationDetail = { conversation: Conversation; messages: ChatMessage[] };
export type ChatEvent =
  | { event: "start"; data: { conversation: Conversation; user_message: ChatMessage; assistant_message: ChatMessage } }
  | { event: "delta"; data: { content: string } }
  | { event: "done" | "error" | "cancelled"; data: { conversation: Conversation; message: ChatMessage; detail?: string | null } };
