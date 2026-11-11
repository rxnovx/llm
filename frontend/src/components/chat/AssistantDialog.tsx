"use client";
import { useEffect,useRef,useState } from 'react';
import { useDialogDismiss } from '@/components/shared/useDialogDismiss';
import { assistantJson,knowledgeJson,type AssistantSelection,type KnowledgeBase,type KnowledgeDocument,type McpServer } from '@/lib/knowledge';
import { UploadQueue } from './UploadQueue';
import { McpManager } from './McpManager';

type Preferences={instructions:string;memory_enabled:boolean;revision:number};
const failure=(e:unknown)=>e instanceof Error?e.message:'요청을 완료하지 못했습니다.';
const toggle=(items:string[],id:string)=>items.includes(id)?items.filter(v=>v!==id):[...items,id];
export function AssistantDialog({conversationId,value,onSelect,onClose,admin}:{conversationId:string;value:AssistantSelection;onSelect:(value:AssistantSelection)=>void;onClose:()=>void;admin:boolean}) {
  const ref=useRef<HTMLDialogElement>(null);
  const [tab,setTab]=useState('knowledge'),[selection,setSelection]=useState(value);
  const [bases,setBases]=useState<KnowledgeBase[]>([]),[base,setBase]=useState(''),[documents,setDocuments]=useState<KnowledgeDocument[]>([]);
  const [servers,setServers]=useState<McpServer[]>([]),[error,setError]=useState(''),[busy,setBusy]=useState(false),[uploading,setUploading]=useState(false),[reload,setReload]=useState(0);
  const dismiss = useDialogDismiss(onClose, !uploading);
  const [name,setName]=useState(''),[mode,setMode]=useState(''),[formats,setFormats]=useState('.pdf,.docx,.xlsx,.csv,.tsv,.txt,.md,.html,.json');
  const [preferences,setPreferences]=useState<Preferences>({instructions:'',memory_enabled:false,revision:0});
  const [memories,setMemories]=useState<{id:string;content:string}[]>([]),[memory,setMemory]=useState('');
  const [members,setMembers]=useState<{id:string;username:string}[]>([]),[username,setUsername]=useState('');
  const owned=bases.find(b=>b.id===base)?.owned??!base;
  useEffect(()=>{const element=ref.current!;element.showModal();const abort=new AbortController();
    void Promise.all([knowledgeJson<{items:KnowledgeBase[]}>('/bases',{signal:abort.signal}),assistantJson<{servers:McpServer[]}>('/catalog',{signal:abort.signal}),
      assistantJson<Preferences>('/preferences',{signal:abort.signal}),assistantJson<{items:{id:string;content:string}[]}>('/memories',{signal:abort.signal}),
      knowledgeJson<{search_mode:string;formats:string[]}>('/config',{signal:abort.signal})]).then(([b,s,p,m,c])=>{setBases(b.items);setServers(s.servers);setPreferences(p);setMemories(m.items);setMode(c.search_mode);setFormats(c.formats.join(','));})
      .catch(e=>{if(!abort.signal.aborted)setError(failure(e));});
    return()=>{abort.abort();element.close();};
  },[]);
  useEffect(()=>{
    const abort=new AbortController();let pending=false;
    const load=()=>{if(pending)return;pending=true;void knowledgeJson<{items:KnowledgeDocument[]}>(`/documents?${base?'base_id='+base:'conversation_id='+conversationId}`,{signal:abort.signal})
      .then(d=>setDocuments(d.items)).catch(e=>{if(!abort.signal.aborted)setError(failure(e));}).finally(()=>{pending=false;});};
    load();const timer=setInterval(load,1500);return()=>{abort.abort();clearInterval(timer);};
  },[base,conversationId,reload]);
  useEffect(()=>{
    if(!base||!owned)return;
    const abort=new AbortController();void knowledgeJson<{items:{id:string;username:string}[]}>(`/bases/${base}/members`,{signal:abort.signal}).then(r=>setMembers(r.items)).catch(e=>{if(!abort.signal.aborted)setError(failure(e));});
    return()=>abort.abort();
  },[base,owned,reload]);
  async function action(work:()=>Promise<void>){setBusy(true);setError('');try{await work();}catch(e){setError(failure(e));}finally{setBusy(false);}}
  const pending=documents.some(d=>(selection.document_ids.includes(d.id)||!!base&&selection.base_ids.includes(base))&&(d.status!=='ready'||d.needs_reindex));
  return <dialog ref={ref} className="cw-dialog cw-knowledge-dialog" aria-labelledby="assistant-dialog-title" {...dismiss}>
    <header className="cw-dialog-header"><div><span className="cw-overline">대화 도구</span><h2 id="assistant-dialog-title">자료·앱·개인 설정</h2></div><button className="cw-small-button" disabled={uploading} onClick={onClose}>닫기</button></header>
    <section className="cw-knowledge-section" aria-label="자동 처리 설정">
      <label className="cw-knowledge-app"><input type="checkbox" checked={selection.automatic} onChange={e=>setSelection(v=>({...v,automatic:e.target.checked}))}/><span>질문에 따라 자동으로 자료·연결된 앱 사용<small>연결된 내 계정과 접근 가능한 자료를 사용합니다. 앱 자동 선택과 GitLab 자동 분석은 읽기 전용입니다.</small></span></label>
      <label className="cw-knowledge-app">
        <input type="checkbox" checked={selection.parallel_agents}
          onChange={event => setSelection(value => ({ ...value, parallel_agents: event.target.checked }))} />
        <span>병렬 Agent 분석<small>모델이 독립 질문 2~3개를 나누어 검토하고 결과를 합칩니다.</small></span>
      </label>
      <label className="cw-scope-field">자동 문서 검색<select value={selection.rag_mode} disabled={!selection.automatic} onChange={e=>setSelection(v=>({...v,rag_mode:e.target.value as AssistantSelection['rag_mode']}))}>
        <option value="auto">필요할 때 검색 · 근거 부족 시 재검색</option><option value="always">답변 전에 항상 검색</option><option value="off">자동 문서 검색 끄기</option>
      </select></label><p className="cw-dialog-description">자료를 직접 선택하면 해당 범위를 우선 사용합니다. 자동 검색은 색인이 준비된 내 자료·공유받은 자료와 현재 대화 첨부를 대상으로 합니다. 근거 점검은 오류 가능성을 완전히 없애지는 못합니다.</p>
    </section>
    <nav className="cw-knowledge-tabs" aria-label="설정 종류">{[['knowledge','파일·지식'],['apps','앱'],['memory','맞춤 설정'],...(admin?[['operations','운영']]:[])].map(([id,label])=><button key={id} className={tab===id?'is-active':''} onClick={()=>setTab(id)} aria-pressed={tab===id}>{label}</button>)}</nav>
    {error&&<p className="cw-inline-error" role="alert">{error}</p>}
    {tab==='knowledge'&&<section className="cw-knowledge-section">
      <p className="cw-dialog-description">파일을 이 대화에 첨부하거나, 지식베이스에 모아 여러 대화에서 활용하세요. 현재 검색: {mode==='hybrid'?'키워드 + 의미 검색':'키워드 검색'}.</p>
      <div className="cw-knowledge-base-list"><label className="cw-scope-field">자료 위치<select value={base} disabled={uploading} onChange={e=>setBase(e.target.value)}><option value="">현재 대화 첨부 · 24시간</option>{bases.map(b=><option key={b.id} value={b.id}>{b.name}{b.owned?'':' · 공유받음'}</option>)}</select></label>
      </div><UploadQueue key={base||conversationId} baseId={base||undefined} conversationId={base?undefined:conversationId} disabled={busy||!owned} accept={formats} onBusy={setUploading} onChanged={()=>setReload(v=>v+1)} onReady={doc=>{if(!base)setSelection(s=>({...s,document_ids:[...new Set([...s.document_ids,doc.id])].slice(0,10)}));}}/>
      {busy&&<p role="status">자료를 처리하고 있습니다…</p>}
      <form className="cw-knowledge-inline" onSubmit={e=>{e.preventDefault();void action(async()=>{const created=await knowledgeJson<KnowledgeBase>('/bases',{method:'POST',body:JSON.stringify({name})});setBases(b=>[...b,created]);setBase(created.id);setName('');});}}><input aria-label="새 지식베이스 이름" placeholder="새 지식베이스 이름" value={name} onChange={e=>setName(e.target.value)} maxLength={120}/><button className="cw-small-button" disabled={busy||!name.trim()||bases.filter(b=>b.owned).length>=20}>만들기</button></form>
      <div className="cw-knowledge-choices" aria-label="대화에서 사용할 지식베이스">{bases.map(b=><label key={b.id}><input type="checkbox" checked={selection.base_ids.includes(b.id)} disabled={!selection.base_ids.includes(b.id)&&selection.base_ids.length>=5} onChange={()=>setSelection(s=>({...s,base_ids:toggle(s.base_ids,b.id)}))}/>{b.name}</label>)}</div>
      <div className="cw-knowledge-documents">{!documents.length&&<p className="cw-dialog-description">등록된 자료가 없습니다.</p>}{documents.map(d=><div key={d.id} className="cw-knowledge-document"><div>{!base&&d.status!=='image'&&<input type="checkbox" aria-label={`${d.filename} 첨부 선택`} checked={selection.document_ids.includes(d.id)} disabled={!selection.document_ids.includes(d.id)&&selection.document_ids.length>=10} onChange={()=>setSelection(s=>({...s,document_ids:toggle(s.document_ids,d.id)}))}/>}<strong>{d.filename}</strong><small>{d.status==='image'?'원본 이미지':d.status==='ready'?`${d.chunk_count}개 구간 · 검색 준비됨`:d.status==='error'?'색인 실패':d.status==='indexing'?'내용을 색인하는 중…':'색인 대기 중'}{d.needs_reindex?' · 재색인 필요':''}</small>{d.error&&<p role="alert">{d.error}</p>}</div>
        {owned&&<div>{d.status!=='image'&&<button className="cw-text-button" disabled={busy||d.status==='indexing'} onClick={()=>void action(async()=>{await knowledgeJson(`/documents/${d.id}/reindex`,{method:'POST'});setReload(v=>v+1);})}>재색인</button>}<button className="cw-text-button" disabled={busy} onClick={()=>void action(async()=>{await knowledgeJson(`/documents/${d.id}`,{method:'DELETE'});setSelection(s=>({...s,document_ids:s.document_ids.filter(id=>id!==d.id)}));setReload(v=>v+1);})}>삭제</button></div>}</div>)}</div>
      {base&&owned&&<details className="cw-knowledge-sharing"><summary>공유 및 관리</summary><p>등록한 사용자에게 자료 검색·읽기를 허용합니다.</p><form className="cw-knowledge-inline" onSubmit={e=>{e.preventDefault();void action(async()=>{await knowledgeJson(`/bases/${base}/members`,{method:'POST',body:JSON.stringify({username})});setUsername('');setReload(v=>v+1);});}}><input aria-label="공유할 사용자 이름" value={username} onChange={e=>setUsername(e.target.value)} placeholder="사용자 이름"/><button disabled={busy||!username.trim()} className="cw-small-button">공유</button></form>{members.map(m=><p key={m.id}>{m.username} <button className="cw-text-button" onClick={()=>void action(async()=>{await knowledgeJson(`/bases/${base}/members/${m.id}`,{method:'DELETE'});setReload(v=>v+1);})}>공유 해제</button></p>)}<button className="cw-text-button" disabled={busy} onClick={()=>void action(async()=>{await knowledgeJson(`/bases/${base}`,{method:'DELETE'});setBases(b=>b.filter(v=>v.id!==base));setSelection(s=>({...s,base_ids:s.base_ids.filter(id=>id!==base)}));setBase('');})}>이 지식베이스와 자료 삭제</button></details>}
    </section>}
    {tab==='apps'&&<section className="cw-knowledge-section"><p className="cw-dialog-description">이 대화에서 사용할 앱을 선택하세요. 모델이 질문에 필요한 도구를 호출합니다.</p>{!servers.length&&<p>운영자가 등록한 MCP 앱이 없습니다.</p>}{servers.map(s=><label className="cw-knowledge-app" key={s.id}><input type="checkbox" checked={selection.mcp_ids.includes(s.id)} disabled={!selection.mcp_ids.includes(s.id)&&selection.mcp_ids.length>=5} onChange={()=>setSelection(v=>({...v,mcp_ids:toggle(v.mcp_ids,s.id)}))}/><span><strong>{s.name}</strong><small>읽기 {s.read_tools.length}개 · 쓰기 {s.write_tools.length}개</small></span></label>)}<label className="cw-knowledge-app"><input type="checkbox" checked={selection.allow_writes} onChange={e=>setSelection(v=>({...v,allow_writes:e.target.checked}))}/><span>선택한 앱의 쓰기 도구 허용<small>외부 서비스에 변경이 발생할 수 있습니다. 기본값은 읽기 전용입니다.</small></span></label></section>}
    {tab==='memory'&&<section className="cw-knowledge-section"><form onSubmit={e=>{e.preventDefault();void action(async()=>setPreferences(await assistantJson<Preferences>('/preferences',{method:'PUT',body:JSON.stringify(preferences)})));}}><label className="cw-scope-field">답변 방식과 내 정보<textarea rows={5} maxLength={1800} value={preferences.instructions} onChange={e=>setPreferences(p=>({...p,instructions:e.target.value}))} placeholder="예: 한국어로 간결하게 답변하고, 예시는 Python을 사용해 주세요."/></label><label className="cw-knowledge-app"><input type="checkbox" checked={preferences.memory_enabled} onChange={e=>setPreferences(p=>({...p,memory_enabled:e.target.checked}))}/>저장한 기억을 대화에서 사용</label><button className="cw-small-button" disabled={busy}>맞춤 설정 저장</button></form>
      <p className="cw-dialog-description">기억은 직접 등록한 내용만 저장합니다. 사용을 꺼도 목록은 남으며 개별 삭제할 수 있습니다.</p><form className="cw-knowledge-inline" onSubmit={e=>{e.preventDefault();void action(async()=>{const row=await assistantJson<{id:string;content:string}>('/memories',{method:'POST',body:JSON.stringify({content:memory})});setMemories(m=>[...m,row]);setMemory('');});}}><input aria-label="저장할 기억" maxLength={600} placeholder="기억해 둘 내용" value={memory} onChange={e=>setMemory(e.target.value)}/><button className="cw-small-button" disabled={busy||!memory.trim()}>기억 저장</button></form>{memories.map(m=><div className="cw-knowledge-document" key={m.id}><p>{m.content}</p><button className="cw-text-button" onClick={()=>void action(async()=>{await assistantJson(`/memories/${m.id}`,{method:'DELETE'});setMemories(v=>v.filter(x=>x.id!==m.id));})}>삭제</button></div>)}</section>}
    {tab==='operations'&&admin&&<McpManager/>}
    <footer className="cw-dialog-footer"><span>지식 {selection.base_ids.length}개 · 첨부 {selection.document_ids.length}개 · 앱 {selection.mcp_ids.length}개{pending?' · 색인 완료 후 적용할 수 있습니다.':''}</span><button className="cw-primary-button" disabled={busy||uploading||pending} onClick={()=>void action(async()=>{
      const scopes=await Promise.all(selection.base_ids.map(id=>knowledgeJson<{items:KnowledgeDocument[]}>(`/documents?base_id=${id}`)));
      if(scopes.some(scope=>scope.items.some(d=>d.status!=='ready'||d.needs_reindex)))throw new Error('선택한 지식베이스의 색인이 준비되지 않았습니다. 자료 위치를 선택해 상태를 확인하세요.');
      onSelect(selection);
    })}>대화에 적용</button></footer>
  </dialog>;
}
