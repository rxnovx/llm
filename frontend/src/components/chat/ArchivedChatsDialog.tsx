"use client";

import { useEffect, useRef, useState } from "react";
import { useDialogDismiss } from "@/components/shared/useDialogDismiss";
import { chatJson } from "@/lib/chat-api";
import type { Conversation } from "@/types/chat";

export function ArchivedChatsDialog({ onClose, onOpen, onChanged, onDeleted }: {
  onClose: () => void; onOpen: (item: Conversation) => void;
  onChanged: (item: Conversation) => void; onDeleted: (id: string) => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [items, setItems] = useState<Conversation[] | null>(null);
  const [query, setQuery] = useState("");
  const [hasMore, setHasMore] = useState(false);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const [moreBusy, setMoreBusy] = useState(false);
  const [confirmation, setConfirmation] = useState<string | null>(null);
  const [refresh, setRefresh] = useState(0);
  const controller = useRef(new AbortController());
  const dismiss = useDialogDismiss(onClose, !busy);

  useEffect(() => {
    if (dialog.current && !dialog.current.open) dialog.current.showModal();
    const abort = new AbortController(); controller.current = abort;
    return () => abort.abort();
  }, []);
  useEffect(() => {
    const abort = new AbortController();
    const timer = setTimeout(() => {
      void chatJson<{ items: Conversation[]; has_more: boolean }>(`/conversations?view=archived&q=${encodeURIComponent(query.trim())}`, {
        signal: AbortSignal.any([abort.signal, AbortSignal.timeout(10000)]),
      }).then(result => { if (!abort.signal.aborted) { setItems(result.items); setHasMore(result.has_more); setError(""); } })
        .catch(caught => { if (!abort.signal.aborted) setError(caught instanceof Error ? caught.message : "보관함을 불러오지 못했습니다."); });
    }, 200);
    return () => { clearTimeout(timer); abort.abort(); };
  }, [query, refresh]);

  const mutate = async (item: Conversation, remove = false) => {
    if (busy) return;
    setBusy(item.id); setError("");
    try {
      if (remove) {
        await chatJson(`/conversations/${item.id}`, { method: "DELETE", signal: AbortSignal.any([controller.current.signal, AbortSignal.timeout(10000)]) });
        onDeleted(item.id);
      } else {
        const result = await chatJson<Conversation>(`/conversations/${item.id}/organization`, { method: "PATCH",
          body: JSON.stringify({ archived: false, revision: item.organization_revision ?? 0 }), signal: AbortSignal.any([controller.current.signal, AbortSignal.timeout(10000)]) });
        onChanged(result);
      }
      setItems(current => current?.filter(value => value.id !== item.id) ?? []); setConfirmation(null);
      setRefresh(value => value + 1);
    } catch (caught) {
      if (!controller.current.signal.aborted) setError(caught instanceof Error ? caught.message : "대화를 변경하지 못했습니다.");
    } finally { if (!controller.current.signal.aborted) setBusy(null); }
  };
  const more = async () => {
    if (!items || moreBusy) return;
    setMoreBusy(true);
    try {
      const result = await chatJson<{ items: Conversation[]; has_more: boolean }>(`/conversations?view=archived&q=${encodeURIComponent(query.trim())}&offset=${items.length}`, {
        signal: AbortSignal.any([controller.current.signal, AbortSignal.timeout(10000)]),
      });
      if (!controller.current.signal.aborted) { setItems(current => [...(current ?? []), ...result.items.filter(item => !current?.some(old => old.id === item.id))]); setHasMore(result.has_more); }
    } catch (caught) {
      if (!controller.current.signal.aborted) setError(caught instanceof Error ? caught.message : "이전 대화를 불러오지 못했습니다.");
    } finally { if (!controller.current.signal.aborted) setMoreBusy(false); }
  };

  return <dialog ref={dialog} className="cw-dialog cw-archive-library" aria-labelledby="archive-library-title" {...dismiss}>
    <header className="cw-dialog-header"><h2 id="archive-library-title">보관된 대화</h2><button type="button" className="cw-small-button" disabled={!!busy} onClick={onClose}>닫기</button></header>
    <p className="cw-dialog-description">보관은 대화를 삭제하지 않습니다. 검색·직접 링크로 다시 열 수 있고, 새 질문을 보내면 일반 기록으로 돌아옵니다.</p>
    <div className="cw-library-toolbar"><label className="cw-sr-only" htmlFor="archive-search">보관된 대화 검색</label>
      <input id="archive-search" value={query} placeholder="제목·본문 검색" disabled={!!busy || moreBusy} onChange={event => { setQuery(event.target.value); setItems(null); setHasMore(false); setConfirmation(null); }} />
      <button type="button" className="cw-text-button" disabled={!!busy || moreBusy} onClick={() => setRefresh(value => value + 1)}>새로고침</button></div>
    {error && <p className="cw-inline-error" role="alert">{error}</p>}
    {items === null && !error && <p role="status">보관된 대화를 불러오는 중…</p>}
    {items?.length === 0 && <p className="cw-library-empty">{query ? "일치하는 보관 대화가 없습니다." : "보관된 대화가 없습니다."}</p>}
    {!!items?.length && <ul className="cw-library-list" aria-label="보관된 대화 목록">{items.map(item => <li key={item.id}>
      <div className="cw-library-heading"><h3>{item.title}</h3>{item.busy && <span>작업 중</span>}</div>
      <div className="cw-library-actions"><button type="button" className="cw-text-button" disabled={!!busy} onClick={() => onOpen(item)}>대화 열기</button>
        <button type="button" className="cw-text-button" disabled={!!busy} onClick={() => void mutate(item)}>보관 해제</button>
        <button type="button" className="cw-text-button cw-danger" disabled={!!busy || item.busy} onClick={() => setConfirmation(item.id)}>삭제</button></div>
      {confirmation === item.id && <div className="cw-library-confirm"><p>이 대화와 메시지를 삭제합니다. 복구할 수 없습니다.</p>
        <button type="button" className="cw-small-button" disabled={!!busy} onClick={() => setConfirmation(null)}>취소</button>{" "}
        <button type="button" className="cw-small-button cw-danger" disabled={!!busy} onClick={() => void mutate(item, true)}>{busy === item.id ? "삭제 중…" : "영구 삭제"}</button></div>}
    </li>)}</ul>}
    {hasMore && <button type="button" className="cw-small-button" disabled={moreBusy || !!busy} onClick={() => void more()}>{moreBusy ? "불러오는 중…" : "이전 보관 대화 더 보기"}</button>}
  </dialog>;
}
