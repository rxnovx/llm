"use client";

import { useEffect, useRef, useState } from "react";
import { authenticatedFetch, ApiRequestError } from "@/lib/authenticated-fetch";
import type { CapabilityName, CapabilityReport } from "@/types/admin";
import type { ModelProfile } from "@/types/chat";
import { elapsedLabel } from "@/lib/generation-trace";

const checks: [CapabilityName, string][] = [["chat", "한국어 채팅"], ["streaming", "스트리밍"], ["tools", "도구 호출"],
  ["structured_output", "구조화 출력"], ["reasoning", "추론 출력"], ["vision", "이미지 입력"], ["context", "문맥 입력"], ["concurrency", "동시 요청"]];
const statuses = { verified: "검증됨", failed: "실패", unavailable: "검사 불가", unverified: "미검증", limited: "제한적으로 확인" };

export function CapabilityDiagnostics({ connection, onForbidden, onClose }: {
  connection: { id: string; name: string; model: string; models: ModelProfile[] };
  onForbidden: () => void; onClose: () => void;
}) {
  const [model, setModel] = useState(connection.model || connection.models[0]?.name || "");
  const [selected, setSelected] = useState<CapabilityName[]>(["streaming", "tools", "structured_output", "reasoning"]);
  const [contextTokens, setContextTokens] = useState(512);
  const [report, setReport] = useState<CapabilityReport | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => {
    if (!model.trim()) return;
    const abort = new AbortController();
    void authenticatedFetch(`/api/admin/connections/${connection.id}/capabilities?model=${encodeURIComponent(model)}`, {
      signal: AbortSignal.any([abort.signal, AbortSignal.timeout(15000)]),
    }).then(response => response.json()).then((result: CapabilityReport) => {
      if (!abort.signal.aborted) { setReport(result); setError(""); }
    }).catch(caught => {
      if (abort.signal.aborted) return;
      if (caught instanceof ApiRequestError && caught.status === 403) onForbidden();
      else setError(caught instanceof Error ? caught.message : "이전 기능 검사 결과를 불러오지 못했습니다.");
    });
    return () => abort.abort();
  }, [connection.id, model, onForbidden]);

  const probe = async () => {
    if (busy || !model.trim() || !selected.length) return;
    const abort = new AbortController(); controller.current = abort;
    setBusy(true); setError("");
    try {
      const response = await authenticatedFetch(`/api/admin/connections/${connection.id}/capabilities/probe`, {
        method: "POST", body: JSON.stringify({ model: model.trim(), checks: selected, context_tokens: contextTokens }),
        signal: AbortSignal.any([abort.signal, AbortSignal.timeout(selected.length * 50_000 + 15_000)]),
      });
      const result: CapabilityReport = await response.json();
      if (!abort.signal.aborted) setReport(result);
    } catch (caught) {
      if (abort.signal.aborted) return;
      if (caught instanceof ApiRequestError && caught.status === 403) onForbidden();
      else setError(caught instanceof Error ? caught.message : "모델 기능 검사를 완료하지 못했습니다.");
    } finally { if (controller.current === abort) { controller.current = null; setBusy(false); } }
  };
  const refresh = async () => {
    if (loading || busy || !model.trim()) return;
    setLoading(true); setError("");
    try {
      const response = await authenticatedFetch(`/api/admin/connections/${connection.id}/capabilities?model=${encodeURIComponent(model)}`, { signal: AbortSignal.timeout(15000) });
      setReport(await response.json());
    } catch (caught) {
      if (caught instanceof ApiRequestError && caught.status === 403) onForbidden();
      else setError(caught instanceof Error ? caught.message : "검사 이력을 불러오지 못했습니다.");
    } finally { setLoading(false); }
  };
  const validReport = report?.model === model.trim() ? report : null;
  return <section className="ops-capability-diagnostics" aria-label={`${connection.name} 모델 기능 진단`}>
    <header><div><h3>{connection.name} · 모델 기능 진단</h3><p>선택한 검사는 실제 모델을 호출합니다. 검사 결과가 모델 설정을 자동으로 바꾸지는 않습니다.</p></div>
      <button type="button" className="ops-button" onClick={onClose}>진단 닫기</button></header>
    <fieldset disabled={busy}><label>검사할 모델 ID{connection.models.length ? <select value={model} onChange={event => setModel(event.target.value)}>
      {connection.models.map(item => <option key={item.name} value={item.name}>{item.name}</option>)}</select>
      : <input maxLength={200} value={model} onChange={event => setModel(event.target.value)} placeholder="실제 서빙 모델 ID" />}</label>
      <div className="ops-capability-checks">{checks.map(([id, label]) => <label key={id}><input type="checkbox" checked={selected.includes(id)}
        onChange={event => setSelected(values => event.target.checked ? [...values, id] : values.filter(value => value !== id))} />{label}</label>)}</div>
      {selected.includes("context") && <label>문맥 검사 입력 한도<input type="number" min={128} max={4096} value={contextTokens}
        onChange={event => setContextTokens(Number(event.target.value))} /></label>}
    </fieldset>
    <div className="ops-actions"><button type="button" className="ops-button" disabled={busy || !selected.length || !model.trim()}
      onClick={() => void probe()}>{busy ? "실제 모델 검사 중…" : "선택한 기능 검사"}</button>
      <button type="button" className="ops-button" disabled={busy || loading || !model.trim()} onClick={() => void refresh()}>{loading ? "불러오는 중…" : "저장된 결과 새로고침"}</button></div>
    {busy && <p role="status">{selected.map(id => checks.find(([name]) => name === id)?.[1]).join(" · ")} 검사 중입니다. 서버 대기 시간에 따라 시간이 걸릴 수 있습니다.</p>}
    {error && <p className="ops-error" role="alert">{error}</p>}
    {validReport?.items.length ? <div className="ops-table-scroll"><table><thead><tr><th>기능</th><th>실제 검사 결과</th><th>소요 시간</th><th>확인 내용</th></tr></thead>
      <tbody>{validReport.items.map(item => <tr key={item.capability}><td>{checks.find(([name]) => name === item.capability)?.[1] ?? item.capability}</td>
        <td><span className={`ops-status ${item.status === "verified" ? "status-complete" : item.status === "failed" ? "status-error" : ""}`}>{statuses[item.status]}</span>
          {item.checked_at && <small className="ops-username">{new Date(item.checked_at * 1000).toLocaleString("ko-KR")}</small>}</td>
        <td>{item.stale ? "—" : elapsedLabel(item.elapsed_ms)}</td><td className="ops-capability-detail">{item.detail}
          {!item.stale && item.metrics?.tokens_per_second != null && <small className="ops-username">서버 토큰 수 기준 {item.metrics.tokens_per_second.toFixed(1)} 토큰/초</small>}</td></tr>)}</tbody></table></div>
      : !busy && <p className="ops-empty">이 모델의 실제 검사 기록이 없습니다. 필요한 기능을 선택해 검사하세요.</p>}
    <p>문맥 검사는 제한된 입력의 처리 여부만 확인합니다. 추론 필드가 없는 응답을 내부 사고의 증거로 해석하지 않습니다.</p>
  </section>;
}
