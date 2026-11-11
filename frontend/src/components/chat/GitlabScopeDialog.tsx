"use client";
import { useEffect, useRef, useState } from "react";
import { WorkspaceLink } from "@/components/shared/WorkspaceLink";
import { useDialogDismiss } from "@/components/shared/useDialogDismiss";
import { appsJson, type AppCatalog, type AppConnection, type GitlabSelection, type ResolvedMergeRequest } from "@/lib/app-connections";
import { Icon } from "./Icons";

type Project = { id: number; name: string; default_branch: string | null };
type Tree = { items: { name: string; path: string; type: "tree" | "blob"; selectable: boolean }[]; has_more: boolean };
const errorText = (error: unknown) => error instanceof Error ? error.message : "GitLab 자료를 불러오지 못했습니다.";

type Terminal = { enabled: boolean; execution_enabled: boolean };
type SelectScope = (selection: GitlabSelection, publish: boolean) => void;

export function GitlabScopeDialog({ onClose, onSelect, terminal, initialReference = "", canWriteModel }: {
  onClose: () => void; onSelect: SelectScope; terminal?: Terminal; initialReference?: string; canWriteModel: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const dismiss = useDialogDismiss(onClose);
  const [catalog, setCatalog] = useState<AppCatalog | null>(null), [error, setError] = useState("");
  const [connection, setConnection] = useState(""), [project, setProject] = useState<Project | null>(null);
  const [ref, setRef] = useState("HEAD"), [directory, setDirectory] = useState(""), [paths, setPaths] = useState<string[]>([]);
  const [mode,setMode]=useState<"files"|"workspace"|"execute"|"merge_request">(initialReference ? "merge_request" : "files");
  const [accountChosen, setAccountChosen] = useState(false);
  const [mrReference, setMrReference] = useState(initialReference);
  const [publish, setPublish] = useState(false);
  useEffect(() => {
    const element = dialog.current!; element.showModal();
    const abort = new AbortController();
    void appsJson<AppCatalog>("/catalog", { signal: abort.signal }).then((value) => {
      setCatalog(value); setConnection(value.apps[0]?.connections.find((item) => item.status === "connected")?.id ?? "");
    }).catch((e) => { if (!abort.signal.aborted) setError(errorText(e)); });
    return () => { abort.abort(); element.close(); };
  }, []);
  const connections = catalog?.apps[0]?.connections.filter((item) => item.status === "connected") ?? [];
  const selectedConnection = connections.find(item => item.id === connection);
  const canPublish = mode === "execute" && !!project && !!selectedConnection?.can_publish;
  return <dialog className="cw-dialog cw-gitlab-dialog" ref={dialog} aria-labelledby="gitlab-scope-title" {...dismiss}>
    <header className="cw-dialog-header"><h2 id="gitlab-scope-title">GitLab에서 가져오기</h2>
      <button type="button" className="cw-icon-button" aria-label="GitLab 선택 닫기" onClick={onClose}><Icon name="close" /></button></header>
    <p className="cw-dialog-description">MR이나 프로젝트 파일을 선택하세요. 조회한 자료는 현재 모델 서버로 전달됩니다.</p>
    <label className="cw-scope-field">작업 방식<select value={mode} onChange={(e)=>{setMode(e.target.value as typeof mode);setPublish(false);}}><option value="files">선택한 파일 조회</option>
      <option value="merge_request">기존 MR 검토·후속 작업</option>
      <option value="workspace" disabled={!terminal?.enabled}>프로젝트 자동 분석{!terminal?.enabled?" · 서버 설정 필요":""}</option>
      <option value="execute" disabled={!terminal?.enabled||!terminal.execution_enabled}>프로젝트 분석·실행{!terminal?.execution_enabled?" · 실행 서버 설정 필요":""}</option></select></label>
    {mode==="execute"&&<p className="cw-inline-error">선택한 프로젝트 작업공간에서 모델의 명령 실행과 파일 수정을 허용합니다. 변경 사항은 작업공간에 보관됩니다.</p>}
    {error && <p role="alert" className="cw-inline-error">{error}</p>}
    {!catalog && !error && <p role="status">내 앱 연결을 불러오는 중…</p>}
    {catalog && !connections.length && <div className="cw-gitlab-empty"><p>먼저 내 GitLab 계정을 연결하세요.</p>
      <WorkspaceLink href="/user/settings/apps" onClick={onClose}>설정에서 GitLab 연결</WorkspaceLink></div>}
    {!!connections.length && <><label className="cw-scope-field">계정<select value={mode === "merge_request" && !accountChosen ? "" : connection} onChange={(e) => {
      setConnection(e.target.value || connections[0].id); setAccountChosen(!!e.target.value); setProject(null); setPaths([]); setDirectory(""); setPublish(false);
    }}>
      {mode === "merge_request" && <option value="">MR 링크로 계정 선택</option>}
      {connections.map((item) => <option key={item.id} value={item.id}>@{item.username} · {catalog?.apps[0].providers.find((p) => p.id === item.provider_id)?.name ?? item.provider_id}</option>)}</select></label>
      {mode === "merge_request" ? <MergeRequestPicker key={`${connection}/${accountChosen}`} reference={mrReference} onReference={setMrReference} connection={connection}
        accountChosen={accountChosen} connections={connections} terminal={terminal} canWriteModel={canWriteModel} onSelect={onSelect} onClose={onClose} /> : <>
      {!project ? <ProjectPicker key={connection} connection={connection} onSelect={(item) => { setProject(item); setRef(item.default_branch || "HEAD"); setPaths([]); setDirectory(""); }} />
        : <><div className="cw-scope-project"><strong>{project.name}</strong><button type="button" className="cw-text-button" onClick={() => { setProject(null); setPaths([]); setPublish(false); }}>프로젝트 변경</button></div>
          <BranchPicker key={`${connection}/${project.id}`} connection={connection} project={project.id} value={ref} onChange={(value) => { setRef(value); setPaths([]); setDirectory(""); setPublish(false); }} />
          <div className="cw-scope-directory"><button type="button" className="cw-text-button" disabled={!directory} onClick={() => setDirectory(directory.split("/").slice(0, -1).join("/"))}>↑ 상위</button><span>{directory || "/"}</span></div>
          {mode==="files"&&<FilePicker key={`${connection}/${project.id}/${ref}/${directory}`} connection={connection} project={project.id} branch={ref} path={directory} paths={paths}
            onDirectory={setDirectory} onToggle={(path) => setPaths((old) => old.includes(path) ? old.filter((p) => p !== path) : old.length < 5 ? [...old, path] : old)} />}
          {!!paths.length && <ul className="cw-selected-files" aria-label="선택한 파일">{paths.map((path) => <li key={path}><span>{path}</span>
            <button type="button" className="cw-icon-button" aria-label={`${path} 선택 해제`} onClick={() => setPaths((old) => old.filter((p) => p !== path))}><Icon name="close" size={13} /></button></li>)}</ul>}
        </>}
      {canPublish && <label className="cw-knowledge-app">
        <input type="checkbox" checked={publish} onChange={event => setPublish(event.target.checked)} />
        <span>이번 요청의 GitLab MR 생성 허용<small>변경사항을 새 브랜치에 커밋하고 선택한 브랜치로 MR을 엽니다.</small></span>
      </label>}
      {mode === "execute" && !selectedConnection?.can_publish && catalog?.apps[0]?.providers.some(
        provider => provider.id === selectedConnection?.provider_id && provider.publish_enabled) &&
        <WorkspaceLink href="/user/settings/apps" onClick={onClose}>설정에서 MR 작성 권한 연결</WorkspaceLink>}
      <footer className="cw-dialog-footer"><span>{mode==="files"?`${paths.length}/5개 파일`:mode==="execute"?"분석·실행 허용":"프로젝트 자동 탐색"}</span><button type="button" className="cw-primary-button" disabled={!project || mode==="files"&&!paths.length} onClick={() => {
        if (project) onSelect({ app: "gitlab", connection_id: connection, project_id: project.id, ref, paths:mode==="files"?paths:[],...(mode==="files"?{}:{mode}) }, canPublish && publish);
      }}>이 질문에 추가</button></footer></>}</>}
  </dialog>;
}

function MergeRequestPicker({ reference, onReference, connection, accountChosen, connections, terminal, canWriteModel, onSelect, onClose }: {
  reference: string; onReference: (value: string) => void; connection: string; accountChosen: boolean; connections: AppConnection[];
  terminal?: Terminal; canWriteModel: boolean; onSelect: SelectScope; onClose: () => void;
}) {
  const [project, setProject] = useState<Project | null>(null);
  const [result, setResult] = useState<ResolvedMergeRequest | null>(null);
  const [loading, setLoading] = useState(false), [error, setError] = useState("");
  const [execute, setExecute] = useState(false), [publish, setPublish] = useState(false);
  const request = useRef<AbortController | null>(null);
  useEffect(() => () => request.current?.abort(), []);
  const short = /^![1-9][0-9]*$/.test(reference.trim());
  const link = /^https?:\/\//i.test(reference.trim());
  const needsAccount = !!reference.trim() && !link && !accountChosen && connections.length > 1;
  const canLookup = !loading && !!reference.trim() && !needsAccount && (!short || !!project);
  const canExecute = !!(result?.merge_request.editable && terminal?.enabled && terminal.execution_enabled && canWriteModel);
  const canPublish = canExecute && execute && connections.some(item => item.id === result?.selection.connection_id && item.can_publish);
  const lookup = async () => {
    if (!canLookup) return;
    const controller = new AbortController();
    request.current = controller;
    setLoading(true); setError(""); setResult(null); setExecute(false); setPublish(false);
    try {
      const value = await appsJson<ResolvedMergeRequest>("/gitlab/merge-request/resolve", {
        method: "POST", signal: controller.signal,
        body: JSON.stringify({
          reference: reference.trim(),
          ...(accountChosen || !link ? { connection_id: connection } : {}),
          ...(short && project ? { project_id: project.id } : {}),
        }),
      });
      if (!controller.signal.aborted) setResult(value);
    } catch (caught) {
      if (!controller.signal.aborted) setError(errorText(caught));
    } finally {
      if (!controller.signal.aborted) setLoading(false);
    }
  };
  const mr = result?.merge_request;
  return <>
    <label className="cw-scope-field">MR 링크 또는 번호
      <input value={reference} maxLength={1400} disabled={loading} placeholder="MR 링크, 그룹/프로젝트!27, 또는 !27"
        onChange={event => { onReference(event.target.value); setResult(null); setError(""); }}
        onKeyDown={event => { if (event.key === "Enter" && !event.nativeEvent.isComposing) { event.preventDefault(); void lookup(); } }} />
    </label>
    {needsAccount && <p className="cw-dialog-description">번호로 조회할 GitLab 계정을 선택하세요.</p>}
    {short && !needsAccount && (project
      ? <div className="cw-scope-project"><strong>{project.name}</strong><button type="button" className="cw-text-button" disabled={loading}
        onClick={() => { setProject(null); setResult(null); }}>프로젝트 변경</button></div>
      : <ProjectPicker connection={connection} onSelect={setProject} />)}
    <button type="button" className="cw-small-button" disabled={!canLookup}
      onClick={() => void lookup()}>{loading ? "MR 확인 중…" : "MR 확인"}</button>
    {error && <p role="alert" className="cw-inline-error">{error}</p>}
    {mr && <div className="cw-mr-summary">
      <strong>{mr.project}!{mr.iid} · {mr.title}</strong>
      <small>{mr.source_branch || "소스 브랜치 없음"} → {mr.target_branch || "대상 브랜치 없음"}</small>
      {!mr.editable && <small>현재 상태는 {mr.state}입니다. 리뷰와 CI를 조회할 수 있습니다.</small>}
      {mr.editable && !canExecute && <small>수정·테스트에는 실행 가능한 Terminal MCP와 도구 호출 모델이 필요합니다.</small>}
    </div>}
    {canExecute && <label className="cw-knowledge-app">
      <input type="checkbox" checked={execute} onChange={event => { setExecute(event.target.checked); setPublish(false); }} />
      <span>작업공간에서 코드 수정·테스트 허용</span>
    </label>}
    {canPublish && <label className="cw-knowledge-app">
      <input type="checkbox" checked={publish} onChange={event => setPublish(event.target.checked)} />
      <span>이번 요청의 기존 MR 커밋 허용<small>{mr?.source_branch} 브랜치에 변경을 게시합니다.</small></span>
    </label>}
    {canExecute && execute && !canPublish && <WorkspaceLink href="/user/settings/apps" onClick={onClose}>설정에서 GitLab 쓰기 권한 연결</WorkspaceLink>}
    <footer className="cw-dialog-footer"><span>{canPublish && publish ? "기존 MR에 커밋" : canExecute && execute ? "작업공간에서 수정·테스트" : "리뷰·CI 조회"}</span>
      <button type="button" className="cw-primary-button" disabled={!result} onClick={() => {
        if (result) onSelect({ ...result.selection, mode: canExecute && execute ? "execute" : "merge_request" }, canPublish && publish);
      }}>이 질문에 추가</button></footer>
  </>;
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
