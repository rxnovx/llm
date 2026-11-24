"use client";

import { memo, useEffect, useRef, useState } from "react";
import { useDialogDismiss } from "@/components/shared/useDialogDismiss";
import { WorkspaceLink } from "@/components/shared/WorkspaceLink";
import { chatJson } from "@/lib/chat-api";
import { activeJob, elapsedLabel, jobStatus, type ChatJob, type JobPage } from "@/lib/job-center";
import { conversationPath } from "@/lib/workspace-navigation";
import { activityLabel } from "./activity-labels";

export const JobCenterDialog = memo(function JobCenterDialog({ onClose }: { onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [view, setView] = useState<"active" | "finished">("active");
  const [page, setPage] = useState<JobPage | null>(null);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  const [working, setWorking] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [receivedAt, setReceivedAt] = useState(Date.now);
  const [now, setNow] = useState(Date.now);
  const lifetime = useRef(new AbortController());
  const dismiss = useDialogDismiss(onClose, !working);

  useEffect(() => {
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
    const controller = new AbortController(); lifetime.current = controller;
    const tick = window.setInterval(() => setNow(Date.now()), 1000);
    return () => { controller.abort(); clearInterval(tick); };
  }, []);

  useEffect(() => {
    const abort = new AbortController();
    let pending = false;
    const load = async () => {
      if (pending || document.hidden) return;
      pending = true;
      try {
        const result = await chatJson<JobPage>(`/jobs?view=${view}`, {
          signal: AbortSignal.any([abort.signal, AbortSignal.timeout(10000)]),
        });
        if (!abort.signal.aborted) { setPage(result); setReceivedAt(Date.now()); setError(""); }
      } catch (caught) {
        if (!abort.signal.aborted) setError(caught instanceof Error ? caught.message : "작업 기록을 불러오지 못했습니다.");
      } finally { pending = false; }
    };
    void load();
    const timer = view === "active" ? window.setInterval(load, 2500) : undefined;
    document.addEventListener("visibilitychange", load);
    return () => { abort.abort(); clearInterval(timer); document.removeEventListener("visibilitychange", load); };
  }, [view, refresh]);

  const stop = async (job: ChatJob) => {
    if (working) return;
    setWorking(job.id); setError("");
    try {
      await chatJson(`/conversations/${job.conversation_id}/cancel`, { method: "POST",
        body: JSON.stringify({ request_id: job.id }), signal: AbortSignal.any([lifetime.current.signal, AbortSignal.timeout(10000)]),
      });
      setPage(current => current ? { ...current, items: current.items.map(item => item.id === job.id
        ? { ...item, cancel_requested: true, can_cancel: false } : item) } : current);
      setRefresh(value => value + 1);
    } catch (caught) {
      if (!lifetime.current.signal.aborted) setError(caught instanceof Error ? caught.message : "중지를 요청하지 못했습니다.");
    } finally { if (!lifetime.current.signal.aborted) setWorking(null); }
  };
  const more = async () => {
    if (!page || loadingMore) return;
    setLoadingMore(true);
    try {
      const result = await chatJson<JobPage>(`/jobs?view=finished&offset=${page.items.length}`, {
        signal: AbortSignal.any([lifetime.current.signal, AbortSignal.timeout(10000)]),
      });
      if (!lifetime.current.signal.aborted) setPage(current => current ? { ...result,
        items: [...current.items, ...result.items.filter(item => !current.items.some(old => old.id === item.id))] } : result);
    } catch (caught) {
      if (!lifetime.current.signal.aborted) setError(caught instanceof Error ? caught.message : "이전 작업을 불러오지 못했습니다.");
    } finally { if (!lifetime.current.signal.aborted) setLoadingMore(false); }
  };

  return <dialog ref={dialog} className="cw-dialog cw-job-center" aria-labelledby="job-center-title" {...dismiss}>
    <header className="cw-dialog-header"><h2 id="job-center-title">작업 센터</h2>
      <button type="button" className="cw-small-button" disabled={!!working} onClick={onClose}>닫기</button></header>
    <p className="cw-dialog-description">다른 대화로 이동해도 작업은 계속됩니다. 경과 시간에는 대기 시간이 포함됩니다.</p>
    <div className="cw-library-toolbar" role="group" aria-label="작업 기록 범위">
      <button type="button" className="cw-small-button" aria-pressed={view === "active"} disabled={!!working || loadingMore}
        onClick={() => { if (view !== "active") { setView("active"); setPage(null); setError(""); } }}>진행 중{page && view === "active" ? ` ${page.active_count}` : ""}</button>
      <button type="button" className="cw-small-button" aria-pressed={view === "finished"} disabled={!!working || loadingMore}
        onClick={() => { if (view !== "finished") { setView("finished"); setPage(null); setError(""); } }}>완료·중단 기록</button>
      <button type="button" className="cw-text-button" disabled={!!working || loadingMore} onClick={() => setRefresh(value => value + 1)}>새로고침</button>
    </div>
    {error && <p className="cw-inline-error" role="alert">{error}</p>}
    {!page && !error && <p role="status">작업 기록을 불러오는 중…</p>}
    {page?.items.length === 0 && <p className="cw-library-empty">{view === "active" ? "진행 중인 작업이 없습니다. 새 대화에서 다른 작업을 시작할 수 있습니다." : "완료되거나 중단된 작업이 없습니다."}</p>}
    {!!page?.items.length && <ul className="cw-library-list" aria-label="내 채팅 작업">{page.items.map(job => <li key={job.id}>
      <div className="cw-library-heading"><h3>{job.title}</h3><span className={`cw-job-state is-${job.status}`}>{jobStatus(job)}</span></div>
      <p>{job.model}{job.is_test ? " · 테스트 모델" : ""} · {elapsedLabel(job, receivedAt, now)}{job.position > 0 && ` · 대기 순번 ${job.position}`}</p>
      {job.activity && <p>{job.activity.status === "running" && activeJob(job) ? "현재 단계" : "마지막 단계"}: {activityLabel(job.activity.name, "도구 작업")}</p>}
      <div className="cw-library-actions"><WorkspaceLink className="cw-text-button" href={conversationPath(job.conversation_id)} onNavigate={onClose}>
        {activeJob(job) ? "작업 대화 열기" : "결과·검증 기록 보기"}</WorkspaceLink>
        {job.can_cancel && <button type="button" className="cw-text-button" disabled={!!working} onClick={() => void stop(job)}>{working === job.id ? "요청 중…" : "작업 중지"}</button>}</div>
    </li>)}</ul>}
    {view === "finished" && page?.has_more && <button type="button" className="cw-small-button" disabled={loadingMore || !!working} onClick={() => void more()}>{loadingMore ? "불러오는 중…" : "이전 작업 더 보기"}</button>}
    <p className="cw-dialog-description">중지는 진행 중인 외부 변경을 되돌리지 않습니다. 재시도는 대화에서 기존 검증·권한 확인 흐름으로 진행합니다.</p>
  </dialog>;
});
