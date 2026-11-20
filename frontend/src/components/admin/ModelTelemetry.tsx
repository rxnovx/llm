"use client";

import { useEffect, useState } from "react";
import { authenticatedFetch, ApiRequestError } from "@/lib/authenticated-fetch";
import type { ModelTelemetryReport } from "@/types/admin";
import { elapsedLabel } from "@/lib/generation-trace";
import { activityLabel } from "@/components/chat/activity-labels";

export function ModelTelemetry({ revision, onForbidden }: { revision: number; onForbidden: () => void }) {
  const [report, setReport] = useState<ModelTelemetryReport | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    let pending = false;
    const load = async () => {
      if (pending) return;
      pending = true;
      try {
        const response = await authenticatedFetch("/api/admin/telemetry?hours=24", {
          signal: AbortSignal.any([abort.signal, AbortSignal.timeout(15000)]),
        });
        const result: ModelTelemetryReport = await response.json();
        if (!abort.signal.aborted) { setReport(result); setError(""); }
      } catch (caught) {
        if (abort.signal.aborted) return;
        if (caught instanceof ApiRequestError && caught.status === 403) { setReport(null); onForbidden(); }
        else setError(caught instanceof Error ? caught.message : "모델 측정 통계를 불러오지 못했습니다.");
      } finally { pending = false; }
    };
    void load();
    const timer = setInterval(() => { if (document.visibilityState === "visible") void load(); }, 30000);
    return () => { clearInterval(timer); abort.abort(); };
  }, [revision, onForbidden]);
  return <section className="ops-panel"><header><div><h2>실제 모델·도구 측정</h2><p>최근 24시간의 실제 모델 호출과 도구 기록 · 생성 통계 필터와 별도</p></div></header>
    {error && <p className="ops-error" role="alert">{error}</p>}
    {report && <><div className="ops-table-scroll"><table><thead><tr><th>모델 / 연결</th><th>호출 / 오류</th><th>평균 응답</th><th>평균 첫 토큰</th><th>평균 생성 속도</th><th>서버 보고 토큰</th></tr></thead>
      <tbody>{report.models.map(item => <tr key={`${item.connection_id}:${item.model}`}><td><strong>{item.model}</strong><small className="ops-username">{item.connection_id}</small></td>
        <td>{item.calls.toLocaleString()} / {item.errors.toLocaleString()} <small className="ops-username">오류율 {item.error_rate}%</small>
          {item.connection_error_rate != null && <small className="ops-username">연결 실패율 {item.connection_error_rate}%</small>}</td>
        <td>{item.average_ms == null ? "—" : elapsedLabel(item.average_ms)}</td><td>{item.average_first_token_ms == null ? "—" : elapsedLabel(item.average_first_token_ms)}
          {item.average_server_first_token_ms != null && <small className="ops-username">서버 보고 {elapsedLabel(item.average_server_first_token_ms)}</small>}</td>
        <td>{item.average_tokens_per_second == null ? "—" : `${item.average_tokens_per_second.toFixed(1)} 토큰/초`}
          {item.average_tokens_per_second != null && <small className="ops-username">{item.speed_source === "server_reported" ? "서버 보고 생성 속도" : "앱 관측 처리 속도"}</small>}</td>
        <td>입력 {item.reported_input_tokens.toLocaleString()} / 출력 {item.reported_output_tokens.toLocaleString()}<small className="ops-username">{item.reported_calls.toLocaleString()}개 호출에서 보고됨</small></td></tr>)}
        {!report.models.length && <tr><td colSpan={6} className="ops-empty">수집된 실제 모델 측정값이 없습니다.</td></tr>}</tbody></table></div>
      <details className="ops-tool-telemetry"><summary>도구 실행 상태 기록</summary><div className="ops-table-scroll"><table><thead><tr><th>도구 / 단계</th><th>상태</th><th>기록 수</th></tr></thead>
        <tbody>{report.tools.map(item => <tr key={`${item.name}:${item.status}`}><td>{activityLabel(item.name)}</td>
          <td>{{ running: "진행 중", complete: "완료", error: "오류", cancelled: "취소" }[item.status] ?? item.status}</td><td>{item.count.toLocaleString()}</td></tr>)}
          {!report.tools.length && <tr><td colSpan={3} className="ops-empty">수집된 도구 기록이 없습니다.</td></tr>}</tbody></table></div></details>
      <p className="ops-telemetry-note">{report.note}</p></>}
    {!report && !error && <p className="ops-empty" role="status">실제 측정값을 불러오는 중…</p>}
  </section>;
}
