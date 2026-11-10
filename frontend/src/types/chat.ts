export type ModelProfile = { name: string; context_window: number; max_output_tokens: number; max_concurrent: number };
export type Connection = { id: string; name: string; provider: "test" | "vllm" | "ollama"; default_model: string; is_test: boolean;
  models: ModelProfile[]; context_window: number; max_output_tokens: number; max_concurrent: number };
export type ContextReport = { context_window: number; input_budget: number; reserved_output_tokens: number; estimated_input_tokens: number;
  is_estimate: boolean; included_turns: number; omitted_turns: number; summarized_turns: number; summary_included: boolean; too_large: boolean;
  summary: string; through_position: number; last_complete_position: number; revision: number };
export type QueueState = { status: "queued" | "running"; position: number; request_id: string };
export type Conversation = { id: string; title: string; connection_id: string; model: string; created_at: number; updated_at: number; busy: boolean };
export type ChatMessage = { id: string; role: "user" | "assistant"; content: string; status: "pending" | "complete" | "cancelled" | "error";
  model: string; is_test: boolean; error: string | null; created_at: number };
export type ConversationDetail = { conversation: Conversation; messages: ChatMessage[]; generation?: QueueState | null };
export type ChatEvent =
  | { event: "start"; data: { conversation: Conversation; user_message: ChatMessage; assistant_message: ChatMessage; context?: ContextReport } }
  | { event: "queue"; data: QueueState }
  | { event: "context"; data: ContextReport }
  | { event: "delta"; data: { content: string } }
  | { event: "done" | "error" | "cancelled"; data: { conversation: Conversation; message: ChatMessage; detail?: string | null } };
