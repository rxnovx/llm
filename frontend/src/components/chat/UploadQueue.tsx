'use client';
import { forwardRef,useEffect,useImperativeHandle,useRef,useState } from 'react';
import { authenticatedUpload } from '@/lib/authenticated-fetch';
import { knowledgeJson,type KnowledgeDocument } from '@/lib/knowledge';
import { workflowJson } from '@/lib/workflows';
export type UploadHandle={add:(files:File[])=>void};
type Item={id:string;file:File;imageInput:boolean;stage:'waiting'|'uploading'|'indexing'|'ready'|'error'|'cancelled';progress:number;error?:string};
type Props={conversationId?:string;baseId?:string;disabled?:boolean;onReady?:(doc:KnowledgeDocument)=>void;onBusy?:(busy:boolean)=>void;onChanged?:()=>void;accept?:string;imageInput?:boolean;imageLimit?:number;selectedImageIds?:string[]};
type UploadConfig = {enabled:boolean;max_upload_bytes:number;formats?:string[];image_formats?:string[];image_max_upload_bytes?:number};
const MiB = 1024 * 1024;
const errorText=(e:unknown)=>e instanceof Error?e.message:'첨부하지 못했습니다.';
export const UploadQueue=forwardRef<UploadHandle,Props>(function UploadQueue({conversationId,baseId,disabled,onReady,onBusy,onChanged,accept='.pdf,.docx,.xlsx,.csv,.tsv,.txt,.md,.html,.json',imageInput=false,imageLimit=4,selectedImageIds=[]},ref){
  const [config,setConfig]=useState<UploadConfig>({enabled:true,max_upload_bytes:10*MiB});
  useEffect(()=>{const abort=new AbortController();void knowledgeJson<UploadConfig>('/config',{signal:abort.signal}).then(setConfig).catch(()=>{});return()=>abort.abort();},[]);
  const [existingImages, setExistingImages] = useState<KnowledgeDocument[]>([]);
  const [existingId, setExistingId] = useState('');
  useEffect(() => {
    if (!imageInput || !conversationId || baseId) return;
    const abort = new AbortController();
    void knowledgeJson<{items:KnowledgeDocument[]}>(`/documents?conversation_id=${encodeURIComponent(conversationId)}`, {signal:abort.signal})
      .then(({items}) => {
        if (!abort.signal.aborted) setExistingImages(current => {
          const rows = [...current, ...items.filter(doc => doc.status === 'image' || /\.(png|jpe?g|webp)$/i.test(doc.filename))];
          return [...new Map(rows.map(doc => [doc.id, doc])).values()];
        });
      }).catch(() => {});
    return () => abort.abort();
  }, [imageInput, conversationId, baseId]);
  const [items,setItems]=useState<Item[]>([]),[error,setError]=useState('');
  const input=useRef<HTMLInputElement>(null),alive=useRef(true),running=useRef(new Map<string,{abort:AbortController;attempt?:string}>());
  const callbacks=useRef({onReady,onBusy,onChanged});
  useEffect(()=>{callbacks.current={onReady,onBusy,onChanged};},[onReady,onBusy,onChanged]);
  const patch=(id:string,data:Partial<Item>)=>{if(alive.current)setItems(old=>old.map(v=>v.id===id?{...v,...data}:v));};
  const enabled = imageInput ? Boolean(conversationId && !baseId) : config.enabled;
  const acceptedFormats = imageInput ? config.image_formats?.join(',') ?? '.png,.jpg,.jpeg,.webp' : config.formats?.join(',') ?? accept;
  const maxBytes = imageInput ? Math.min(config.image_max_upload_bytes ?? config.max_upload_bytes, 10 * MiB) : Math.min(config.max_upload_bytes, 20 * MiB);
  const imageKey = (doc: KnowledgeDocument) => doc.sha256 || doc.id;
  const selectedKeys = new Set(existingImages.filter(doc => selectedImageIds.includes(doc.id)).map(imageKey));
  const candidates = new Map<string, KnowledgeDocument>();
  for (const doc of existingImages) {
    const key = imageKey(doc);
    if (!selectedKeys.has(key) && !candidates.has(key)) candidates.set(key, doc);
  }
  const imageChoices = [...candidates.values()];
  const existingChoice = imageChoices.find(doc => doc.id === existingId);
  const add=(files:File[])=>{
    if(disabled||!enabled||!files.length)return;
    setError('');
    const supported=new Set(acceptedFormats.split(',').map(v=>v.toLowerCase()));
    const accepted=files.filter(file=>supported.has('.'+file.name.split('.').at(-1)?.toLowerCase())&&file.size>0&&file.size<=maxBytes);
    if(imageInput && accepted.length > imageLimit){
      setError(imageLimit === 0 ? '첨부한 이미지를 제거한 뒤 다시 선택하세요.' : `이미지는 ${imageLimit}장까지 더 첨부할 수 있습니다.`);
      return;
    }
    if(accepted.length!==files.length)setError(`빈 파일, ${maxBytes/MiB}MiB 초과 파일 또는 지원하지 않는 형식은 제외했습니다.`);
    const waiting=accepted.map(file=>({id:crypto.randomUUID(),file,imageInput,stage:'waiting' as const,progress:0}));
    setItems(old=>[...old,...waiting.slice(0,Math.max(0,20-old.length))]);
    if(files.length>20)setError('한 번에 20개까지 첨부할 수 있습니다.');
  };
  useImperativeHandle(ref,()=>({add}));
  useEffect(()=>{
    alive.current=true;
    const active=running.current;
    return()=>{alive.current=false;for(const {abort,attempt} of active.values()){abort.abort();if(attempt)void workflowJson(`/uploads/${attempt}/cancel`,{method:'POST'}).catch(()=>{});}active.clear();callbacks.current.onBusy?.(false);};
  },[]);
  useEffect(()=>{callbacks.current.onBusy?.(items.some(i=>['waiting','uploading','indexing'].includes(i.stage)));},[items]);
  useEffect(()=>{
    for(const item of items.filter(i=>i.stage==='waiting')){
      if(running.current.size>=2)break;
      if(running.current.has(item.id))continue;
      const task:{abort:AbortController;attempt?:string}={abort:new AbortController()};running.current.set(item.id,task);patch(item.id,{stage:'uploading'});
      void (async()=>{
        try{
          const attempt=await workflowJson<{id:string}>('/uploads',{method:'POST',body:JSON.stringify({filename:item.file.name,base_id:baseId||null,conversation_id:baseId?null:conversationId}),signal:task.abort.signal});
          task.attempt=attempt.id;
          const form=new FormData();form.set('file',item.file);form.set(baseId?'base_id':'conversation_id',baseId||conversationId||'');form.set('upload_id',attempt.id);
          if(item.imageInput)form.set('image_input','true');
          const doc=await authenticatedUpload<KnowledgeDocument>('/api/knowledge/documents',form,task.abort.signal,(loaded,total)=>patch(item.id,{progress:Math.round(loaded/total*100)}));
          if(item.imageInput){
            if(alive.current&&!task.abort.signal.aborted){
              setExistingImages(current => [doc, ...current.filter(item => item.id !== doc.id)]);
              patch(item.id,{stage:'ready',progress:100});callbacks.current.onReady?.(doc);callbacks.current.onChanged?.();
            }
            return;
          }
          patch(item.id,{stage:'indexing',progress:100});callbacks.current.onChanged?.();
          let current=doc;
          if(current.status==='error')current=await knowledgeJson<KnowledgeDocument>(`/documents/${doc.id}/reindex`,{method:'POST',signal:task.abort.signal});
          for(let round=0;current.status!=='ready'||current.needs_reindex;round++){
            if(current.status==='error')throw new Error(current.error||'문서 색인에 실패했습니다.');
            if(round>=240)throw new Error('색인이 오래 걸리고 있습니다. 자료 설정에서 상태를 확인하세요.');
            await new Promise<void>((resolve,reject)=>{const timer=setTimeout(()=>{task.abort.signal.removeEventListener('abort',abort);resolve();},1500);const abort=()=>{clearTimeout(timer);reject(new DOMException('업로드 취소','AbortError'));};task.abort.signal.addEventListener('abort',abort,{once:true});});
            const list=await knowledgeJson<{items:KnowledgeDocument[]}>(`/documents?${baseId?'base_id='+baseId:'conversation_id='+conversationId}`,{signal:task.abort.signal});
            const found=list.items.find(d=>d.id===doc.id);if(!found)throw new Error('첨부 파일이 삭제되었거나 만료되었습니다.');current=found;
          }
          if(alive.current&&!task.abort.signal.aborted){patch(item.id,{stage:'ready'});callbacks.current.onReady?.(current);callbacks.current.onChanged?.();}
        }catch(e){if(!task.abort.signal.aborted)patch(item.id,{stage:'error',error:errorText(e)});}
        finally{running.current.delete(item.id);if(alive.current)setItems(old=>[...old]);}
      })();
    }
  },[items,baseId,conversationId]);
  const cancel=async(item:Item)=>{
    const task=running.current.get(item.id);task?.abort.abort();patch(item.id,{stage:'cancelled'});
    if(task?.attempt){try{await workflowJson(`/uploads/${task.attempt}/cancel`,{method:'POST'});callbacks.current.onChanged?.();}catch(e){setError('전송은 중지했지만 서버 첨부 취소를 확인하지 못했습니다. 자료 설정에서 확인하세요. '+errorText(e));}}
  };
  return <div className="cw-upload-queue" onDragOver={e=>{if(e.dataTransfer.types.includes('Files'))e.preventDefault();}} onDrop={e=>{e.preventDefault();e.stopPropagation();add(Array.from(e.dataTransfer.files));}}>
    <button type="button" className="cw-text-button" disabled={disabled||!enabled||imageInput&&imageLimit===0} onClick={()=>input.current?.click()}>{imageInput?'＋ 이미지 선택':'＋ 파일 첨부'} <small>{imageInput?'PNG·JPEG·WebP':'여러 파일 선택·끌어놓기'}</small></button>
    <input ref={input} type="file" hidden multiple={!imageInput||imageLimit>1} accept={acceptedFormats} onChange={e=>{add(Array.from(e.target.files??[]));e.target.value='';}}/>
    {imageInput && imageChoices.length > 0 && <div className="cw-existing-images">
      <select className="cw-context-button" aria-label="기존 이미지" value={existingChoice?.id ?? ''} disabled={disabled || imageLimit === 0}
        onChange={event => setExistingId(event.target.value)}>
        <option value="">이 대화의 이미지 선택</option>
        {imageChoices.map(doc => <option key={doc.id} value={doc.id}>{doc.filename}</option>)}
      </select>
      <button type="button" className="cw-small-button" disabled={disabled || imageLimit === 0 || !existingChoice}
        onClick={() => {
          if (!existingChoice) return;
          onReady?.(existingChoice);
          setExistingId('');
        }}>이미지 사용</button>
    </div>}
    {!enabled&&!imageInput&&<p>운영자가 파일·지식 기능을 활성화해야 첨부할 수 있습니다.</p>}
    {error&&<p className="cw-inline-error" role="alert">{error}</p>}
    {items.length>0&&<ul aria-label="첨부 진행 상황">{items.map(item=><li key={item.id}><div><strong>{item.file.name}</strong><small>{item.stage==='waiting'?'전송 대기':item.stage==='uploading'?item.progress===100?'서버에서 파일 확인 중…':`전송 ${item.progress}%`:item.stage==='indexing'?'문서 색인 중…':item.stage==='ready'?(item.imageInput?'이미지 첨부됨':'검색 준비됨'):item.stage==='cancelled'?'취소됨':item.error}</small></div>
      {item.stage==='uploading'&&<progress max={100} value={item.progress} aria-label={`${item.file.name} 전송률`}/>}
      {['waiting','uploading','indexing'].includes(item.stage)&&<button type="button" onClick={()=>void cancel(item)}>취소</button>}
      {item.stage==='error'&&<button type="button" disabled={disabled} onClick={()=>patch(item.id,{stage:'waiting',error:undefined,progress:0})}>재시도</button>}
      {['ready','cancelled','error'].includes(item.stage)&&<button type="button" aria-label={`${item.file.name} 진행 기록 닫기`} onClick={()=>setItems(old=>old.filter(v=>v.id!==item.id))}>×</button>}
    </li>)}</ul>}
  </div>;
});
