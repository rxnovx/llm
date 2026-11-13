'use client';

import { useEffect, useRef, useState } from 'react';
import { useDialogDismiss } from '@/components/shared/useDialogDismiss';
import { downloadResult, workflowJson, type ResultFile } from '@/lib/workflows';
import { AnswerAudio } from './AnswerAudio';
import { AnswerImage, resultImageTypes } from './AnswerImage';
import { MessageContent } from './MessageContent';

type Preview = ResultFile & {
  content: string;
  columns: string[];
  rows: (string | number | boolean | null)[][];
  preview_truncated: boolean;
  html_preview?: string;
  kind?: 'table_analysis';
};

type Props = {
  messageId: string;
  files?: ResultFile[];
  onChange: (files: ResultFile[]) => void;
  onReferenceImage?: (file: ResultFile) => void;
  referenceDisabled?: boolean;
};

export function AnswerFiles({ messageId, files = [], onChange, onReferenceImage, referenceDisabled }: Props) {
  const [formats, setFormats] = useState<string[]>([]);
  const [format, setFormat] = useState('md');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [preview, setPreview] = useState<Preview | null>(null);
  const dialog = useRef<HTMLDialogElement>(null);
  const closePreview = () => setPreview(null);
  const dismiss = useDialogDismiss(closePreview);

  useEffect(() => {
    const abort = new AbortController();
    void workflowJson<{ formats: string[] }>('/formats', { signal: abort.signal })
      .then(data => setFormats(data.formats)).catch(() => {});
    return () => abort.abort();
  }, []);
  useEffect(() => {
    if (preview) dialog.current?.showModal();
    else dialog.current?.close();
  }, [preview]);

  const action = async (work: () => Promise<void>) => {
    if (busy) return;
    setBusy(true);
    setError('');
    try {
      await work();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '파일을 처리하지 못했습니다.');
    } finally {
      setBusy(false);
    }
  };

  return <section className="cw-result-files" aria-label="결과 파일">
    {files.length > 0 && <ul>{files.map(file => {
      const mimeType = resultImageTypes.get(file.format);
      const previewLabel = file.format === 'wav' ? '재생' : '미리보기';
      const metadata = <>
        <strong>{file.name}</strong>
        <small>{file.format.toUpperCase()} · {(file.size / 1024).toFixed(1)} KB{!mimeType && ` · ${previewLabel}`}</small>
      </>;
      return <li key={file.id} className={mimeType ? 'cw-result-image-file' : undefined}>
        {mimeType ? <>
          <AnswerImage file={file} mimeType={mimeType} />
          <div className="cw-file-preview">{metadata}</div>
        </> : <button type="button" className="cw-file-preview" disabled={busy}
          onClick={() => void action(async () => {
            setPreview(await workflowJson<Preview>(`/artifacts/${file.id}`));
          })}>{metadata}</button>}
        {mimeType && onReferenceImage && <button type="button" className="cw-text-button" disabled={busy || referenceDisabled}
          onClick={() => onReferenceImage(file)}>이 이미지 참조</button>}
        <button type="button" className="cw-text-button" disabled={busy}
          onClick={() => void action(() => downloadResult(file))}>다운로드</button>
        <button type="button" className="cw-text-button" disabled={busy} aria-label={`${file.name} 삭제`}
          onClick={() => void action(async () => {
            await workflowJson(`/artifacts/${file.id}`, { method: 'DELETE' });
            onChange(files.filter(item => item.id !== file.id));
          })}>삭제</button>
      </li>;
    })}</ul>}
    <details className="cw-export">
      <summary>답변을 파일로 저장</summary>
      <div>
        <label className="cw-sr-only" htmlFor={`export-${messageId}`}>저장 형식</label>
        <select id={`export-${messageId}`} value={format} onChange={event => setFormat(event.target.value)}>
          {formats.map(value => <option key={value} value={value}>{value.toUpperCase()}</option>)}
        </select>
        <button className="cw-small-button" disabled={busy || !formats.length} onClick={() => void action(async () => {
          const file = await workflowJson<ResultFile>(`/messages/${messageId}/export`, {
            method: 'POST', body: JSON.stringify({ format, name: '대화 결과' }),
          });
          onChange([...files, file]);
        })}>{busy ? '생성 중…' : '파일 만들기'}</button>
      </div>
      <small>표가 있으면 CSV·XLSX에 첫 표를 저장합니다. 그 외에는 본문을 행별로 저장합니다.</small>
    </details>
    {error && !preview && <p className="cw-inline-error" role="alert">{error}</p>}
    <dialog ref={dialog} className="cw-dialog cw-file-dialog" aria-label="결과 파일 미리보기" {...dismiss} onClose={closePreview}>
      {preview && <>
        <header className="cw-dialog-header">
          <div><span className="cw-overline">결과 파일</span><h2>{preview.name}</h2></div>
          <button className="cw-small-button" onClick={closePreview}>닫기</button>
        </header>
        <div className="cw-file-body">
          {preview.html_preview && <>
            <iframe className="cw-result-html" title={`${preview.name} 미리보기`}
              sandbox="" referrerPolicy="no-referrer" srcDoc={preview.html_preview} />
            {preview.kind !== 'table_analysis' && <p className="cw-result-note">슬라이드의 스크립트 동작은 다운로드한 파일에서 사용할 수 있습니다.</p>}
          </>}
          {preview.format === 'wav' && <AnswerAudio key={preview.id} file={preview} />}
          {preview.content && (preview.format === 'md' ? <MessageContent content={preview.content} /> : <pre>{preview.content}</pre>)}
          {preview.rows.length > 0 && <div className="cw-table-scroll"><table>
            <thead><tr>{preview.columns.map((column, index) => <th key={index}>{column}</th>)}</tr></thead>
            <tbody>{preview.rows.map((row, index) => <tr key={index}>
              {row.map((cell, column) => <td key={column}>{cell === null ? '' : String(cell)}</td>)}
            </tr>)}</tbody>
          </table></div>}
          {preview.preview_truncated && <p>미리보기는 본문 20,000자·표 100행까지 표시합니다. 전체 내용은 파일을 다운로드하세요.</p>}
          {error && <p className="cw-inline-error" role="alert">{error}</p>}
        </div>
        <footer className="cw-dialog-footer">
          <small>SHA-256 {preview.sha256.slice(0, 16)}…</small>
          <button className="cw-primary-button" disabled={busy}
            onClick={() => void action(() => downloadResult(preview))}>다운로드</button>
        </footer>
      </>}
    </dialog>
  </section>;
}
