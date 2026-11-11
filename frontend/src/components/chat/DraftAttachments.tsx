"use client";

import { useEffect, useRef, useState } from "react";
import { Icon } from "./Icons";

export type DraftAttachment = {
  id: string;
  file: File;
  isImage: boolean;
  progress?: { stage: "uploading" | "indexing" | "ready"; percent?: number };
};

const imageTypes = new Map([
  ["png", "image/png"], ["jpg", "image/jpeg"], ["jpeg", "image/jpeg"],
  ["webp", "image/webp"], ["tif", "image/tiff"], ["tiff", "image/tiff"],
]);
const rasterTypes = new Set(imageTypes.values());

function Thumbnail({ file, isImage }: Pick<DraftAttachment, "file" | "isImage">) {
  const image = useRef<HTMLImageElement>(null);
  const [failedFile, setFailedFile] = useState<File | null>(null);
  const mimeType = rasterTypes.has(file.type) ? file.type :
    !file.type.startsWith("image/") ? imageTypes.get(file.name.split(".").at(-1)?.toLowerCase() ?? "") : undefined;
  const showImage = isImage && !!mimeType && failedFile !== file;

  useEffect(() => {
    const element = image.current;
    if (!showImage || !element) return;
    const blob = file.type === mimeType ? file : file.slice(0, file.size, mimeType);
    const url = URL.createObjectURL(blob);
    element.src = url;
    return () => {
      element.removeAttribute("src");
      URL.revokeObjectURL(url);
    };
  }, [file, mimeType, showImage]);

  return <span className="cw-draft-attachment-preview">
    {showImage ? (
      // eslint-disable-next-line @next/next/no-img-element -- A local Blob URL belongs to this draft.
      <img ref={image} alt={file.name} decoding="async" draggable={false} onError={() => setFailedFile(file)} />
    ) : <Icon name="file" size={19} />}
  </span>;
}

export function DraftAttachments({ items, disabled, onRemove }: {
  items: DraftAttachment[];
  disabled: boolean;
  onRemove: (id: string) => void;
}) {
  if (!items.length) return null;
  return <ul className="cw-draft-attachments" aria-label="첨부 미리보기">
    {items.map(({ id, file, isImage, progress }) => (
      <li key={id} className="cw-draft-attachment">
        <Thumbnail file={file} isImage={isImage} />
        <div className="cw-draft-attachment-details">
          <span title={file.name}>{file.name}</span>
          {progress?.stage === "uploading" && <progress max={100} value={progress.percent} aria-label={`${file.name} 전송률`} />}
          {progress?.stage === "indexing" && <small>색인 중…</small>}
          {progress?.stage === "ready" && <small>전송 준비됨</small>}
        </div>
        <button type="button" className="cw-icon-button" aria-label={`${file.name} 제거`} disabled={disabled} onClick={() => onRemove(id)}>
          <Icon name="close" size={14} />
        </button>
      </li>
    ))}
  </ul>;
}
