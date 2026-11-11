import type { MergeRequestSummary } from "@/lib/app-connections";
import type { ChatMessage, ToolActivity } from "@/types/chat";

const stages = [
  ["MR 확인", ["gitlab_mr_overview"]],
  ["리뷰 조회", ["gitlab_mr_reviews"]],
  ["CI 확인", ["gitlab_mr_ci"]],
  ["MR 변경 파일 조회", ["gitlab_mr_diffs"]],
  ["작업공간 준비", ["gitlab_checkout"]],
  ["코드 확인", ["repository_index", "repo_map", "code_search", "code_context", "workspace_read_file"]],
  ["코드 수정", ["workspace_write_file"]],
  ["명령·테스트 실행", ["terminal_exec"]],
  ["작업공간 변경 검토", ["workspace_diff"]],
  ["기존 MR에 커밋", ["gitlab_update_merge_request", "gitlab_publication"]],
] as const;

export function gitlabWebUrl(value: unknown): string {
  if (typeof value !== "string") return "";
  try {
    const url = new URL(value);
    return (url.protocol === "http:" || url.protocol === "https:") && !url.username && !url.password ? url.href : "";
  } catch {
    return "";
  }
}

function latest(tools: ToolActivity[], names: readonly string[]) {
  return tools.reduce<ToolActivity | undefined>((previous, tool) => {
    if (!names.includes(tool.name) || previous && (previous.step > tool.step
        || previous.step === tool.step && previous.status !== "running" && tool.status === "running")) return previous;
    return tool;
  }, undefined);
}

export function GitlabWorkProgress({ message, onFollowup }: { message: ChatMessage; onFollowup?: (url: string) => void }) {
  const tools = message.tools ?? [];
  const rows = stages.flatMap(([label, names]) => {
    const tool = latest(tools, names);
    if (!tool) return [];
    const status: ToolActivity["status"] | "stopped" = tool.status === "running" && message.status !== "pending" ? "stopped" : tool.status;
    const detail = tool.name === "gitlab_mr_ci" && tool.status !== "running" ? tool.detail : "";
    return [{ label, status, detail }] as const;
  });
  if (!rows.length) return null;
  const overview = latest(tools, ["gitlab_mr_overview"]);
  let mr: MergeRequestSummary | undefined;
  if (overview?.status === "complete") {
    try { mr = JSON.parse(overview.detail) as MergeRequestSummary; } catch { /* Keep the other activity visible. */ }
  }
  const url = gitlabWebUrl(mr?.url);
  const statuses = { running: "진행 중", complete: "완료", error: "오류", stopped: "중단" };
  const running = rows.findLast(row => row.status === "running");
  return <section className="cw-mr-progress" aria-label="GitLab MR 작업 진행">
    {url && mr && <header><a className="cw-text-button" href={url} target="_blank" rel="noreferrer">MR !{mr.iid} ↗</a>
      {onFollowup && mr.editable && <button type="button" className="cw-text-button" onClick={() => onFollowup(url)}>이 MR에서 작업</button>}</header>}
    <ol>{rows.map(row => <li key={row.label} data-status={row.status}>
      <span className="cw-mr-step-dot" aria-hidden="true" /><span>{row.label}{row.detail && <small>{row.detail}</small>}</span>
      <span className="cw-mr-step-status">{statuses[row.status]}</span>
    </li>)}</ol>
    {running && <span className="cw-sr-only" role="status">{running.label} 진행 중</span>}
  </section>;
}
