'use client';

import { useCallback, useEffect, useRef, useState } from 'react';
import { cancelAttachmentUpload, defaultAttachmentConfig, uploadAttachment, type AttachmentConfig } from '@/lib/attachments';
import { knowledgeJson, type KnowledgeDocument } from '@/lib/knowledge';

type Item = {
  id: number;
  file: File;
  stage: 'waiting' | 'uploading' | 'indexing' | 'ready' | 'error' | 'cancelled';
  progress: number;
  error?: string;
};

type Props = {
  conversationId?: string;
  baseId?: string;
  disabled?: boolean;
  onReady?: (doc: KnowledgeDocument) => void;
  onBusy?: (busy: boolean) => void;
  onChanged?: () => void;
  accept?: string;
};

type Task = { abort: AbortController; attempt?: string };

const MiB = 1024 * 1024;
const errorText = (error: unknown) => error instanceof Error ? error.message : '첨부하지 못했습니다.';
const inProgress = (stage: Item['stage']) => stage === 'waiting' || stage === 'uploading' || stage === 'indexing';

export function UploadQueue({
  conversationId, baseId, disabled, onReady, onBusy, onChanged, accept = defaultAttachmentConfig.formats.join(','),
}: Props) {
  const [config, setConfig] = useState<AttachmentConfig>({ ...defaultAttachmentConfig, formats: accept.split(',') });
  const [items, setItems] = useState<Item[]>([]);
  const [error, setError] = useState('');
  const input = useRef<HTMLInputElement>(null);
  const nextId = useRef(0);
  const alive = useRef(true);
  const running = useRef(new Map<number, Task>());
  const callbacks = useRef({ onReady, onBusy, onChanged });

  useEffect(() => {
    const abort = new AbortController();
    void knowledgeJson<AttachmentConfig>('/config', { signal: abort.signal }).then(setConfig).catch(() => {});
    return () => abort.abort();
  }, []);
  useEffect(() => {
    callbacks.current = { onReady, onBusy, onChanged };
  }, [onReady, onBusy, onChanged]);

  const patch = useCallback((id: number, data: Partial<Item>) => {
    if (alive.current) setItems((old) => old.map((item) => item.id === id ? { ...item, ...data } : item));
  }, []);

  const acceptedFormats = config.formats.join(',');
  const maxBytes = Math.min(config.max_upload_bytes, 20 * MiB);
  const add = (files: File[]) => {
    if (disabled || !config.enabled || !files.length) return;
    const supported = new Set(config.formats.map((format) => format.toLowerCase()));
    const accepted = files.filter((file) =>
      supported.has('.' + file.name.split('.').at(-1)?.toLowerCase()) && file.size > 0 && file.size <= maxBytes);
    const room = Math.max(0, 20 - items.length);
    setError(accepted.length > room ? '한 번에 20개까지 첨부할 수 있습니다.'
      : accepted.length !== files.length ? `빈 파일, ${maxBytes / MiB}MiB 초과 파일 또는 지원하지 않는 형식은 제외했습니다.` : '');
    const waiting = accepted.slice(0, room).map((file) => ({
      id: ++nextId.current, file, stage: 'waiting' as const, progress: 0,
    }));
    setItems((old) => [...old, ...waiting]);
  };

  useEffect(() => {
    alive.current = true;
    const active = running.current;
    return () => {
      alive.current = false;
      for (const { abort, attempt } of active.values()) {
        abort.abort();
        if (attempt) void cancelAttachmentUpload(attempt).catch(() => {});
      }
      active.clear();
      callbacks.current.onBusy?.(false);
    };
  }, []);
  useEffect(() => {
    callbacks.current.onBusy?.(items.some((item) => inProgress(item.stage)));
  }, [items]);

  useEffect(() => {
    for (const item of items.filter((entry) => entry.stage === 'waiting')) {
      if (running.current.size >= 2) break;
      if (running.current.has(item.id)) continue;

      const task: Task = { abort: new AbortController() };
      running.current.set(item.id, task);
      patch(item.id, { stage: 'uploading' });
      void uploadAttachment(item.file, { conversationId, baseId }, {
        signal: task.abort.signal,
        onAttempt: (id) => {
          task.attempt = id;
          if (task.abort.signal.aborted) void cancelAttachmentUpload(id).catch(() => {});
        },
        onProgress: (progress) => patch(item.id, { progress }),
        onIndexing: () => {
          patch(item.id, { stage: 'indexing', progress: 100 });
          if (alive.current) callbacks.current.onChanged?.();
        },
      }).then((doc) => {
        if (!alive.current || task.abort.signal.aborted) return;
        patch(item.id, { stage: 'ready', progress: 100 });
        callbacks.current.onReady?.(doc);
        callbacks.current.onChanged?.();
      }).catch((caught) => {
        if (!task.abort.signal.aborted) patch(item.id, { stage: 'error', error: errorText(caught) });
      }).finally(() => {
        running.current.delete(item.id);
        if (alive.current) setItems((old) => [...old]);
      });
    }
  }, [items, conversationId, baseId, patch]);

  const cancel = async (item: Item) => {
    const task = running.current.get(item.id);
    task?.abort.abort();
    patch(item.id, { stage: 'cancelled' });
    if (!task?.attempt) return;
    try {
      await cancelAttachmentUpload(task.attempt);
      if (alive.current) callbacks.current.onChanged?.();
    } catch (caught) {
      if (alive.current) setError('서버 첨부 취소를 확인하지 못했습니다. ' + errorText(caught));
    }
  };

  return <div className="cw-upload-queue" onDragOver={(event) => {
    if (event.dataTransfer.types.includes('Files')) event.preventDefault();
  }} onDrop={(event) => {
    if (!event.dataTransfer.files.length) return;
    event.preventDefault();
    event.stopPropagation();
    add(Array.from(event.dataTransfer.files));
  }}>
    <button type="button" className="cw-text-button" disabled={disabled || !config.enabled} onClick={() => input.current?.click()}>
      ＋ 파일 첨부 <small>여러 파일 선택·끌어놓기</small>
    </button>
    <input ref={input} type="file" hidden multiple accept={acceptedFormats} onChange={(event) => {
      add(Array.from(event.target.files ?? []));
      event.target.value = '';
    }} />
    {!config.enabled && <p>운영자가 파일·지식 기능을 활성화해야 첨부할 수 있습니다.</p>}
    {error && <p className="cw-inline-error" role="alert">{error}</p>}
    {items.length > 0 && <ul aria-label="첨부 진행 상황">{items.map((item) => <li key={item.id}>
      <div>
        <strong>{item.file.name}</strong>
        <small>{item.stage === 'waiting' ? '전송 대기'
          : item.stage === 'uploading' ? item.progress === 100 ? '서버에서 파일 확인 중…' : `전송 ${item.progress}%`
          : item.stage === 'indexing' ? '문서 색인 중…' : item.stage === 'ready' ? '검색 준비됨'
          : item.stage === 'cancelled' ? '취소됨' : item.error}</small>
      </div>
      {item.stage === 'uploading' && <progress max={100} value={item.progress} aria-label={`${item.file.name} 전송률`} />}
      {inProgress(item.stage) && <button type="button" onClick={() => void cancel(item)}>취소</button>}
      {item.stage === 'error' && <button type="button" disabled={disabled}
        onClick={() => patch(item.id, { stage: 'waiting', error: undefined, progress: 0 })}>재시도</button>}
      {!inProgress(item.stage) && <button type="button" aria-label={`${item.file.name} 진행 기록 닫기`}
        onClick={() => setItems((old) => old.filter((entry) => entry.id !== item.id))}>×</button>}
    </li>)}</ul>}
  </div>;
}
