'use client';
import { useEffect,useRef,useState } from 'react';
import { useDialogDismiss } from '@/components/shared/useDialogDismiss';
import { downloadResult,workflowJson,type ResultFile } from '@/lib/workflows';
import { AnswerImage, resultImageTypes } from './AnswerImage';
import { MessageContent } from './MessageContent';
type Preview=ResultFile&{content:string;columns:string[];rows:(string|number|boolean|null)[][];preview_truncated:boolean};
export function AnswerFiles({messageId,files=[],onChange}:{messageId:string;files?:ResultFile[];onChange:(files:ResultFile[])=>void}){
  const [formats,setFormats]=useState<string[]>([]),[format,setFormat]=useState('md'),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const [preview,setPreview]=useState<Preview|null>(null);const dialog=useRef<HTMLDialogElement>(null);
  const closePreview = () => setPreview(null);
  const dismiss = useDialogDismiss(closePreview);
  useEffect(()=>{const abort=new AbortController();void workflowJson<{formats:string[]}>('/formats',{signal:abort.signal}).then(v=>setFormats(v.formats)).catch(()=>{});return()=>abort.abort();},[]);
  useEffect(()=>{if(preview)dialog.current?.showModal();else dialog.current?.close();},[preview]);
  const action=async(work:()=>Promise<void>)=>{if(busy)return;setBusy(true);setError('');try{await work();}catch(e){setError(e instanceof Error?e.message:'파일을 처리하지 못했습니다.');}finally{setBusy(false);}};
  return <section className="cw-result-files" aria-label="결과 파일">
    {files.length > 0 && <ul>{files.map(file => {
      const mimeType = resultImageTypes.get(file.format);
      const metadata = <><strong>{file.name}</strong><small>
        {file.format.toUpperCase()} · {(file.size / 1024).toFixed(1)} KB{!mimeType && ' · 미리보기'}
      </small></>;
      return <li key={file.id} className={mimeType ? 'cw-result-image-file' : undefined}>
        {mimeType ? <>
          <AnswerImage file={file} mimeType={mimeType} />
          <div className="cw-file-preview">{metadata}</div>
        </> : <button type="button" className="cw-file-preview" disabled={busy}
          onClick={() => void action(async () => setPreview(await workflowJson<Preview>(`/artifacts/${file.id}`)))}>
          {metadata}
        </button>}
        <button type="button" className="cw-text-button" disabled={busy}
          onClick={() => void action(() => downloadResult(file))}>다운로드</button>
        <button type="button" className="cw-text-button" disabled={busy} aria-label={`${file.name} 삭제`}
          onClick={() => void action(async () => {
            await workflowJson(`/artifacts/${file.id}`, { method: 'DELETE' });
            onChange(files.filter(item => item.id !== file.id));
          })}>삭제</button>
      </li>;
    })}</ul>}
    <details className="cw-export"><summary>답변을 파일로 저장</summary><div><label className="cw-sr-only" htmlFor={`export-${messageId}`}>저장 형식</label><select id={`export-${messageId}`} value={format} onChange={e=>setFormat(e.target.value)}>{formats.map(f=><option key={f} value={f}>{f.toUpperCase()}</option>)}</select><button className="cw-small-button" disabled={busy||!formats.length} onClick={()=>void action(async()=>{const file=await workflowJson<ResultFile>(`/messages/${messageId}/export`,{method:'POST',body:JSON.stringify({format,name:'대화 결과'})});onChange([...files,file]);})}>{busy?'생성 중…':'파일 만들기'}</button></div><small>표가 있으면 CSV·XLSX에 첫 표를 저장합니다. 그 외에는 본문을 행별로 저장합니다.</small></details>
    {error&&<p className="cw-inline-error" role="alert">{error}</p>}
    <dialog ref={dialog} className="cw-dialog cw-file-dialog" aria-label="결과 파일 미리보기" {...dismiss} onClose={closePreview}>{preview&&<><header className="cw-dialog-header"><div><span className="cw-overline">결과 파일</span><h2>{preview.name}</h2></div><button className="cw-small-button" onClick={closePreview}>닫기</button></header><div className="cw-file-body">{preview.content&&(preview.format==='md'?<MessageContent content={preview.content}/>:<pre>{preview.content}</pre>)}{preview.rows.length>0&&<div className="cw-table-scroll"><table><thead><tr>{preview.columns.map((c,i)=><th key={i}>{c}</th>)}</tr></thead><tbody>{preview.rows.map((row,i)=><tr key={i}>{row.map((c,j)=><td key={j}>{c===null?'':String(c)}</td>)}</tr>)}</tbody></table></div>}{preview.preview_truncated&&<p>미리보기는 본문 20,000자·표 100행까지 표시합니다. 전체 내용은 파일을 다운로드하세요.</p>}</div><footer className="cw-dialog-footer"><small>SHA-256 {preview.sha256.slice(0,16)}…</small><button className="cw-primary-button" disabled={busy} onClick={()=>void action(()=>downloadResult(preview))}>다운로드</button></footer></>}</dialog>
  </section>;
}
