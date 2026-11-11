"use client";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { useDialogDismiss } from "@/components/shared/useDialogDismiss";
import { workflowJson } from '@/lib/workflows';
import { chatJson } from "@/lib/chat-api";
import type { ContextReport } from "@/types/chat";
import { Icon } from "./Icons";

type Props = { conversationId: string; connectionId: string; model: string; content: string;
  onClose: () => void; onSaved: (report: ContextReport) => void };
export function ContextDialog({ conversationId, connectionId, model, content, onClose, onSaved }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [report, setReport] = useState<ContextReport | null>(null);
  const [summary, setSummary] = useState("");
  const [through, setThrough] = useState(-1);
  const [saving, setSaving] = useState(false);
  const dismiss = useDialogDismiss(onClose, !saving);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => { dialog.current?.showModal(); }, []);
  useEffect(() => {
    const abort = new AbortController();
    void chatJson<ContextReport>(`/conversations/${conversationId}/context/preview`, { method: "POST",
      signal: AbortSignal.any([abort.signal, AbortSignal.timeout(10000)]), body: JSON.stringify({ connection_id: connectionId, model, content }) })
      .then((value) => { if (!abort.signal.aborted) { setReport(value); setSummary(value.summary); setThrough(value.through_position); setError(""); } })
      .catch((caught) => { if (!abort.signal.aborted) setError(caught instanceof Error ? caught.message : "문맥 정보를 불러오지 못했습니다."); });
    return () => abort.abort();
  }, [conversationId, connectionId, model, content, refresh]);
  const save = async (event: FormEvent) => {
    event.preventDefault(); if (!report || saving) return;
    setSaving(true); setError("");
    try {
      await chatJson(`/conversations/${conversationId}/context`, { method: "PATCH", signal: AbortSignal.timeout(10000),
        body: JSON.stringify({ summary, through_position: through, revision: report.revision }) });
      const updated = await chatJson<ContextReport>(`/conversations/${conversationId}/context/preview`, { method: "POST",
        signal: AbortSignal.timeout(10000), body: JSON.stringify({ connection_id: connectionId, model, content }) });
      onSaved(updated); onClose();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "요약 메모를 저장하지 못했습니다."); }
    finally { setSaving(false); }
  };
  return <dialog ref={dialog} className="cw-dialog cw-context-dialog" aria-labelledby="context-title" {...dismiss} onClose={onClose}>
    <header className="cw-dialog-header"><div><span className="cw-overline">대화 문맥</span><h2 id="context-title">모델에게 전달할 내용</h2></div>
      <button className="cw-icon-button" type="button" aria-label="문맥 설정 닫기" disabled={saving} onClick={onClose}><Icon name="close" /></button></header>
    {error && <p className="cw-inline-error" role="alert">{error}<button className="cw-text-button" disabled={saving} onClick={() => setRefresh((value) => value + 1)}>다시 불러오기</button></p>}
    {!report && !error && <p role="status">문맥 예산을 계산하는 중…</p>}
    {report && <form onSubmit={(event) => void save(event)}><dl className="cw-context-metrics"><div><dt>입력 예산</dt><dd>{report.input_budget.toLocaleString()} 토큰</dd></div>
      <div><dt>입력 추정</dt><dd>{report.estimated_input_tokens.toLocaleString()} 토큰</dd></div><div><dt>출력 예약</dt><dd>{report.reserved_output_tokens.toLocaleString()} 토큰</dd></div></dl>
      <p className="cw-context-note">UTF-8 바이트와 메시지 여유분으로 계산한 보수적 추정입니다. 실제 모델의 토큰 수와 다를 수 있습니다.</p>
      <p>이전 대화 {report.included_turns}턴 전달 · {report.omitted_turns}턴 제외 · {report.summarized_turns}턴 요약 메모로 대체</p>
      {report.too_large && <p className="cw-inline-error" role="alert">질문과 요약 메모가 입력 예산을 초과합니다. 내용을 줄이거나 더 큰 문맥의 모델을 선택하세요.</p>}
      <section className="cw-auto-summary"><label><input type="checkbox" checked={report.auto_summary_enabled??true} disabled={saving} onChange={e=>{const enabled=e.target.checked;setSaving(true);void workflowJson(`/conversations/${conversationId}/auto-summary`,{method:'PATCH',body:JSON.stringify({enabled})}).then(()=>{setReport({...report,auto_summary_enabled:enabled});onSaved({...report,auto_summary_enabled:enabled});}).catch(e=>setError(String(e))).finally(()=>setSaving(false));}}/> 긴 대화 자동 요약</label><p>대화가 길어지면 이전 내용을 요약하고 최근 원문을 유지합니다. 원래 기록은 삭제하지 않습니다.</p>{report.auto_summary&&<details><summary>자동 요약 내용 보기</summary><pre>{report.auto_summary}</pre></details>}</section>
      <label className="cw-context-label">직접 작성하는 추가 요약 메모<textarea rows={7} maxLength={6000} disabled={saving} value={summary} onChange={(event) => setSummary(event.target.value)}
        placeholder="대화의 결정 사항, 중요한 사실, 계속 지킬 조건을 직접 요약해 주세요. 비우면 요약을 사용하지 않습니다." /></label>
      <label className="cw-context-label">메모가 요약한 범위<select disabled={saving} value={through} onChange={(event) => setThrough(Number(event.target.value))}>
        <option value={-1}>원문을 제외하지 않고 메모만 추가</option>
        {report.through_position >= 0 && report.through_position !== report.last_complete_position && <option value={report.through_position}>기존에 지정한 범위까지</option>}
        {report.last_complete_position >= 0 && <option value={report.last_complete_position}>현재 완료된 마지막 답변까지</option>}</select></label>
      <p className="cw-context-note">지정한 범위의 원문 대신 메모를 다음 요청에 보냅니다. 중요한 내용이 빠지지 않았는지 확인하세요. 전체 대화 기록은 계속 볼 수 있습니다.</p>
      <footer className="cw-dialog-footer"><span>직접 작성한 메모는 자동 요약과 별도로 보관합니다.</span><button className="cw-primary-button" disabled={saving}>{saving ? "저장 중…" : "메모 저장"}</button></footer>
    </form>}
  </dialog>;
}
