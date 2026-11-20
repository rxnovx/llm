"use client";

import { useEffect, useState } from "react";
import { appsJson, notifyAppsChanged } from "@/lib/app-connections";
import { CopyButton } from "@/components/chat/MessageContent";

export type WebhookSubscription = { id: string; connection_id: string; project_id: number; project: string;
  notifications_enabled: boolean; callback_path: string; created_at: number };
export type WebhookEvent = { id: string; kind: string; object_id: number; status: string; received_at: number; notify: boolean };
type WebhookStatus = WebhookSubscription & { events: WebhookEvent[] };
type WebhookSetup = WebhookSubscription & { secret_token: string; setup: string };

function callbackUrl(path: string) {
  // 서비스 내부의 고정된 수신 경로만 표시합니다. OAuth 토큰은 취급하지 않습니다.
  if (!/^\/api\/apps\/gitlab\/webhooks\/[a-f0-9-]{36}$/i.test(path)) return "";
  return typeof window === "undefined" ? path : new URL(path, window.location.origin).href;
}

export function GitlabWebhookSettings({ connectionId }: { connectionId: string }) {
  const [items, setItems] = useState<WebhookSubscription[]>([]);
  const [query, setQuery] = useState("");
  const [projects, setProjects] = useState<{ id: number; name: string }[]>([]);
  const [projectId, setProjectId] = useState("");
  const [notify, setNotify] = useState(false);
  const [setup, setSetup] = useState<WebhookSetup | null>(null);
  const [selected, setSelected] = useState("");
  const [status, setStatus] = useState<WebhookStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [revision, setRevision] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    void appsJson<{ items: WebhookSubscription[] }>(`/connections/${connectionId}/webhooks`, { signal: abort.signal })
      .then(result => { if (!abort.signal.aborted) { setItems(result.items); setError(""); } })
      .catch(caught => { if (!abort.signal.aborted) setError(caught instanceof Error ? caught.message : "상태 구독을 불러오지 못했습니다."); })
      .finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [connectionId, revision]);
  useEffect(() => {
    const abort = new AbortController();
    const timer = setTimeout(() => {
      void appsJson<{ items: { id: number; name: string }[] }>(`/connections/${connectionId}/projects?q=${encodeURIComponent(query.trim())}`, { signal: abort.signal })
        .then(result => { if (!abort.signal.aborted) setProjects(result.items); })
        .catch(caught => { if (!abort.signal.aborted) setError(caught instanceof Error ? caught.message : "접근 가능한 프로젝트를 불러오지 못했습니다."); });
    }, 250);
    return () => { clearTimeout(timer); abort.abort(); };
  }, [connectionId, query]);
  useEffect(() => {
    if (!selected) return;
    const abort = new AbortController();
    let pending = false;
    const load = async () => {
      if (pending || document.visibilityState !== "visible") return;
      pending = true;
      try {
        const result = await appsJson<WebhookStatus>(`/gitlab/webhook-subscriptions/${selected}`, {
          signal: AbortSignal.any([abort.signal, AbortSignal.timeout(35000)]),
        });
        if (!abort.signal.aborted) { setStatus(result); setError(""); }
      } catch (caught) { if (!abort.signal.aborted) setError(caught instanceof Error ? caught.message : "상태 이벤트를 불러오지 못했습니다."); }
      finally { pending = false; }
    };
    void load();
    const timer = setInterval(() => void load(), 15000);
    return () => { clearInterval(timer); abort.abort(); };
  }, [selected, revision]);
  const perform = async (operation: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await operation(); setRevision(value => value + 1); notifyAppsChanged(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "상태 구독을 변경하지 못했습니다."); }
    finally { setBusy(false); }
  };
  const selectedStatus = status?.id === selected ? status : null;
  return <section className="settings-webhooks" aria-label="GitLab MR·파이프라인 상태 구독"><h4>MR·파이프라인 상태 구독</h4>
    <p>GitLab 프로젝트에 웹훅을 등록하면 상태 변경을 앱에서 확인합니다. 이벤트가 코드 수정이나 명령 실행을 시작하지는 않습니다.</p>
    {error && <p className="account-notice text-[var(--error)]" role="alert">{error}</p>}
    {loading && <p role="status">상태 구독을 불러오는 중…</p>}
    {items.map(item => <div key={item.id} className="settings-webhook-row"><strong>{item.project}</strong><div className="settings-app-actions">
      <button type="button" className="settings-outline-button" disabled={busy} onClick={() => setSelected(item.id)}>상태 확인</button>
      <label><input type="checkbox" checked={item.notifications_enabled} disabled={busy} onChange={event => void perform(async () => {
        await appsJson(`/gitlab/webhook-subscriptions/${item.id}`, { method: "PATCH", body: JSON.stringify({ notifications_enabled: event.target.checked }) });
      })} />앱에서 알림</label>
      <button type="button" className="settings-text-button" disabled={busy} onClick={() => void perform(async () => {
        await appsJson(`/gitlab/webhook-subscriptions/${item.id}`, { method: "DELETE" });
        if (selected === item.id) { setSelected(""); setStatus(null); }
        if (setup?.id === item.id) setSetup(null);
      })}>구독 해제</button></div></div>)}
    {!loading && !items.length && <p>등록한 상태 구독이 없습니다.</p>}
    <details><summary>새 프로젝트 상태 구독</summary><fieldset disabled={busy}>
      <label>프로젝트 검색<input value={query} maxLength={100} onChange={event => { setQuery(event.target.value); setProjectId(""); }} placeholder="접근 가능한 프로젝트 이름" /></label>
      <label>프로젝트<select value={projectId} onChange={event => setProjectId(event.target.value)}><option value="">선택하세요</option>
        {projects.filter(item => !items.some(subscription => subscription.project_id === item.id)).map(item => <option value={item.id} key={item.id}>{item.name}</option>)}</select></label>
      <label className="settings-webhook-notify"><input type="checkbox" checked={notify} onChange={event => setNotify(event.target.checked)} />MR·파이프라인 변경을 앱에서 알림</label>
      <button type="button" className="settings-outline-button" disabled={!projectId || busy} onClick={() => void perform(async () => {
        const result = await appsJson<WebhookSetup>(`/connections/${connectionId}/projects/${projectId}/webhooks`, {
          method: "POST", body: JSON.stringify({ notifications_enabled: notify }),
        });
        setSetup(result); setProjectId(""); setSelected(result.id);
      })}>{busy ? "저장 중…" : "상태 구독 생성"}</button>
    </fieldset></details>
    <p>구독 해제 후 GitLab 프로젝트에 등록한 웹훅도 제거하세요.</p>
    {setup && <section className="settings-webhook-setup" aria-label="GitLab 웹훅 등록 정보"><h4>GitLab 프로젝트에 웹훅 등록</h4>
      <p>프로젝트의 Settings → Webhooks에서 GitLab 서버가 접근할 수 있는 앱 URL과 Secret token을 등록하고 Merge request, Pipeline, Comments 이벤트를 선택하세요.</p>
      <label>수신 URL<input readOnly value={callbackUrl(setup.callback_path)} /></label><CopyButton text={callbackUrl(setup.callback_path)} label="웹훅 URL 복사" />
      <label>웹훅 Secret token<input readOnly type="password" value={setup.secret_token} autoComplete="off" /></label><CopyButton text={setup.secret_token} label="웹훅 비밀값 복사" />
      <p>이 비밀값은 지금 한 번만 제공합니다. 저장한 뒤 이 안내를 닫으세요. 페이지를 새로고침하면 다시 조회할 수 없습니다.</p>
      <button type="button" className="settings-text-button" onClick={() => setSetup(null)}>등록 정보 닫기</button>
    </section>}
    {selectedStatus && <section className="settings-webhook-events" aria-label={`${selectedStatus.project} 최근 상태`}><header><h4>{selectedStatus.project} · 최근 상태</h4>
      <button type="button" className="settings-text-button" disabled={busy} onClick={() => void perform(async () => { await appsJson(`/gitlab/webhook-subscriptions/${selected}/seen`, { method: "POST" }); })}>알림 읽음 처리</button></header>
      <ul>{selectedStatus.events.map(event => <li key={event.id}><span>{event.kind === "pipeline" ? "파이프라인" : event.kind === "merge_request_comment" ? "MR 댓글" : "MR"} {event.kind === "pipeline" ? "#" : "!"}{event.object_id}
        {event.notify && <small>새 소식</small>}</span><strong>{event.status}</strong><time>{new Date(event.received_at * 1000).toLocaleString("ko-KR")}</time></li>)}</ul>
      {!selectedStatus.events.length && <p>수신한 상태 이벤트가 없습니다. GitLab의 웹훅 테스트로 연결을 확인하세요.</p>}
    </section>}
  </section>;
}
