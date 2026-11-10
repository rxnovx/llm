"use client";
import { useEffect,useState } from 'react';
import { assistantJson,type McpServer } from '@/lib/knowledge';
type Registration=McpServer&{url:string;secret_ref:string;enabled:boolean;allowed_users:string[];timeout_seconds:number;revision:number};
const blank=():Registration=>({id:'',name:'',url:'',secret_ref:'',enabled:true,read_tools:[],write_tools:[],allowed_users:[],timeout_seconds:30,revision:0});
export function McpManager(){
  const [rows,setRows]=useState<Registration[]>([]),[form,setForm]=useState(blank),[error,setError]=useState(''),[result,setResult]=useState(''),[busy,setBusy]=useState(false);
  useEffect(()=>{const abort=new AbortController();void assistantJson<{items:Registration[]}>('/mcp/servers',{signal:abort.signal}).then(r=>setRows(r.items)).catch(e=>{if(!abort.signal.aborted)setError(String(e));});
    void assistantJson<Record<string,unknown>>('/operations',{signal:abort.signal}).then(v=>setResult(JSON.stringify(v,null,2))).catch(()=>{});return()=>abort.abort();},[]);
  const list=(key:'read_tools'|'write_tools'|'allowed_users',label:string)=><label className="cw-scope-field">{label}<input value={form[key].join(',')} onChange={e=>setForm(s=>({...s,[key]:e.target.value.split(',').map(v=>v.trim())}))}/></label>;
  return <section className="cw-knowledge-section"><p className="cw-dialog-description">등록한 HTTP MCP 서버의 허용 도구를 대화에 제공합니다. 인증 키는 서버 환경의 MCP_SECRETS_JSON에 보관하고 여기에는 참조 이름만 입력합니다.</p>
    <div className="cw-workspace-actions"><button className="cw-small-button" onClick={()=>setForm(blank())}>새 MCP 서버</button>{rows.map(r=><button className="cw-small-button" key={r.id} onClick={()=>setForm(r)}>{r.name}{r.enabled?'':' · 꺼짐'}</button>)}</div>
    {error&&<p className="cw-inline-error" role="alert">{error}</p>}<form className="cw-mcp-form" onSubmit={e=>{e.preventDefault();setBusy(true);setError('');void assistantJson<Registration>(`/mcp/servers/${form.id}`,{method:'PUT',body:JSON.stringify({...form,read_tools:form.read_tools.filter(Boolean),write_tools:form.write_tools.filter(Boolean),allowed_users:form.allowed_users.filter(Boolean)})})
      .then(r=>{setForm(r);setRows(v=>[...v.filter(s=>s.id!==r.id),r]);}).catch(e=>setError(e instanceof Error?e.message:String(e))).finally(()=>setBusy(false));}}>
      <label className="cw-scope-field">서버 ID<input required pattern="[A-Za-z0-9_-]{1,40}" disabled={form.revision>0} value={form.id} onChange={e=>setForm(v=>({...v,id:e.target.value}))}/></label>
      <label className="cw-scope-field">앱 이름<input required maxLength={80} value={form.name} onChange={e=>setForm(v=>({...v,name:e.target.value}))}/></label>
      <label className="cw-scope-field">MCP 주소<input type="url" required value={form.url} onChange={e=>setForm(v=>({...v,url:e.target.value}))} placeholder="http://internal-service:8011/mcp"/></label>
      <label className="cw-scope-field">인증 키 참조<input value={form.secret_ref} onChange={e=>setForm(v=>({...v,secret_ref:e.target.value}))}/></label>
      {list('read_tools','허용할 읽기 도구 · 쉼표로 구분')}{list('write_tools','허용할 쓰기 도구 · 쉼표로 구분')}{list('allowed_users','허용할 사용자 이름 · 비우면 전체 사용자')}
      <label className="cw-knowledge-app"><input type="checkbox" checked={form.enabled} onChange={e=>setForm(v=>({...v,enabled:e.target.checked}))}/>사용</label><div className="cw-workspace-actions"><button className="cw-primary-button" disabled={busy}>서버 저장</button><button type="button" className="cw-small-button" disabled={busy||!form.id||!rows.some(s=>s.id===form.id)} onClick={()=>{setBusy(true);void assistantJson(` /mcp/servers/${form.id}/check`.trim(),{method:'POST'}).then(r=>setResult(JSON.stringify(r,null,2))).catch(e=>setError(e instanceof Error?e.message:String(e))).finally(()=>setBusy(false));}}>연결·도구 조회</button></div>
    </form>{result&&<pre className="cw-knowledge-preview" tabIndex={0}>{result}</pre>}</section>;
}
