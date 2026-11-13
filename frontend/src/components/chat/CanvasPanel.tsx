"use client";

import { useDeferredValue, useEffect, useId, useRef, useState } from "react";
import { ApiRequestError } from "@/lib/authenticated-fetch";
import { workflowJson, type CanvasDraft } from "@/lib/workflows";
import type { ChatMessage } from "@/types/chat";
import { Icon } from "./Icons";
import { CopyButton, MessageContent } from "./MessageContent";
import { splitCanvas } from "./answer-canvas";
import "./canvas.css";

export type CanvasEdit = { content: string; saved: CanvasDraft };

type Props = {
  message: ChatMessage;
  editKey: string;
  readOnly: boolean;
  readEdit: (key: string) => CanvasEdit | undefined;
  rememberEdit: (key: string, edit?: CanvasEdit) => void;
  onClose: () => void;
  onStop?: () => void;
};

const errorText = (error: unknown) => error instanceof Error ? error.message : "캔버스를 불러오지 못했습니다.";

export function CanvasAnswer({ message, expanded, onOpen }: {
  message: ChatMessage;
  expanded: boolean;
  onOpen: (trigger: HTMLButtonElement) => void;
}) {
  const { before, after } = splitCanvas(message);
  return <>
    {before.trim() && <MessageContent content={before} />}
    <button type="button" className="cw-canvas-card" aria-expanded={expanded}
      onClick={event => onOpen(event.currentTarget)}>
      <Icon name="file" size={18} />
      <span>캔버스에서 열기</span>
      <Icon name="panel" size={16} />
    </button>
    {after.trim() && <MessageContent content={after} />}
  </>;
}

export function CanvasPanel({ message, editKey, readOnly, readEdit, rememberEdit, onClose, onStop }: Props) {
  const headingId = useId();
  const heading = useRef<HTMLHeadingElement>(null);
  const [preview, setPreview] = useState(false);
  const [edit, setEdit] = useState<CanvasEdit | null>(null);
  const [error, setError] = useState("");
  const [conflict, setConflict] = useState(false);
  const [saving, setSaving] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const discardOnLoad = useRef(false);
  const pending = message.status === "pending";
  const canEdit = message.status === "complete" && !readOnly;
  const loading = canEdit && !edit && !error;
  const editable = canEdit && !!edit && !saving;
  const dirty = canEdit && edit !== null && edit.content !== edit.saved.content;
  const content = canEdit && edit ? edit.content : splitCanvas(message).body;
  const rendered = useDeferredValue(preview ? content : "");
  const path = `/messages/${message.id}/canvas`;
  const versionId = message.routing?.canvas ? message.active_version : null;
  const readPath = versionId ? `${path}?version_id=${encodeURIComponent(versionId)}` : path;

  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, []);

  useEffect(() => {
    if (!canEdit) return;
    const abort = new AbortController();
    void workflowJson<CanvasDraft>(readPath, { signal: abort.signal }).then(saved => {
      if (abort.signal.aborted) return;
      const cached = discardOnLoad.current ? undefined : readEdit(editKey);
      if (discardOnLoad.current) rememberEdit(editKey);
      discardOnLoad.current = false;
      setEdit(cached ?? { saved, content: saved.content });
      const changed = cached !== undefined && cached.saved.revision !== saved.revision;
      setConflict(changed);
      setError(changed ? "다른 창에서 저장한 변경사항이 있습니다. 저장본을 확인해 주세요." : "");
    }).catch(caught => {
      if (abort.signal.aborted) return;
      discardOnLoad.current = false;
      const cached = readEdit(editKey);
      if (cached) setEdit(cached);
      setError(errorText(caught));
    });
    return () => abort.abort();
  }, [canEdit, editKey, loadAttempt, readPath, readEdit, rememberEdit]);

  const change = (nextContent: string) => {
    if (!edit || !editable) return;
    const next = { ...edit, content: nextContent };
    setEdit(next);
    rememberEdit(editKey, next.content === next.saved.content ? undefined : next);
    if (!conflict) setError("");
  };

  const save = async () => {
    if (!edit || !dirty || !editable || conflict) return;
    setSaving(true);
    setError("");
    try {
      const saved = await workflowJson<CanvasDraft>(path, {
        method: "PUT", body: JSON.stringify({ content: edit.content, revision: edit.saved.revision,
          ...(versionId ? { version_id: versionId } : {}) }),
      });
      if (readEdit(editKey) === edit) rememberEdit(editKey);
      setEdit({ saved, content: saved.content });
    } catch (caught) {
      setConflict(caught instanceof ApiRequestError && caught.status === 409);
      setError(errorText(caught));
    } finally {
      setSaving(false);
    }
  };

  const reload = (discardEdit: boolean) => {
    discardOnLoad.current = discardEdit;
    setLoadAttempt(attempt => attempt + 1);
    setEdit(null);
    setError("");
    setConflict(false);
  };

  return <aside className="cw-canvas" aria-labelledby={headingId} aria-busy={pending || loading || saving}
    onKeyDown={event => {
      if (event.nativeEvent.isComposing) return;
      if (event.key === "Escape") {
        event.stopPropagation();
        if (!saving) onClose();
      }
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
        event.preventDefault();
        void save();
      }
    }}>
    <header className="cw-canvas-header">
      <h2 id={headingId} ref={heading} tabIndex={-1}><Icon name="file" size={19} />캔버스</h2>
      <button className="cw-icon-button" type="button" aria-label="캔버스 닫기" disabled={saving}
        onClick={onClose}><Icon name="close" size={20} /></button>
    </header>
    <div className="cw-canvas-tools">
      <div className="cw-canvas-views" role="group" aria-label="캔버스 표시 방식">
        <button type="button" aria-pressed={!preview} onClick={() => setPreview(false)}>Markdown</button>
        <button type="button" aria-pressed={preview} onClick={() => setPreview(true)}>미리보기</button>
      </div>
      <CopyButton text={content} label="Markdown 복사" disabled={!content || loading} />
    </div>
    <textarea className="cw-canvas-editor" aria-label="캔버스 Markdown" spellCheck={false} autoCapitalize="off"
      value={content} maxLength={200_000} readOnly={!editable} hidden={preview}
      placeholder={pending ? "답변을 작성하고 있습니다…" : "Markdown을 입력하세요."}
      onChange={event => change(event.target.value)} />
    {preview && <div className="cw-canvas-preview" role="region" aria-label="Markdown 미리보기">
      {rendered ? <MessageContent content={rendered} /> : <p>{pending ? "답변을 작성하고 있습니다…" : "표시할 내용이 없습니다."}</p>}
    </div>}
    <footer className="cw-canvas-footer">
      {error && <div className="cw-canvas-error"><p role="alert">{error}</p>
        {(conflict || !edit) && <button type="button" className="cw-text-button"
          onClick={() => reload(conflict)}>{conflict ? "저장본으로 바꾸기" : "다시 불러오기"}</button>}
      </div>}
      <div className="cw-canvas-save">
        <span role="status">{pending ? "답변 작성 중…" : loading ? "불러오는 중…" : saving ? "저장 중…"
          : dirty ? "저장하지 않은 변경" : edit?.saved.revision ? "저장됨" : !canEdit ? "열람 전용" : ""}</span>
        {pending && onStop && <button className="cw-small-button" type="button" onClick={onStop}>응답 중지</button>}
        {canEdit && <button className="cw-primary-button" type="button" disabled={!dirty || saving || conflict}
          onClick={() => void save()}>저장</button>}
      </div>
    </footer>
  </aside>;
}
