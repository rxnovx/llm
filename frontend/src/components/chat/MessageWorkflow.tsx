'use client';
import { useEffect,useRef,useState } from 'react';
import type { ChatMessage } from '@/types/chat';
import { workflowJson,type ForkResult } from '@/lib/workflows';
import { MessageContent } from './MessageContent';
import { WorkspaceLink } from '@/components/shared/WorkspaceLink';
import { useDialogDismiss } from '@/components/shared/useDialogDismiss';
import { AnswerFiles } from './AnswerFiles';
const phaseLabel=(phase:string)=>({queued:'모델 대기',model:'답변 작성',gitlab_resolve:'프로젝트 접근 확인',gitlab_checkout:'소스 내려받기',gitlab_read:'GitLab 파일 읽기',repository_index:'소스 색인',knowledge_search:'문서 검색',evidence_review:'근거 점검',mcp_discovery:'앱 연결 확인',create_document:'결과 파일 생성',generate_image:'이미지 생성',terminal_exec:'명령 실행',auto_summary:'대화 요약'}[phase]??(phase.startsWith('mcp_')?'연결된 앱 조회':phase?'도구 작업':'응답 생성'));
export function MessageWorkflow({message,conversationId,disabled,onResult,onFiles,onRetry,onModels,onApps}:{message:ChatMessage;conversationId:string;disabled:boolean;onResult:(r:ForkResult)=>void;onFiles:(files:NonNullable<ChatMessage['artifacts']>)=>void;onRetry?:()=>void;onModels:()=>void;onApps:()=>void}){
  const dialog=useRef<HTMLDialogElement>(null);
  const [mode,setMode]=useState(''),[text,setText]=useState(message.content),[busy,setBusy]=useState(false),[error,setError]=useState('');
  const closeDialog = () => setMode('');
  const dismiss = useDialogDismiss(closeDialog, !busy);
  const [versions,setVersions]=useState<{id:string;sequence:number;model:string;message:ChatMessage}[]>([]),[chosen,setChosen]=useState(''),[compare,setCompare]=useState('');
  const [category,setCategory]=useState('correctness'),[detail,setDetail]=useState(''),[reported,setReported]=useState(false);
  useEffect(()=>{if(mode)dialog.current?.showModal();else dialog.current?.close();},[mode]);
  const action=async(work:()=>Promise<void>)=>{setBusy(true);setError('');try{await work();}catch(e){setError(e instanceof Error?e.message:'요청을 완료하지 못했습니다.');}finally{setBusy(false);}};
  const fork=(content?:string)=>action(async()=>{const r=await workflowJson<ForkResult>(`/conversations/${conversationId}/fork`,{method:'POST',body:JSON.stringify({message_id:message.id,...(content===undefined?{}:{content})})});setMode('');onResult(r);});
  const openVersions=()=>{setMode('versions');void action(async()=>{const r=await workflowJson<{items:typeof versions}>(`/messages/${message.id}/versions`);setVersions(r.items);setChosen(message.active_version||r.items.at(-1)?.id||'');setCompare(r.items.find(v=>v.id!==message.active_version)?.id||'');});};
  const coverage=message.routing?.coverage;
  return <>
    {message.role==='assistant'&&coverage&&<details className="cw-coverage"><summary>확인한 범위 · 소스 {coverage.source_files??0}개 파일 · 문서 {coverage.documents??0}개 · 앱 조회 {coverage.app_results??0}개</summary><dl><div><dt>실제 발췌</dt><dd>{coverage.excerpts??0}개</dd></div>{coverage.repository&&<><div><dt>색인한 파일</dt><dd>{coverage.repository.indexed_files??0}개</dd></div><div><dt>정의 / import</dt><dd>{coverage.repository.symbols??0} / {coverage.repository.imports??0}</dd></div></>}</dl>{coverage.repository?.truncated&&<p>저장소 색인 한도로 일부 파일이 제외되었습니다.</p>}{coverage.issues?.length? <p>파싱·색인 주의사항 {coverage.issues.length}개 · 작업 기록에서 상세 내용을 확인하세요.</p>:null}{coverage.limitations?.map((text,i)=><p key={i}>{text}</p>)}</details>}
    {message.recovery&&<div className="cw-recovery" role="status"><strong>중단된 단계: {phaseLabel(message.recovery.phase)}</strong><p>{message.recovery.preserved_previous?'재생성이 중단되어 이전 정상 답변을 유지했습니다. ':'표시된 부분 답변은 저장했습니다. '}{message.recovery.requires_confirmation?'재시도하면 명령이나 외부 변경이 다시 실행될 수 있습니다.':'연결 상태와 자료를 확인한 뒤 다시 시도하세요.'}</p><div>{onRetry&&<button type="button" className="cw-text-button" disabled={disabled} onClick={onRetry}>다시 시도</button>}<button type="button" className="cw-text-button" onClick={onModels}>모델 변경·연결 확인</button><button type="button" className="cw-text-button" onClick={onApps}>앱·자료 확인</button></div>{['app_connection','gitlab_error'].includes(message.recovery.code)&&<p>GitLab 로그인은 <WorkspaceLink href="/user/settings/apps">계정 설정의 연결된 앱</WorkspaceLink>에서 진행하세요. 일반 MCP의 주소·인증·허용 도구는 운영자가 등록합니다.</p>}</div>}
    {message.role==='assistant'&&message.status==='complete'&&<AnswerFiles messageId={message.id} files={message.artifacts} onChange={onFiles}/>}
    {message.status!=='pending'&&<div className="cw-message-workflows">
      {message.role==='user'&&<button type="button" className="cw-text-button" disabled={disabled} onClick={()=>{setText(message.content);setError('');setMode('edit');}}>질문 수정</button>}
      <button type="button" className="cw-text-button" disabled={disabled||busy} onClick={()=>void fork()}>여기서 분기</button>
      {message.role==='assistant'&&(message.versions?.length??0)>0&&<button type="button" className="cw-text-button" disabled={disabled} onClick={openVersions}>답변 버전 {message.versions?.length}개</button>}
      {message.role==='assistant'&&<button type="button" className="cw-text-button" disabled={busy||reported} onClick={()=>{setError('');setMode('report');}}>{reported?'의견 접수됨':'오류·출처 신고'}</button>}
    </div>}
    {error&&!mode&&<p className="cw-inline-error" role="alert">{error}</p>}
    <dialog ref={dialog} className={`cw-dialog cw-workflow-dialog ${mode==='versions'?'cw-version-dialog':''}`} aria-label={mode==='edit'?'질문 수정':mode==='report'?'답변 오류 신고':'답변 버전 비교'} {...dismiss} onClose={closeDialog}>
      <header className="cw-dialog-header"><h2>{mode==='edit'?'질문 수정':mode==='report'?'답변 오류·출처 신고':'답변 버전'}</h2><button type="button" className="cw-small-button" disabled={busy} onClick={closeDialog}>닫기</button></header>
      {error&&<p className="cw-inline-error" role="alert">{error}</p>}
      {mode==='edit'&&<form onSubmit={e=>{e.preventDefault();void fork(text);}}><p>원래 대화를 남기고 이 질문부터 새 분기로 이어갑니다. 수정한 질문을 확인한 뒤 전송하세요.</p><label className="cw-sr-only" htmlFor="edited-question">수정할 질문</label><textarea id="edited-question" rows={8} value={text} onChange={e=>setText(e.target.value)} maxLength={16000}/><footer className="cw-dialog-footer"><span>쓰기 권한은 새 분기에 전달하지 않습니다.</span><button className="cw-primary-button" disabled={busy||!text.trim()}>{busy?'분기 중…':'수정한 대화 열기'}</button></footer></form>}
      {mode==='versions'&&<><p>버전을 고르면 이후 질문에서 해당 답변을 사용합니다. 뒤에 다른 메시지가 있으면 새 분기로 열립니다.</p><div className="cw-version-grid">{[['선택할 버전',chosen,setChosen],['비교할 버전',compare,setCompare]].map(([label,value,set])=><section key={String(label)}><label>{String(label)}<select value={String(value)} onChange={e=>(set as (v:string)=>void)(e.target.value)}><option value="">선택</option>{versions.map(v=><option key={v.id} value={v.id}>버전 {v.sequence} · {v.model}{v.id===message.active_version?' · 현재':''}</option>)}</select></label><div className="cw-version-body">{versions.find(v=>v.id===value)&&<MessageContent content={versions.find(v=>v.id===value)!.message.content}/>}</div></section>)}</div><footer className="cw-dialog-footer"><span>최대 20개 버전 보관</span><button className="cw-primary-button" disabled={busy||!chosen} onClick={()=>void action(async()=>{const r=await workflowJson<ForkResult>(`/messages/${message.id}/versions/${chosen}/select`,{method:'POST'});setMode('');onResult(r);})}>이 버전으로 이어가기</button></footer></>}
      {mode==='report'&&<form onSubmit={e=>{e.preventDefault();void action(async()=>{await workflowJson(`/messages/${message.id}/feedback`,{method:'POST',body:JSON.stringify({category,detail})});setReported(true);setMode('');});}}><label className="cw-scope-field">문제 유형<select value={category} onChange={e=>setCategory(e.target.value)}><option value="correctness">사실·답변 오류</option><option value="source">출처가 맞지 않음</option><option value="missing">분석 범위·내용 누락</option><option value="other">기타</option></select></label><label className="cw-scope-field">상세 설명<textarea rows={5} maxLength={1500} value={detail} onChange={e=>setDetail(e.target.value)} placeholder="어느 부분이 잘못됐는지 알려 주세요."/></label><p>이 앱 운영자에게 현재 답변 버전과 함께 접수됩니다.</p><footer className="cw-dialog-footer"><span/><button className="cw-primary-button" disabled={busy}>의견 보내기</button></footer></form>}
    </dialog>
  </>;
}
