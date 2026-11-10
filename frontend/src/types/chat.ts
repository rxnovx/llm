import type { GitlabSelection, GitlabSource } from "@/lib/app-connections";
import type { AssistantSelection,KnowledgeSource } from "@/lib/knowledge";
export type MessageSource=GitlabSource|KnowledgeSource;
export type ModelProfile = { name: string; context_window: number; max_output_tokens: number; max_concurrent: number; supports_tools?: boolean };
export type Connection = { id: string; name: string; provider: "test" | "vllm" | "ollama"; default_model: string; is_test: boolean;
  models: ModelProfile[]; context_window: number; max_output_tokens: number; max_concurrent: number; supports_tools?: boolean };
export type ContextReport = { context_window: number; input_budget: number; reserved_output_tokens: number; estimated_input_tokens: number;
  is_estimate: boolean; included_turns: number; omitted_turns: number; summarized_turns: number; summary_included: boolean; too_large: boolean;
  summary: string; through_position: number; last_complete_position: number; revision: number };
export type QueueState = { status: "queued" | "running" | "tool_waiting"; position: number; request_id: string };
export type ToolActivity = { name: string; step: number; status: "running"|"complete"|"error"; detail: string };
export type Workspace = { id: string; conversation_id: string; status: string; allow_execution: boolean; ref: string; project_id: number; source: {project?:string;commit?:string} };
export type Conversation = { id: string; title: string; connection_id: string; model: string; created_at: number; updated_at: number; busy: boolean };
export type ChatMessage = { id: string; role: "user" | "assistant"; content: string; status: "pending" | "complete" | "cancelled" | "error";
  model: string; is_test: boolean; error: string | null; created_at: number; apps?: GitlabSelection[]; sources?: MessageSource[]; tools?: ToolActivity[];assistant?:AssistantSelection };
export type ConversationDetail = { conversation: Conversation; messages: ChatMessage[]; generation?: QueueState | null };
export type ChatEvent =
  | { event: "start"; data: { conversation: Conversation; user_message: ChatMessage; assistant_message: ChatMessage; context?: ContextReport } }
  | { event: "queue"; data: QueueState }
  | { event: "context"; data: ContextReport }
  | { event: "tool"; data: { app: "gitlab"; status: "reading"; detail: string } }
  | { event: "sources"; data: { sources: MessageSource[] } }
  | { event: "activity"; data: ToolActivity }
  | { event: "workspace"; data: Workspace }
  | { event: "delta"; data: { content: string } }
  | { event: "done" | "error" | "cancelled"; data: { conversation: Conversation; message: ChatMessage; detail?: string | null } };
