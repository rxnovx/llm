"use client";
import { useEffect, useRef, useState } from "react";
import { WorkspaceLink } from "@/components/shared/WorkspaceLink";
import { appsJson, type AppCatalog, type GitlabSelection } from "@/lib/app-connections";
import { Icon } from "./Icons";

type Project = { id: number; name: string; default_branch: string | null };
type Tree = { items: { name: string; path: string; type: "tree" | "blob"; selectable: boolean }[]; has_more: boolean };
const errorText = (error: unknown) => error instanceof Error ? error.message : "GitLab 자료를 불러오지 못했습니다.";

export function GitlabScopeDialog({ onClose, onSelect }: { onClose: () => void; onSelect: (selection: GitlabSelection) => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [catalog, setCatalog] = useState<AppCatalog | null>(null), [error, setError] = useState("");
  const [connection, setConnection] = useState(""), [project, setProject] = useState<Project | null>(null);
  const [ref, setRef] = useState("HEAD"), [directory, setDirectory] = useState(""), [paths, setPaths] = useState<string[]>([]);
  useEffect(() => {
    const element = dialog.current!; element.showModal();
    const abort = new AbortController();
    void appsJson<AppCatalog>("/catalog", { signal: abort.signal }).then((value) => {
      setCatalog(value); setConnection(value.apps[0]?.connections.find((item) => item.status === "connected")?.id ?? "");
    }).catch((e) => { if (!abort.signal.aborted) setError(errorText(e)); });
    return () => { abort.abort(); element.close(); };
  }, []);
  const connections = catalog?.apps[0]?.connections.filter((item) => item.status === "connected") ?? [];
  return <dialog className="cw-dialog cw-gitlab-dialog" ref={dialog} aria-labelledby="gitlab-scope-title" onCancel={(e) => { e.preventDefault(); onClose(); }}>
    <header className="cw-dialog-header"><div><span className="cw-overline">앱 · 읽기 전용</span><h2 id="gitlab-scope-title">GitLab에서 가져오기</h2></div>
      <button type="button" className="cw-icon-button" aria-label="GitLab 선택 닫기" onClick={onClose}><Icon name="close" /></button></header>
    <p className="cw-dialog-description">이 질문에서 참고할 파일을 최대 5개 선택하세요. 선택한 파일의 발췌 내용이 현재 모델 서버로 전달됩니다.</p>
    {error && <p role="alert" className="cw-inline-error">{error}</p>}
    {!catalog && !error && <p role="status">내 앱 연결을 불러오는 중…</p>}
    {catalog && !connections.length && <div className="cw-gitlab-empty"><p>먼저 내 GitLab 계정을 연결하세요.</p>
      <WorkspaceLink href="/user/settings/apps" onClick={onClose}>설정에서 GitLab 연결</WorkspaceLink></div>}
    {!!connections.length && <><label className="cw-scope-field">계정<select value={connection} onChange={(e) => { setConnection(e.target.value); setProject(null); setPaths([]); setDirectory(""); }}>
      {connections.map((item) => <option key={item.id} value={item.id}>@{item.username} · {catalog?.apps[0].providers.find((p) => p.id === item.provider_id)?.name ?? item.provider_id}</option>)}</select></label>
      {!project ? <ProjectPicker key={connection} connection={connection} onSelect={(item) => { setProject(item); setRef(item.default_branch || "HEAD"); setPaths([]); setDirectory(""); }} />
        : <><div className="cw-scope-project"><strong>{project.name}</strong><button type="button" className="cw-text-button" onClick={() => { setProject(null); setPaths([]); }}>프로젝트 변경</button></div>
          <BranchPicker key={`${connection}/${project.id}`} connection={connection} project={project.id} value={ref} onChange={(value) => { setRef(value); setPaths([]); setDirectory(""); }} />
          <div className="cw-scope-directory"><button type="button" className="cw-text-button" disabled={!directory} onClick={() => setDirectory(directory.split("/").slice(0, -1).join("/"))}>↑ 상위</button><span>{directory || "/"}</span></div>
          <FilePicker key={`${connection}/${project.id}/${ref}/${directory}`} connection={connection} project={project.id} branch={ref} path={directory} paths={paths}
            onDirectory={setDirectory} onToggle={(path) => setPaths((old) => old.includes(path) ? old.filter((p) => p !== path) : old.length < 5 ? [...old, path] : old)} />
          {!!paths.length && <ul className="cw-selected-files" aria-label="선택한 파일">{paths.map((path) => <li key={path}><span>{path}</span>
            <button type="button" className="cw-icon-button" aria-label={`${path} 선택 해제`} onClick={() => setPaths((old) => old.filter((p) => p !== path))}><Icon name="close" size={13} /></button></li>)}</ul>}
        </>}
      <footer className="cw-dialog-footer"><span>{paths.length}/5개 · 환경 변수와 키 파일 제외</span><button type="button" className="cw-primary-button" disabled={!project || !paths.length} onClick={() => {
        if (project) onSelect({ app: "gitlab", connection_id: connection, project_id: project.id, ref, paths });
      }}>이 질문에 추가</button></footer></>}
  </dialog>;
}

function ProjectPicker({ connection, onSelect }: { connection: string; onSelect: (project: Project) => void }) {
  const [query, setQuery] = useState("");
  return <div><label className="cw-scope-field">프로젝트 검색<input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="프로젝트 이름" maxLength={100} /></label>
    <ProjectResults key={query} query={query} connection={connection} onSelect={onSelect} /></div>;
}
function ProjectResults({ connection, query, onSelect }: { connection: string; query: string; onSelect: (project: Project) => void }) {
  const [items, setItems] = useState<Project[]>([]), [more, setMore] = useState(false), [page, setPage] = useState(1), [loading, setLoading] = useState(true), [error, setError] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    const timer = setTimeout(() => { void appsJson<{ items: Project[]; has_more: boolean }>(`/connections/${connection}/projects?q=${encodeURIComponent(query)}&page=${page}`, { signal: abort.signal })
      .then((data) => { setItems((old) => [...old, ...data.items.filter((item) => !old.some((p) => p.id === item.id))]); setMore(data.has_more); })
      .catch((e) => { if (!abort.signal.aborted) setError(errorText(e)); }).finally(() => { if (!abort.signal.aborted) setLoading(false); }); }, 250);
    return () => { clearTimeout(timer); abort.abort(); };
  }, [connection, query, page]);
  return <div className="cw-scope-results">{error && <p role="alert">{error}</p>}{items.map((item) => <button type="button" key={item.id} onClick={() => onSelect(item)}><Icon name="code" size={16} /><span>{item.name}</span><span>→</span></button>)}
    {loading && <p role="status">프로젝트를 불러오는 중…</p>}{!loading && !error && !items.length && <p>접근 가능한 프로젝트가 없습니다.</p>}
    {more && <button type="button" disabled={loading} onClick={() => { setLoading(true); setError(""); setPage((p) => p+1); }}>더 보기</button>}</div>;
}
function BranchPicker({ connection, project, value, onChange }: { connection: string; project: number; value: string; onChange: (value: string) => void }) {
  const [items, setItems] = useState<string[]>([]), [page, setPage] = useState(1), [more, setMore] = useState(false), [error, setError] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    void appsJson<{ items: { name: string }[]; has_more: boolean }>(`/connections/${connection}/projects/${project}/branches?page=${page}`, { signal: abort.signal })
      .then((data) => { setItems((old) => [...new Set([...old, ...data.items.map((i) => i.name)])]); setMore(data.has_more); })
      .catch((e) => { if (!abort.signal.aborted) setError(errorText(e)); });
    return () => abort.abort();
  }, [connection, project, page]);
  return <div className="cw-scope-branch"><label className="cw-scope-field">브랜치<select value={value} onChange={(e) => onChange(e.target.value)}>
    {!items.includes(value) && <option>{value}</option>}{items.map((name) => <option key={name}>{name}</option>)}</select></label>
    {more && <button type="button" className="cw-text-button" onClick={() => setPage((p) => p+1)}>브랜치 더 불러오기</button>}{error && <p role="alert">{error}</p>}</div>;
}
function FilePicker({ connection, project, branch, path, paths, onDirectory, onToggle }: { connection: string; project: number; branch: string; path: string; paths: string[]; onDirectory: (path: string) => void; onToggle: (path: string) => void }) {
  const [items, setItems] = useState<Tree["items"]>([]), [page, setPage] = useState(1), [more, setMore] = useState(false), [loading, setLoading] = useState(true), [error, setError] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    void appsJson<Tree>(`/connections/${connection}/projects/${project}/tree?ref=${encodeURIComponent(branch)}&path=${encodeURIComponent(path)}&page=${page}`, { signal: abort.signal })
      .then((data) => { setItems((old) => [...old, ...data.items.filter((item) => !old.some((i) => i.path === item.path))]); setMore(data.has_more); })
      .catch((e) => { if (!abort.signal.aborted) setError(errorText(e)); }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [connection, project, branch, path, page]);
  return <div className="cw-scope-results cw-scope-files">{error && <p role="alert">{error}</p>}{items.map((item) => item.type === "tree"
    ? <button type="button" key={item.path} onClick={() => onDirectory(item.path)}><Icon name="panel" size={15} /><span>{item.name}/</span><span>→</span></button>
    : <label key={item.path} title={item.selectable ? item.path : "지원하지 않거나 민감한 파일 유형입니다."}><input type="checkbox" checked={paths.includes(item.path)} disabled={!item.selectable || paths.length >= 5 && !paths.includes(item.path)} onChange={() => onToggle(item.path)} /><span>{item.name}</span></label>)}
    {loading && <p role="status">파일 목록을 불러오는 중…</p>}{!loading && !error && !items.length && <p>이 디렉터리는 비어 있습니다.</p>}
    {more && <button type="button" disabled={loading} onClick={() => { setLoading(true); setPage((p) => p+1); }}>더 보기</button>}</div>;
}
