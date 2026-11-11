'use client';

import { useEffect, useRef, useState } from 'react';
import { resultFileBlob, type ResultFile } from '@/lib/workflows';

export const resultImageTypes = new Map([
  ['png', 'image/png'],
  ['jpg', 'image/jpeg'],
  ['webp', 'image/webp'],
]);

type Preview =
  | { status: 'loading' }
  | { status: 'ready'; url: string }
  | { status: 'error' };

export function AnswerImage({ file, mimeType }: { file: ResultFile; mimeType: string }) {
  const { id, name, size } = file;
  const container = useRef<HTMLDivElement>(null);
  const [preview, setPreview] = useState<Preview>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    let url: string | undefined;
    async function load() {
      try {
        const blob = await resultFileBlob(id, abort.signal);
        if (abort.signal.aborted) return;
        if (blob.type !== mimeType || blob.size === 0 || blob.size !== size) {
          setPreview({ status: 'error' });
          return;
        }
        url = URL.createObjectURL(blob);
        setPreview({ status: 'ready', url });
      } catch {
        if (!abort.signal.aborted) setPreview({ status: 'error' });
      }
    }

    let observer: IntersectionObserver | undefined;
    if (container.current && typeof IntersectionObserver !== 'undefined') {
      observer = new IntersectionObserver(([entry], observer) => {
        if (!entry?.isIntersecting) return;
        observer.disconnect();
        if (!abort.signal.aborted) void load();
      });
      observer.observe(container.current);
    } else {
      void load();
    }
    return () => {
      observer?.disconnect();
      abort.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [id, mimeType, size, attempt]);

  return <div className="cw-result-image" ref={container}>
    {preview.status === 'ready' && (
      // 인증된 결과 파일을 브라우저에서 Blob URL로 표시합니다.
      // eslint-disable-next-line @next/next/no-img-element
      <img src={preview.url} alt={name} decoding="async" onError={() => {
        URL.revokeObjectURL(preview.url);
        setPreview({ status: 'error' });
      }} />
    )}
    {preview.status === 'loading' && <p className="cw-result-image-status" role="status">이미지를 불러오는 중…</p>}
    {preview.status === 'error' && <div className="cw-result-image-status">
      <p className="cw-inline-error" role="alert">이미지를 불러오지 못했습니다.</p>
      <button type="button" className="cw-text-button" onClick={() => {
        setPreview({ status: 'loading' });
        setAttempt(value => value + 1);
      }}>다시 불러오기</button>
    </div>}
  </div>;
}
