"use client";

import { normalizeAvatarUrl } from "@/lib/avatar-url";
import { useState } from "react";

type UserAvatarProps = {
  src?: string | null;
  name: string;
  className?: string;
};

export function UserAvatar({
  src,
  name,
  className = "size-9 rounded-[var(--radius-control)]",
}: UserAvatarProps) {
  const imageUrl = normalizeAvatarUrl(src);
  const initial = name.trim().slice(0, 1).toUpperCase() || "L";

  return (
    <span
      className={[
        "relative inline-flex shrink-0 items-center justify-center overflow-hidden bg-[var(--primary)] font-bold text-[var(--on-primary)]",
        className,
      ].join(" ")}
      aria-label={`${name}의 프로필 사진`}
    >
      {imageUrl ? (
        <AvatarImage key={imageUrl} src={imageUrl} initial={initial} />
      ) : (
        <span aria-hidden="true">{initial}</span>
      )}
    </span>
  );
}

function AvatarImage({ src, initial }: { src: string; initial: string }) {
  const [failed, setFailed] = useState(false);
  if (failed) return <span aria-hidden="true">{initial}</span>;

  return (
    // 사용자 지정 외부 호스트도 허용해야 하므로 Next Image 대신 일반 이미지를 사용합니다.
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={src}
      alt=""
      className="size-full object-cover"
      loading="lazy"
      decoding="async"
      referrerPolicy="no-referrer"
      onError={() => setFailed(true)}
    />
  );
}
