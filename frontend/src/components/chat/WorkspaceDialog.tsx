"use client";
import { useEffect,useRef,useState } from "react";
import { authenticatedFetch } from "@/lib/authenticated-fetch";
import type { Workspace } from "@/types/chat";
import { Icon } from "./Icons";

async function request<T>(path:string,init:RequestInit={}) {
  return (await (await authenticatedFetch('/api/workspaces'+path,init)).json()) as T;
}
type Job={id:string;status:string;argv:string;exit_code:number|null};
type Output={job_id:string;status:string;exit_code:number|null;next_cursor:number;truncated:boolean;events:{text:string;stream:string}[]};
const failure=(e:unknown)=>e instanceof Error?e.message:"작업을 완료하지 못했습니다.";
export function WorkspaceDialog({conversationId,onClose}:{conversationId:string;onClose:()=>void}) {
  const dialog=useRef<HTMLDialogElement>(null);
  const [items,setItems]=useState<Workspace[]>([]),[selected,setSelected]=useState("");
  const [error,setError]=useState(""),[busy,setBusy]=useState(false),[result,setResult]=useState("");
  const [query,setQuery]=useState(""),[path,setPath]=useState(""),[command,setCommand]=useState("");
  const [jobs,setJobs]=useState<Job[]>([]),[job,setJob]=useState<string|null>(null),[output,setOutput]=useState("");
  const [jobState,setJobState]=useState(""),[refresh,setRefresh]=useState(0);
  const workspace=items.find((item)=>item.id===selected);
  useEffect(()=>{
    const element=dialog.current!;element.showModal();
    const abort=new AbortController();
    void request<{items:Workspace[]}>(`?conversation_id=${conversationId}`,{signal:abort.signal}).then(data=>{
      setItems(data.items);setSelected(data.items.find(i=>i.status==='ready')?.id??data.items[0]?.id??"");
    }).catch(e=>{if(!abort.signal.aborted)setError(failure(e));});
    return()=>{abort.abort();element.close();};
  },[conversationId]);
  useEffect(()=>{
    if(!selected||workspace?.status!=='ready')return;
    const abort=new AbortController();
    void request<{items:Job[]}>(`/${selected}/tools`,{method:'POST',signal:abort.signal,body:JSON.stringify({name:'terminal_jobs',arguments:{}})})
      .then(value=>setJobs(value.items)).catch(e=>{if(!abort.signal.aborted)setError(failure(e));});
    return()=>abort.abort();
  },[selected,workspace?.status,refresh]);
  useEffect(()=>{
    if(!job||!selected)return;
    const abort=new AbortController();let cursor=0,pending=false,finished=false;
    const poll=()=>{
      if(pending||finished)return;pending=true;
      void request<Output>(`/${selected}/tools`,{method:'POST',signal:abort.signal,body:JSON.stringify({name:'terminal_read',arguments:{job_id:job,cursor}})})
        .then(value=>{cursor=value.next_cursor;setJobState(value.status);setOutput(old=>(old+value.events.map(e=>e.text).join("")).slice(-64000));
          if(!['running','queued'].includes(value.status)){finished=true;setRefresh(r=>r+1);if(value.truncated)setOutput(old=>old+'\n[출력 일부가 생략되었습니다.]');}})
        .catch(e=>{if(!abort.signal.aborted){finished=true;setError(failure(e));}}).finally(()=>{pending=false;});
    };
    poll();const timer=setInterval(poll,700);
    return()=>{abort.abort();clearInterval(timer);};
  },[job,selected]);
  const tool=async(name:string,args:Record<string,unknown>={})=>{
    if(!workspace)return;setBusy(true);setError("");
    try{const value=await request<Record<string,unknown>>(`/${workspace.id}/tools`,{method:'POST',body:JSON.stringify({name,arguments:args})});setResult(JSON.stringify(value,null,2));return value;}
    catch(e){setError(failure(e));}finally{setBusy(false);}
  };
  return <dialog ref={dialog} className="cw-dialog cw-workspace-dialog" aria-labelledby="workspace-title" onCancel={e=>{e.preventDefault();onClose();}}>
    <header className="cw-dialog-header"><div><span className="cw-overline">현재 대화</span><h2 id="workspace-title">프로젝트 작업공간</h2></div><button type="button" className="cw-icon-button" aria-label="작업공간 닫기" onClick={onClose}><Icon name="close"/></button></header>
    <p className="cw-dialog-description">@GitLab에서 프로젝트 자동 분석을 선택하고 질문을 보내면 작업공간이 만들어집니다. 같은 작업공간의 소스와 수정 사항을 이어서 사용할 수 있습니다.</p>
    {error&&<p role="alert" className="cw-inline-error">{error}</p>}
    {!items.length&&<p className="cw-dialog-description">이 대화에 연결된 작업공간이 없습니다.</p>}
    {!!items.length&&<label className="cw-scope-field">작업공간<select value={selected} onChange={e=>{setSelected(e.target.value);setResult("");setJob(null);setOutput("");setError("");}}>
      {items.map(item=><option key={item.id} value={item.id}>{item.source.project??item.id} · {item.ref} · {item.status}</option>)}</select></label>}
    {workspace?.status==='ready'&&<><p className="cw-workspace-meta">{workspace.source.commit?.slice(0,12)} · {workspace.allow_execution?'분석·실행':'분석 전용'}</p>
      <div className="cw-workspace-actions"><button type="button" disabled={busy} className="cw-small-button" onClick={()=>void tool('repo_map')}>저장소 지도</button>
        <button type="button" disabled={busy} className="cw-small-button" onClick={()=>void tool('workspace_diff')}>변경 내용</button></div>
      <div className="cw-workspace-fields"><label className="cw-scope-field">코드 검색<input value={query} maxLength={200} onChange={e=>setQuery(e.target.value)} placeholder="함수명 또는 검색어"/></label>
        <button type="button" disabled={busy||!query.trim()} className="cw-small-button" onClick={()=>void tool('code_search',{query})}>검색</button></div>
      <div className="cw-workspace-fields"><label className="cw-scope-field">파일 경로<input value={path} maxLength={600} onChange={e=>setPath(e.target.value)} placeholder="예: backend/app/main.py"/></label>
        <button type="button" disabled={busy||!path} className="cw-small-button" onClick={()=>void tool('workspace_read_file',{path})}>코드 읽기</button>
        <button type="button" disabled={busy||!path} className="cw-small-button" onClick={()=>void tool('symbol_lookup',{path})}>함수·클래스</button></div>
      {result&&<pre className="cw-workspace-output" tabIndex={0} aria-label="도구 조회 결과">{result}</pre>}
      {workspace.allow_execution&&<form className="cw-terminal-command" onSubmit={e=>{e.preventDefault();void tool('terminal_exec',{argv:['sh','-lc',command],timeout_seconds:120}).then(value=>{if(value?.job_id){setJob(String(value.job_id));setOutput("");setJobState('running');setRefresh(r=>r+1);}});}}>
        <label className="cw-scope-field">터미널 명령<input value={command} maxLength={4000} onChange={e=>setCommand(e.target.value)} placeholder="예: cd backend && python -m pytest -q"/></label>
        <button disabled={busy||!command.trim()||jobState==='running'} className="cw-primary-button">실행</button></form>}
      {!!jobs.length&&<label className="cw-scope-field">최근 실행<select value={job??''} onChange={e=>{setJob(e.target.value||null);setOutput("");}}><option value="">실행 선택</option>{jobs.map(item=><option key={item.id} value={item.id}>{item.status} · {item.argv}</option>)}</select></label>}
      {job&&<section className="cw-terminal-output"><div><span role="status">{jobState}</span><button type="button" className="cw-text-button" onClick={()=>void tool('terminal_cancel',{job_id:job}).then(()=>setRefresh(r=>r+1))}>실행 중지</button></div>
        <pre tabIndex={0} aria-label="터미널 출력">{output||'출력을 기다리는 중…'}</pre>
        {jobState==='running'&&<form onSubmit={e=>{e.preventDefault();const form=e.currentTarget;const value=new FormData(form).get('stdin');void tool('terminal_input',{job_id:job,text:String(value)+'\n'});form.reset();}}><label className="cw-sr-only" htmlFor="terminal-stdin">프로그램 입력</label><input id="terminal-stdin" name="stdin" maxLength={4000}/><button className="cw-small-button">입력 보내기</button></form>}</section>}
      <footer className="cw-dialog-footer"><span>작업공간을 제거하면 저장소 사본과 로컬 수정 내용이 삭제됩니다.</span><button type="button" disabled={busy} className="cw-text-button" onClick={async()=>{
        setBusy(true);try{await request(`/${workspace.id}`,{method:'DELETE'});setItems(old=>old.map(i=>i.id===workspace.id?{...i,status:'closed'}:i));setJob(null);}catch(e){setError(failure(e));}finally{setBusy(false);}
      }}>작업공간 제거</button></footer></>}
  </dialog>;
}
