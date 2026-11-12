'use client';

import { useEffect, useState } from 'react';
import { resultFileBlob, type ResultFile } from '@/lib/workflows';

type Playback = { status: 'loading' | 'error' } | { status: 'ready'; url: string };

export function AnswerAudio({ file }: { file: ResultFile }) {
  const { id, name, size } = file;
  const [playback, setPlayback] = useState<Playback>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    const abort = new AbortController();
    let url: string | undefined;
    async function load() {
      try {
        const blob = await resultFileBlob(id, abort.signal);
        if (abort.signal.aborted) return;
        if (blob.type !== 'audio/wav' || blob.size === 0 || blob.size !== size) {
          setPlayback({ status: 'error' });
          return;
        }
        url = URL.createObjectURL(blob);
        setPlayback({ status: 'ready', url });
      } catch {
        if (!abort.signal.aborted) setPlayback({ status: 'error' });
      }
    }
    void load();
    return () => {
      abort.abort();
      if (url) URL.revokeObjectURL(url);
    };
  }, [id, size, attempt]);

  if (playback.status === 'ready') {
    return <audio className="cw-result-audio" aria-label={name} controls preload="metadata"
      src={playback.url} onError={() => setPlayback({ status: 'error' })} />;
  }
  if (playback.status === 'loading') return <p role="status">오디오를 불러오는 중…</p>;
  return <div>
    <p className="cw-inline-error" role="alert">오디오를 재생하지 못했습니다.</p>
    <button type="button" className="cw-text-button" onClick={() => {
      setPlayback({ status: 'loading' });
      setAttempt(value => value + 1);
    }}>다시 불러오기</button>
  </div>;
}
