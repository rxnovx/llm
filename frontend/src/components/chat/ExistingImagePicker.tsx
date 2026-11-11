'use client';

import { useEffect, useState } from 'react';
import { knowledgeJson, type KnowledgeDocument } from '@/lib/knowledge';

type Props = {
  conversationId: string;
  disabled?: boolean;
  selectedImageIds?: string[];
  onSelect: (image: KnowledgeDocument) => void;
};

export function ExistingImagePicker({ conversationId, disabled, selectedImageIds = [], onSelect }: Props) {
  const [result, setResult] = useState<{ conversationId: string; images: KnowledgeDocument[]; failed: boolean } | null>(null);
  const [selectedId, setSelectedId] = useState('');

  useEffect(() => {
    const abort = new AbortController();
    void knowledgeJson<{ items: KnowledgeDocument[] }>(`/documents?conversation_id=${encodeURIComponent(conversationId)}`, { signal: abort.signal })
      .then(({ items }) => {
        const images = items.filter((doc) => doc.status === 'image' || /\.(png|jpe?g|webp)$/i.test(doc.filename));
        if (!abort.signal.aborted) setResult({ conversationId, images, failed: false });
      }).catch(() => {
        if (!abort.signal.aborted) setResult({ conversationId, images: [], failed: true });
      });
    return () => abort.abort();
  }, [conversationId]);

  if (result?.conversationId !== conversationId) return null;
  if (result.failed) return <p className="cw-inline-error" role="status">이 대화의 이미지를 불러오지 못했습니다.</p>;

  const selected = new Set(result.images.filter((doc) => selectedImageIds.includes(doc.id)).map((doc) => doc.sha256 || doc.id));
  const choices = new Map<string, KnowledgeDocument>();
  for (const doc of result.images) {
    const key = doc.sha256 || doc.id;
    if (!selected.has(key) && !choices.has(key)) choices.set(key, doc);
  }
  if (!choices.size) return <p className="cw-upload-selection-note" role="status">이 대화에 다시 사용할 이미지가 없습니다.</p>;

  const current = Array.from(choices.values()).find((doc) => doc.id === selectedId);
  return <div className="cw-existing-images">
    <select className="cw-context-button" aria-label="기존 이미지" value={current?.id ?? ''} disabled={disabled}
      onChange={(event) => setSelectedId(event.target.value)}>
      <option value="">이 대화의 이미지 선택</option>
      {Array.from(choices.values(), (doc) => <option key={doc.id} value={doc.id}>{doc.filename}</option>)}
    </select>
    <button type="button" className="cw-small-button" disabled={disabled || !current} onClick={() => {
      if (!current) return;
      onSelect(current);
      setSelectedId('');
    }}>이미지 사용</button>
  </div>;
}
