"use client";
import { useEffect,useRef,useState } from 'react';
import type { MessageSource } from '@/types/chat';
import type { KnowledgeSource } from '@/lib/knowledge';
import { workflowJson,type McpSource } from '@/lib/workflows';
import { knowledgeJson } from '@/lib/knowledge';
function SourceDialog({source,onClose}:{source:KnowledgeSource;onClose:()=>void}) {
  const ref=useRef<HTMLDialogElement>(null);const [text,setText]=useState('불러오는 중…');
  useEffect(()=>{const element=ref.current!;element.showModal();const abort=new AbortController();
    void knowledgeJson<{document:{revision:number;sha256:string};items:{id:string;content:string;location:string}[]}>(`/documents/${source.document_id}/chunks?offset=0`,{signal:abort.signal})
      .then(async value=>{
        if(value.document.sha256!==source.sha256||value.document.revision!==source.revision){setText('문서가 다시 색인되었습니다. 아래 출처는 이전 버전의 기록입니다.');return;}
        let found=value.items.find(c=>c.id===source.chunk_id);
        // The dedicated chunk endpoint avoids downloading the rest of a large document.
        if(!found){const part=await knowledgeJson<{content:string}>(`/documents/${source.document_id}/chunks/${source.chunk_id}`,{signal:abort.signal});found={id:source.chunk_id,content:part.content,location:source.location};}
        setText(found.content);
      }).catch(e=>{if(!abort.signal.aborted)setText(e instanceof Error?e.message:'문서를 열 수 없습니다.');});
    return()=>{abort.abort();element.close();};
  },[source]);
  return <dialog ref={ref} className="cw-dialog cw-knowledge-dialog" onCancel={e=>{e.preventDefault();onClose();}} aria-label="답변 출처"><header className="cw-dialog-header"><div><h2>{source.filename}</h2><p>{source.location} · 버전 {source.revision}</p></div><button className="cw-small-button" onClick={onClose}>닫기</button></header><pre className="cw-knowledge-preview" tabIndex={0}>{text}</pre></dialog>;
}
function McpEvidenceDialog({source,onClose}:{source:McpSource;onClose:()=>void}){
  const ref=useRef<HTMLDialogElement>(null),[text,setText]=useState('불러오는 중…');
  useEffect(()=>{ref.current?.showModal();const abort=new AbortController();void workflowJson<{content:string}>(`/evidence/${source.evidence_id}`,{signal:abort.signal}).then(v=>setText(v.content)).catch(e=>{if(!abort.signal.aborted)setText(String(e));});return()=>abort.abort();},[source]);
  return <dialog ref={ref} className="cw-dialog cw-knowledge-dialog" aria-label="앱 조회 근거" onCancel={e=>{e.preventDefault();onClose();}}><header className="cw-dialog-header"><h2>{source.server} · {source.tool}</h2><button className="cw-small-button" onClick={onClose}>닫기</button></header>{source.truncated&&<p>저장 한도로 일부 결과를 생략했습니다.</p>}<pre className="cw-knowledge-preview" tabIndex={0}>{text}</pre></dialog>;
}
export function MessageSources({ sources }: { sources: MessageSource[] }) {
  const [opened,setOpened]=useState<KnowledgeSource|McpSource|null>(null);
  if (!sources.length) return null;
  return <><aside className="cw-message-sources" aria-label="답변 출처"><span>참고한 자료</span><div>{sources.map((source) =>source.app==='knowledge'
    ? <button key={source.id} className="cw-knowledge-source" onClick={()=>setOpened(source)}><strong>[{source.id}] {source.filename}</strong><small>{source.location} · 버전 {source.revision}</small></button>
    : source.app==='mcp'?<button key={source.id} className="cw-knowledge-source" onClick={()=>setOpened(source)}><strong>[{source.id}] {source.server}</strong><small>{source.tool} · 조회 근거</small></button>
    : <a key={source.id} href={/^https?:\/\//i.test(source.url) ? source.url : undefined} target="_blank" rel="noopener noreferrer"><strong>[{source.id}] {source.path}</strong><small>{source.project} · {source.start_line}–{source.end_line}줄 · {source.working_copy?'수정된 작업 사본 · 원본 ':''}{source.commit.slice(0, 8)}</small></a>)}</div></aside>{opened&&(opened.app==='mcp'?<McpEvidenceDialog source={opened} onClose={()=>setOpened(null)}/>:<SourceDialog source={opened} onClose={()=>setOpened(null)}/>)}</>;
}
