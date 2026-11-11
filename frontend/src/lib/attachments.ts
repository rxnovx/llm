import { authenticatedUpload } from './authenticated-fetch';
import { knowledgeJson, type KnowledgeDocument } from './knowledge';
import { workflowJson } from './workflows';

export type AttachmentConfig = {
  enabled: boolean;
  formats: string[];
  max_upload_bytes: number;
  image_formats: string[];
  image_max_upload_bytes: number;
};

export const defaultAttachmentConfig: AttachmentConfig = {
  enabled: true,
  formats: ['.pdf', '.docx', '.xlsx', '.csv', '.tsv', '.txt', '.md', '.html', '.json'],
  max_upload_bytes: 10 * 1024 * 1024,
  image_formats: ['.png', '.jpg', '.jpeg', '.webp'],
  image_max_upload_bytes: 10 * 1024 * 1024,
};

type AttachmentDestination = { conversationId?: string; baseId?: string; imageInput?: boolean };
type AttachmentUploadOptions = {
  signal: AbortSignal;
  onAttempt?: (id: string) => void;
  onProgress?: (percent: number) => void;
  onIndexing?: () => void;
};

function waitForIndexCheck(signal: AbortSignal) {
  signal.throwIfAborted();
  return new Promise<void>((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener('abort', abort);
      resolve();
    }, 1500);
    signal.addEventListener('abort', abort, { once: true });
  });
}

export async function uploadAttachment(
  file: File,
  { conversationId, baseId, imageInput = false }: AttachmentDestination,
  { signal, onAttempt, onProgress, onIndexing }: AttachmentUploadOptions,
): Promise<KnowledgeDocument> {
  signal.throwIfAborted();
  const destinationId = baseId || conversationId;
  if (!destinationId) throw new Error('자료 위치를 지정하세요.');
  const destination = baseId ? 'base_id' : 'conversation_id';
  const filename = file.name.slice(-240) || '첨부 파일';
  const attempt = await workflowJson<{ id: string }>('/uploads', {
    method: 'POST',
    body: JSON.stringify({ filename, [destination]: destinationId }),
    signal,
  });
  onAttempt?.(attempt.id);
  signal.throwIfAborted();

  const form = new FormData();
  form.set('file', file, filename);
  form.set(destination, destinationId);
  form.set('upload_id', attempt.id);
  if (imageInput) form.set('image_input', 'true');
  let document = await authenticatedUpload<KnowledgeDocument>('/api/knowledge/documents', form, signal, (loaded, total) => {
    if (total > 0) onProgress?.(Math.round(loaded / total * 100));
  });
  signal.throwIfAborted();
  if (imageInput) {
    if (document.status !== 'image') throw new Error('원본 이미지를 첨부하지 못했습니다.');
    return document;
  }
  if (document.status === 'ready' && !document.needs_reindex) return document;

  onIndexing?.();
  if (document.status === 'error' || document.needs_reindex) {
    document = await knowledgeJson<KnowledgeDocument>(`/documents/${encodeURIComponent(document.id)}/reindex`, { method: 'POST', signal });
  }
  const query = new URLSearchParams({ [destination]: destinationId });
  for (let round = 0; document.status !== 'ready' || document.needs_reindex; round++) {
    if (document.status === 'error') throw new Error(document.error || '문서 색인에 실패했습니다.');
    if (round >= 240) throw new Error('색인이 오래 걸리고 있습니다. 자료 설정에서 상태를 확인하세요.');
    await waitForIndexCheck(signal);
    const { items } = await knowledgeJson<{ items: KnowledgeDocument[] }>(`/documents?${query}`, { signal });
    const indexed = items.find((item) => item.id === document.id);
    if (!indexed) throw new Error('첨부 파일이 삭제되었거나 만료되었습니다.');
    document = indexed;
  }
  signal.throwIfAborted();
  return document;
}

export async function cancelAttachmentUpload(id: string): Promise<void> {
  await workflowJson(`/uploads/${encodeURIComponent(id)}/cancel`, { method: 'POST', signal: AbortSignal.timeout(5000) });
}
