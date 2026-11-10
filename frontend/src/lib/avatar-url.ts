/** Uploaded avatars are served through the web origin, never a Docker hostname. */
const UPLOADED_AVATAR = /\/(?:users\/avatars|api\/avatars)\/([a-f0-9]{24})\/([a-f0-9]{32}\.[a-z0-9]{1,10})$/i;

export function normalizeAvatarUrl(value: string | null | undefined): string | null {
  if (!value) return null;
  try {
    const url = new URL(value, "https://avatar.invalid");
    if (!["http:", "https:"].includes(url.protocol) || url.username || url.password) return null;
    const match = url.pathname.match(UPLOADED_AVATAR);
    if (match) return `/api/avatars/${match[1]}/${match[2]}`;
    return /^https?:\/\//i.test(value) ? value : null;
  } catch { return null; }
}

/** The profile API accepts its canonical path; a browser proxy URL is display-only. */
export function avatarProfileValue(value: string): string | null {
  const normalized = value.trim();
  const match = normalized.match(/^\/api\/avatars\/([a-f0-9]{24})\/([a-f0-9]{32}\.[a-z0-9]{1,10})$/i);
  return match ? `/users/avatars/${match[1]}/${match[2]}` : normalized || null;
}
