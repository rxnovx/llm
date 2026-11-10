import type { Connection } from "./chat";

export type OpsMode = "all" | "live" | "test";
export type RunStatus = "complete" | "error" | "cancelled" | "queued" | "running" | "interrupted";
export type RunCounts = Record<RunStatus | "total", number> & { average_ms: number | null; success_rate: number | null };
export type OpsOverview = {
  generated_at: number; time_zone: string; mode: OpsMode;
  users: { total: number; active: number; new_24h: number; logged_in_24h: number; sessions: number };
  conversations: number; messages: number; requests_24h: RunCounts; generating: number; queued: number; first_record_at: number | null;
  daily: (RunCounts & { date: string })[];
  model_usage: { connection_id: string; provider: string; model: string; is_test: boolean; requests: number; output_chars: number }[];
  connections: Connection[]; configuration_error: boolean;
  service: { database: string; uptime_seconds: number; app_env: string; test_mode: boolean; registration_enabled: boolean };
};
export type OpsRequest = { id: string; username: string | null; model: string; connection_id: string; provider: string; is_test: boolean;
  status: RunStatus; started_at: number; duration_ms: number | null; first_token_ms: number | null; output_chars: number; error_code: string | null };
export type OpsUser = { id: string; username: string; display_name: string | null; is_active: boolean; is_admin: boolean; created_at: number; last_login_at: number | null; sessions: number };
export type OpsAudit = { id: string; action: string; outcome: string; actor: string | null; target: string | null; created_at: number };
export type OpsPage<T> = { items: T[]; has_more: boolean };
