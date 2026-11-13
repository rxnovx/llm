import type { GitlabSelection, GitlabSource } from "@/lib/app-connections";
import type { AssistantSelection,KnowledgeSource } from "@/lib/knowledge";
import type { ResultFile,Coverage,McpSource } from '@/lib/workflows';
export type WebSource = { id: string; app: "web"; title: string; url: string; excerpt?: string };
export type ChatHistorySource = { id: string; app: "chat_history"; title: string; conversation_id: string; message_id: string; excerpt?: string };
export type TableAnalysisSource = { id: string; app: "table_analysis"; title: string;
  documents: { document_id: string; filename: string; sha256: string; revision: number; sheet?: string | null }[]; excerpt: string };
export type MessageSource = GitlabSource | KnowledgeSource | McpSource | WebSource | ChatHistorySource | TableAnalysisSource;
export type ModelProfile = { name: string; context_window: number; max_output_tokens: number; max_concurrent: number; supports_tools?: boolean; supports_vision?: boolean };
export type Connection = { id: string; name: string; provider: "test" | "vllm" | "ollama"; default_model: string; is_test: boolean;
  models: ModelProfile[]; context_window: number; max_output_tokens: number; max_concurrent: number; supports_tools?: boolean; supports_vision?: boolean };
export type ChatConfig = {
  connections: Connection[];
  terminal?: { enabled: boolean; execution_enabled: boolean };
  images?: { generate: boolean; edit: boolean };
};
export type ContextReport = { context_window: number; input_budget: number; reserved_output_tokens: number; estimated_input_tokens: number;
  is_estimate: boolean; included_turns: number; omitted_turns: number; summarized_turns: number; summary_included: boolean; too_large: boolean;
  summary: string; through_position: number; last_complete_position: number; revision: number; auto_summary_enabled?:boolean;auto_summary?:string;auto_through_position?:number;project_name?:string|null };
export type QueueState = { status: "queued" | "running" | "tool_waiting"; position: number; request_id: string; kind?: "image" | "model" };
export type ToolActivity = { name: string; step: number; status: "running"|"complete"|"error"; detail: string };
export type Workspace = { id: string; conversation_id: string; status: string; allow_execution: boolean; ref: string; project_id: number; source: {project?:string;commit?:string} };
export type Conversation = { id: string; title: string; connection_id: string; model: string; created_at: number; updated_at: number; busy: boolean;read_only?:boolean;project_id?:string|null;project_name?:string|null;parent_id?:string|null;auto_summary?:boolean;match?:{message_id:string;role:string;excerpt:string}|null };
export type CanvasSelection = { start: number; end: number | null };
export type ChatMessage = { id: string; role: "user" | "assistant"; content: string; status: "pending" | "complete" | "cancelled" | "error";
  artifacts?:ResultFile[];versions?:{id:string;sequence:number;model:string;created_at:number}[];active_version?:string|null;feedback_count?:number;
  recovery?:{code:string;phase:string;requires_confirmation:boolean;preserved_previous?:boolean;can_retry:boolean;can_resume_execution:boolean;actions:string[]}|null;
  model: string; is_test: boolean; error: string | null; created_at: number; apps?: GitlabSelection[]; sources?: MessageSource[]; tools?: ToolActivity[];assistant?:AssistantSelection;routing?:{canvas?:CanvasSelection;coverage?:Coverage;mcp_ids?:string[];automatic?:boolean;automatic_project?:boolean;vision?:boolean;route?:string;status?:string;rounds?:number;grounding?:{verdict:string;issues_count:number}} };
export type MessagePage = { older_before: number | null; newer_after: number | null };
export type ConversationDetail = { conversation: Conversation; messages: ChatMessage[]; generation?: QueueState | null; message_page?: MessagePage };
export type ChatEvent =
  | { event: "start"; data: { conversation: Conversation; user_message: ChatMessage; assistant_message: ChatMessage; context?: ContextReport } }
  | { event: "queue"; data: QueueState }
  | { event: "context"; data: ContextReport }
  | { event: "tool"; data: { app: "gitlab"; status: "reading"; detail: string } }
  | { event: "sources"; data: { sources: MessageSource[] } }
  | { event: "activity"; data: ToolActivity }
  | { event: "workspace"; data: Workspace & {selection?:GitlabSelection;automatic?:boolean} }
  | { event: "artifact"; data: ResultFile }
  | { event: "coverage"; data: Coverage }
  | { event: "canvas"; data: CanvasSelection }
  | { event: "delta"; data: { content: string } }
  | { event: "done" | "error" | "cancelled"; data: { conversation: Conversation; message: ChatMessage; detail?: string | null } };
