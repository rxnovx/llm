import type { GitlabSource } from "@/lib/app-connections";
export function MessageSources({ sources }: { sources: GitlabSource[] }) {
  if (!sources.length) return null;
  return <aside className="cw-message-sources" aria-label="GitLab 조회 출처"><span>조회한 소스</span><div>{sources.map((source) =>
    <a key={source.id} href={/^https?:\/\//i.test(source.url) ? source.url : undefined} target="_blank" rel="noopener noreferrer">
      <strong>[{source.id}] {source.path}</strong><small>{source.project} · {source.start_line}–{source.end_line}줄 · {source.commit.slice(0, 8)}</small></a>)}</div></aside>;
}
