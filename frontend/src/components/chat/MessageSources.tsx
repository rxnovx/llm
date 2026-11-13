"use client";
import { useEffect,useRef,useState,type MouseEvent } from 'react';
import { useDialogDismiss } from '@/components/shared/useDialogDismiss';
import { WorkspaceLink } from '@/components/shared/WorkspaceLink';
import type { MessageSource, TableAnalysisSource } from '@/types/chat';
import type { KnowledgeSource } from '@/lib/knowledge';
import { sourceWebUrl } from '@/lib/chat-stream';
import { conversationPath,normalizeConversationId } from '@/lib/workspace-navigation';
import { workflowJson,type McpSource } from '@/lib/workflows';
import { knowledgeJson } from '@/lib/knowledge';
function SourceDialog({source,onClose}:{source:KnowledgeSource;onClose:()=>void}) {
  const ref=useRef<HTMLDialogElement>(null);const [text,setText]=useState('불러오는 중…');
  const dismiss = useDialogDismiss(onClose);
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
  return <dialog ref={ref} className="cw-dialog cw-knowledge-dialog" aria-label="답변 출처" {...dismiss}><header className="cw-dialog-header"><div><h2>{source.filename}</h2><p>{source.location} · 버전 {source.revision}</p></div><button className="cw-small-button" onClick={onClose}>닫기</button></header><pre className="cw-knowledge-preview" tabIndex={0}>{text}</pre></dialog>;
}
function McpEvidenceDialog({source,onClose}:{source:McpSource;onClose:()=>void}){
  const ref=useRef<HTMLDialogElement>(null),[text,setText]=useState('불러오는 중…');
  const dismiss = useDialogDismiss(onClose);
  useEffect(()=>{ref.current?.showModal();const abort=new AbortController();void workflowJson<{content:string}>(`/evidence/${source.evidence_id}`,{signal:abort.signal}).then(v=>setText(v.content)).catch(e=>{if(!abort.signal.aborted)setText(String(e));});return()=>abort.abort();},[source]);
  return <dialog ref={ref} className="cw-dialog cw-knowledge-dialog" aria-label="앱 조회 근거" {...dismiss}><header className="cw-dialog-header"><h2>{source.server} · {source.tool}</h2><button className="cw-small-button" onClick={onClose}>닫기</button></header>{source.truncated&&<p>저장 한도로 일부 결과를 생략했습니다.</p>}<pre className="cw-knowledge-preview" tabIndex={0}>{text}</pre></dialog>;
}

function TableEvidenceDialog({ source, onClose }: { source: TableAnalysisSource; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  const dismiss = useDialogDismiss(onClose);
  useEffect(() => { ref.current?.showModal(); }, []);
  return <dialog ref={ref} className="cw-dialog cw-knowledge-dialog" aria-label="첨부 데이터 분석 근거" {...dismiss}>
    <header className="cw-dialog-header"><h2>{source.title}</h2><button type="button" className="cw-small-button" onClick={onClose}>닫기</button></header>
    <ul className="cw-table-source-files">{source.documents.map(document => <li key={`${document.document_id}:${document.sheet ?? ''}`}>
      {document.filename}{document.sheet && ` · ${document.sheet}`} · 버전 {document.revision}
    </li>)}</ul>
    <pre className="cw-knowledge-preview" tabIndex={0}>{source.excerpt}</pre>
  </dialog>;
}

type Props = {
  sources: MessageSource[];
  onOpenConversation: (id: string, messageId: string) => void;
};

export function MessageSources({ sources, onOpenConversation }: Props) {
  const [opened, setOpened] = useState<KnowledgeSource | McpSource | TableAnalysisSource | null>(null);
  if (!sources.length) return null;
  const openHistorySource = (event: MouseEvent<HTMLAnchorElement>, id: string, messageId: string) => {
    if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    onOpenConversation(id, messageId);
  };

  return <>
    <aside className="cw-message-sources" aria-label="답변 출처">
      <span>참고한 자료</span>
      <div>{sources.map(source => {
        if (source.app === 'knowledge') return <button key={source.id} type="button" className="cw-knowledge-source" onClick={() => setOpened(source)}>
          <strong>[{source.id}] {source.filename}</strong><small>{source.location} · 버전 {source.revision}</small>
        </button>;
        if (source.app === 'mcp') return <button key={source.id} type="button" className="cw-knowledge-source" onClick={() => setOpened(source)}>
          <strong>[{source.id}] {source.server}</strong><small>{source.tool} · 조회 근거</small>
        </button>;
        if (source.app === 'table_analysis') return <button key={source.id} type="button" className="cw-knowledge-source" onClick={() => setOpened(source)}>
          <strong>[{source.id}] {source.title}</strong><small>{source.documents.map(document => document.filename).join(' · ')}</small>
        </button>;
        if (source.app === 'web') {
          const url = sourceWebUrl(source.url);
          if (!url) return null;
          return <a key={source.id} href={url} target="_blank" rel="noopener noreferrer">
            <strong>[{source.id}] {source.title}</strong><small>{new URL(url).hostname}</small>
            {source.excerpt && <small className="cw-source-excerpt">{source.excerpt}</small>}
          </a>;
        }
        if (source.app === 'chat_history') {
          const id = normalizeConversationId(source.conversation_id);
          const messageId = normalizeConversationId(source.message_id);
          if (!id || !messageId) return null;
          return <WorkspaceLink key={source.id} href={conversationPath(id)} onClick={event => openHistorySource(event, id, messageId)}>
            <strong>[{source.id}] {source.title}</strong><small>이전 대화</small>
            {source.excerpt && <small className="cw-source-excerpt">{source.excerpt}</small>}
          </WorkspaceLink>;
        }
        return <a key={source.id} href={sourceWebUrl(source.url)} target="_blank" rel="noopener noreferrer">
          <strong>[{source.id}] {source.path}</strong>
          <small>{source.project} · {source.start_line}–{source.end_line}줄 · {source.working_copy ? '수정된 작업 사본 · 원본 ' : ''}{source.commit.slice(0, 8)}</small>
        </a>;
      })}</div>
    </aside>
    {opened?.app === 'mcp' && <McpEvidenceDialog source={opened} onClose={() => setOpened(null)} />}
    {opened?.app === 'knowledge' && <SourceDialog source={opened} onClose={() => setOpened(null)} />}
    {opened?.app === 'table_analysis' && <TableEvidenceDialog source={opened} onClose={() => setOpened(null)} />}
  </>;
}
