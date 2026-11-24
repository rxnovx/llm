export type ChatJob = {
  id: string; conversation_id: string; title: string; model: string; is_test: boolean;
  status: "queued" | "running" | "tool_waiting" | "complete" | "cancelled" | "error";
  started_at: number; finished_at: number | null; interrupted_detected_at?: number | null; elapsed_ms: number | null;
  position: number; cancel_requested: boolean; can_cancel: boolean; error_code: string | null;
  activity: { name: string; status: string; duration_ms: number | null } | null;
};
export type JobPage = { items: ChatJob[]; has_more: boolean; active_count: number };

export function activeJob(job: ChatJob) {
  return ["queued", "running", "tool_waiting"].includes(job.status);
}
export function jobStatus(job: ChatJob) {
  if (job.cancel_requested && activeJob(job)) return "중지 요청됨";
  if (job.error_code === "interrupted") return "서버 중단 감지";
  return { queued: "대기 중", running: "답변 생성 중", tool_waiting: "도구 작업 중",
    complete: "완료", cancelled: "중지됨", error: "오류" }[job.status];
}
export function elapsedLabel(job: ChatJob, receivedAt: number, now: number) {
  if (job.elapsed_ms === null) return "소요 시간 미확인";
  const value = Math.max(0, job.elapsed_ms + (activeJob(job) ? Math.max(0, now - receivedAt) : 0));
  const seconds = Math.floor(value / 1000);
  return seconds < 60 ? `${seconds}초` : `${Math.floor(seconds / 60)}분 ${seconds % 60}초`;
}
