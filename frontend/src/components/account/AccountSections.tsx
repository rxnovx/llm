"use client";
import type { FormEvent } from "react";
import { useEffect, useRef, useState } from "react";
import { GoogleSignInButton } from "@/components/auth/GoogleSignInButton";
import { UserAvatar } from "@/components/shared/UserAvatar";
import { normalizeAvatarUrl, avatarProfileValue } from "@/lib/avatar-url";
import { api } from "@/lib/api";
import { getAuthSessionVersion } from "@/lib/auth";
import type { AuthUser, AuthMessage, AccountSession } from "@/types/auth";
const fieldClass = "account-input";
function formatDate(timestamp: number | null) {
    return timestamp ? new Intl.DateTimeFormat("ko-KR", {dateStyle:"medium", timeStyle:"short"}).format(new Date(timestamp*1000)) : "기록 없음";
}
function Card({
  title,
  description,
  children,
}: {
  title: string;
  description: string;
  children: React.ReactNode;
}) {
  return (
    <section className={"account-card"}>
      <h2 className="text-lg font-semibold">{title}</h2>
      <p className="mt-1 text-sm leading-6 text-[var(--fg-muted)]">
        {description}
      </p>
      <div className="mt-5">{children}</div>
    </section>
  );
}

export function AvatarSection({
  user,
  onSuccess,
  onError,
}: {
  user: AuthUser;
  onSuccess: (message: string) => Promise<void>;
  onError: (error: unknown, fallback: string) => void;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [avatarUrl, setAvatarUrl] = useState(normalizeAvatarUrl(user.avatar_url) ?? "");
  const [uploading, setUploading] = useState(false);
  const [savingUrl, setSavingUrl] = useState(false);
  const [removing, setRemoving] = useState(false);

  useEffect(() => {
    const timer = window.setTimeout(
      () => setAvatarUrl(normalizeAvatarUrl(user.avatar_url) ?? ""),
      0,
    );
    return () => window.clearTimeout(timer);
  }, [user.avatar_url]);

  const previewUrl = normalizeAvatarUrl(avatarUrl.trim()) ?? normalizeAvatarUrl(user.avatar_url);
  const busy = uploading || savingUrl || removing;

  const upload = async (file: File) => {
    if (file.size > 5 * 1024 * 1024) {
      onError(new Error("프로필 사진은 5MB 이하여야 합니다."), "");
      return;
    }

    const formData = new FormData();
    formData.append("file", file);
    setUploading(true);
    try {
      await api.post<AuthUser>("/users/me/avatar", formData);
      await onSuccess("프로필 사진을 업로드했습니다.");
    } catch (caught) {
      onError(caught, "프로필 사진을 업로드하지 못했습니다.");
    } finally {
      setUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = "";
    }
  };

  const saveUrl = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSavingUrl(true);
    try {
      await api.patch<AuthUser>("/users/me", {
        avatar_url: avatarProfileValue(avatarUrl),
      });
      await onSuccess(
        avatarUrl.trim()
          ? "프로필 사진 링크를 저장했습니다."
          : "프로필 사진을 제거했습니다.",
      );
    } catch (caught) {
      onError(caught, "프로필 사진 링크를 저장하지 못했습니다.");
    } finally {
      setSavingUrl(false);
    }
  };

  const remove = async () => {
    setRemoving(true);
    try {
      await api.patch<AuthUser>("/users/me", { avatar_url: null });
      setAvatarUrl("");
      await onSuccess("프로필 사진을 제거했습니다.");
    } catch (caught) {
      onError(caught, "프로필 사진을 제거하지 못했습니다.");
    } finally {
      setRemoving(false);
    }
  };

  return (
    <Card
      title="프로필 사진"
      description="이미지를 직접 업로드하거나 공개 이미지 주소를 연결할 수 있습니다."
    >
      <div className="grid gap-5 sm:grid-cols-[auto_minmax(0,1fr)] sm:items-start">
        <UserAvatar
          src={previewUrl}
          name={user.display_name || user.username}
          className="size-24 rounded-[var(--radius-xl)] border border-[var(--border)] text-3xl shadow-[var(--shadow-sm)]"
        />
        <div className="min-w-0 space-y-4">
          <div className="flex flex-wrap items-center gap-2">
            <input
              ref={fileInputRef}
              type="file"
              accept="image/png,image/jpeg,image/gif,image/webp,image/avif,image/bmp"
              className="sr-only"
              onChange={(event) => {
                const file = event.target.files?.[0];
                if (file) void upload(file);
              }}
            />
            <button
              type="button"
              disabled={busy}
              onClick={() => fileInputRef.current?.click()}
              className="h-10 rounded-[var(--radius-lg)] bg-[var(--primary)] px-4 text-sm font-semibold text-[var(--on-primary)] disabled:opacity-60"
            >
              {uploading ? "업로드 중..." : "사진 업로드"}
            </button>
            <button
              type="button"
              disabled={busy || !user.avatar_url}
              onClick={() => void remove()}
              className="h-10 rounded-[var(--radius-lg)] border border-[var(--border)] px-4 text-sm font-semibold hover:bg-[var(--surface-hover)] disabled:opacity-50"
            >
              {removing ? "제거 중..." : "사진 제거"}
            </button>
            <span className="text-xs text-[var(--fg-subtle)]">
              PNG, JPG, GIF, WebP, AVIF, BMP · 최대 5MB
            </span>
          </div>

          <form onSubmit={saveUrl} className="space-y-2">
            <label htmlFor="avatar-url" className="text-sm font-medium">
              이미지 링크
            </label>
            <div className="flex flex-col gap-2 sm:flex-row">
              <input
                id="avatar-url"
                type="text"
                inputMode="url"
                value={avatarUrl}
                onChange={(event) => setAvatarUrl(event.target.value)}
                maxLength={2048}
                placeholder="https://example.com/profile.jpg"
                className={fieldClass}
              />
              <button
                disabled={busy}
                className="h-11 shrink-0 rounded-[var(--radius-lg)] border border-[var(--border)] px-4 text-sm font-semibold hover:bg-[var(--surface-hover)] disabled:opacity-60"
              >
                {savingUrl ? "저장 중..." : "링크 저장"}
              </button>
            </div>
            <p className="text-xs leading-5 text-[var(--fg-subtle)]">
              외부 링크를 사용하면 이미지는 해당 사이트에서 직접 불러옵니다.
            </p>
          </form>
        </div>
      </div>
    </Card>
  );
}

export function ProfileSection({
  user,
  onSuccess,
  onError,
}: {
  user: AuthUser;
  onSuccess: (message: string) => Promise<void>;
  onError: (error: unknown, fallback: string) => void;
}) {
  const [displayName, setDisplayName] = useState(user.display_name ?? "");
  const [saving, setSaving] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    try {
      await api.patch<AuthUser>("/users/me", {
        display_name: displayName.trim() || null,
      });
      await onSuccess("프로필을 저장했습니다.");
    } catch (caught) {
      onError(caught, "프로필을 저장하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title="프로필"
      description="표시 이름은 자유롭게 바꿀 수 있으며 로그인 사용자명은 계정 보호를 위해 고정됩니다."
    >
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        <label className="space-y-2 text-sm font-medium">
          표시 이름
          <input
            value={displayName}
            onChange={(event) => setDisplayName(event.target.value)}
            maxLength={100}
            className={fieldClass}
          />
        </label>
        <label className="space-y-2 text-sm font-medium">
          사용자명
          <input
            value={user.username}
            readOnly
            aria-readonly="true"
            className={`${fieldClass} cursor-not-allowed bg-[var(--surface-soft)] text-[var(--fg-muted)]`}
          />
          <span className="block text-xs font-normal leading-5 text-[var(--fg-subtle)]">
            로그인에 사용되므로 가입 후 변경할 수 없습니다.
          </span>
        </label>
        <label className="space-y-2 text-sm font-medium sm:col-span-2">
          현재 로그인 이메일
          <input
            type="email"
            value={user.email}
            readOnly
            aria-readonly="true"
            className={`${fieldClass} cursor-not-allowed bg-[var(--surface-soft)] text-[var(--fg-muted)]`}
          />
          <span className="block text-xs font-normal leading-5 text-[var(--fg-subtle)]">
            이메일은 설정의 계정 및 보안에서 변경할 수 있습니다.
          </span>
        </label>
        <div className="text-xs leading-5 text-[var(--fg-subtle)] sm:col-span-2">
          가입 {formatDate(user.created_at)} · 최근 로그인{" "}
          {formatDate(user.last_login_at)}
        </div>
        <button
          disabled={saving}
          className="h-11 rounded-[var(--radius-lg)] bg-[var(--primary)] text-sm font-semibold text-[var(--on-primary)] disabled:opacity-60 sm:col-start-2"
        >
          {saving ? "저장 중..." : "프로필 저장"}
        </button>
      </form>
    </Card>
  );
}

export function EmailChangeSection({
  user,
  onSuccess,
  onError,
}: {
  user: AuthUser;
  onSuccess: (message: string) => void;
  onError: (error: unknown, fallback: string) => void;
}) {
  const [newEmail, setNewEmail] = useState("");
  const [currentPassword, setCurrentPassword] = useState("");
  const [saving, setSaving] = useState(false);
  const googleConnected = user.connected_providers.includes("google");

  const requestChange = async (
    reauthentication:
      | { current_password: string }
      | { google_credential: string },
  ) => {
    const normalizedEmail = newEmail.trim().toLowerCase();
    if (!normalizedEmail) {
      onError(new Error("새 이메일을 입력해주세요."), "");
      return;
    }
    if (normalizedEmail === user.email) {
      onError(new Error("현재 이메일과 다른 주소를 입력해주세요."), "");
      return;
    }

    setSaving(true);
    try {
      const response = await api.post<AuthMessage>(
        "/users/me/email-change/request",
        {
          new_email: normalizedEmail,
          ...reauthentication,
        },
      );
      setNewEmail("");
      setCurrentPassword("");
      onSuccess(response.data.message);
    } catch (caught) {
      onError(caught, "이메일 변경 확인 메일을 보내지 못했습니다.");
    } finally {
      setSaving(false);
    }
  };

  const submitWithPassword = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    await requestChange({ current_password: currentPassword });
  };

  return (
    <Card
      title="로그인 이메일 변경"
      description="현재 계정으로 다시 인증한 뒤 새 이메일의 확인 링크를 열어야 변경이 완료됩니다."
    >
      <div className="space-y-4">
        <label className="block space-y-2 text-sm font-medium">
          새 이메일
          <input
            type="email"
            value={newEmail}
            onChange={(event) => setNewEmail(event.target.value)}
            autoComplete="email"
            className={fieldClass}
            required
          />
        </label>

        {user.has_password && (
          <form
            onSubmit={submitWithPassword}
            className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_auto]"
          >
            <label className="space-y-2 text-sm font-medium">
              현재 비밀번호로 확인
              <input
                type="password"
                value={currentPassword}
                onChange={(event) => setCurrentPassword(event.target.value)}
                autoComplete="current-password"
                className={fieldClass}
                required
              />
            </label>
            <button
              disabled={saving || !newEmail.trim() || !currentPassword}
              className="h-11 self-end rounded-[var(--radius-lg)] bg-[var(--primary)] px-4 text-sm font-semibold text-[var(--on-primary)] disabled:opacity-60"
            >
              {saving ? "전송 중..." : "확인 메일 보내기"}
            </button>
          </form>
        )}

        {googleConnected && (
          <div className="space-y-2">
            {user.has_password && (
              <p className="text-center text-xs text-[var(--fg-subtle)]">
                또는
              </p>
            )}
            <p className="text-sm font-medium">연결된 Google 계정으로 확인</p>
            <div
              className={
                saving || !newEmail.trim()
                  ? "pointer-events-none opacity-50"
                  : ""
              }
            >
              <GoogleSignInButton
                onCredential={(credential) =>
                  requestChange({ google_credential: credential })
                }
                onError={(caught) =>
                  onError(caught, "Google 재인증을 시작하지 못했습니다.")
                }
              />
            </div>
          </div>
        )}

        <p className="rounded-[var(--radius-lg)] bg-[var(--surface-soft)] px-3 py-2 text-xs leading-5 text-[var(--fg-muted)]">
          확인 링크를 열기 전까지 기존 이메일로 로그인할 수 있습니다. 변경이
          완료되면 모든 기기의 로그인 세션이 자동으로
          해제됩니다.
        </p>
      </div>
    </Card>
  );
}

export function EmailVerificationSection({
  user,
  onSuccess,
  onError,
}: {
  user: AuthUser;
  onSuccess: (message: string) => void;
  onError: (error: unknown, fallback: string) => void;
}) {
  if (!user.email_verification_required || user.is_email_verified) return null;
  return (
    <Card
      title="이메일 인증"
      description="현재 이메일을 인증해야 다음 로그인부터 계정을 사용할 수 있습니다."
    >
      <button
        onClick={async () => {
          try {
            const response = await api.post<AuthMessage>(
              "/auth/email-verification/request",
            );
            onSuccess(response.data.message);
          } catch (caught) {
            onError(caught, "인증 안내를 보내지 못했습니다.");
          }
        }}
        className="rounded-[var(--radius-lg)] border border-[var(--border)] px-4 py-2.5 text-sm font-semibold"
      >
        인증 안내 다시 받기
      </button>
    </Card>
  );
}


export function OAuthConnectionSection({
  user,
  onSuccess,
  onError,
}: {
  user: AuthUser;
  onSuccess: (message: string) => Promise<void>;
  onError: (error: unknown, fallback: string) => void;
}) {
  const connected = user.connected_providers.includes("google");
  const [busy, setBusy] = useState(false);

  const link = async (credential: string) => {
    setBusy(true);
    try {
      await api.post("/users/me/oauth/google", {
        credential,
        accept_terms: false,
      });
      await onSuccess("Google 계정을 연결했습니다.");
    } catch (caught) {
      onError(caught, "Google 계정을 연결하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  const unlink = async () => {
    setBusy(true);
    try {
      await api.delete("/users/me/oauth/google");
      await onSuccess("Google 계정 연결을 해제했습니다.");
    } catch (caught) {
      onError(caught, "Google 계정 연결을 해제하지 못했습니다.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <Card
      title="연결된 로그인"
      description="Google 계정을 연결하면 비밀번호 없이 안전하게 로그인할 수 있습니다."
    >
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div>
          <p className="text-sm font-semibold">Google</p>
          <p className="mt-1 text-xs text-[var(--fg-subtle)]">
            {connected ? "연결됨" : "연결되지 않음"}
          </p>
        </div>
        {connected ? (
          <button
            type="button"
            disabled={busy || (!user.has_password && user.connected_providers.length <= 1)}
            onClick={() => void unlink()}
            className="h-10 rounded-[var(--radius-lg)] border border-[var(--border)] px-4 text-sm font-semibold disabled:opacity-50"
            title={
              !user.has_password && user.connected_providers.length <= 1
                ? "먼저 비밀번호를 설정해주세요."
                : undefined
            }
          >
            {busy ? "처리 중..." : "연결 해제"}
          </button>
        ) : (
          <div className={busy ? "pointer-events-none opacity-60" : ""}>
            <GoogleSignInButton
              onCredential={link}
              onError={(error) =>
                onError(error, "Google 로그인 모듈을 불러오지 못했습니다.")
              }
            />
          </div>
        )}
      </div>
    </Card>
  );
}

export function PasswordSection({
  hasPassword,
  onSuccess,
  onError,
}: {
  hasPassword: boolean;
  onSuccess: (sessionVersion: number) => Promise<void>;
  onError: (error: unknown, fallback: string) => void;
}) {
  const [currentPassword, setCurrentPassword] = useState("");
  const [newPassword, setNewPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [saving, setSaving] = useState(false);

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (newPassword !== confirm) {
      onError(new Error("새 비밀번호가 일치하지 않습니다."), "");
      return;
    }
    setSaving(true);
    const sessionVersion = getAuthSessionVersion();
    try {
      await api.post(
        hasPassword ? "/users/me/password" : "/users/me/password/set",
        hasPassword
          ? { current_password: currentPassword, new_password: newPassword }
          : { new_password: newPassword },
      );
      await onSuccess(sessionVersion);
    } catch (caught) {
      onError(caught, "비밀번호를 변경하지 못했습니다.");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Card
      title={hasPassword ? "비밀번호" : "비밀번호 설정"}
      description={
        hasPassword
          ? "변경하면 모든 기기의 로그인 세션이 즉시 해제됩니다."
          : "Google 로그인 외에도 사용할 LLM 비밀번호를 설정합니다."
      }
    >
      <form onSubmit={submit} className="grid gap-4 sm:grid-cols-2">
        {hasPassword && (
        <label className="space-y-2 text-sm font-medium sm:col-span-2">
          현재 비밀번호
          <input
            type="password"
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            autoComplete="current-password"
            className={fieldClass}
            required={hasPassword}
          />
        </label>
        )}
        <label className="space-y-2 text-sm font-medium">
          새 비밀번호
          <input
            type="password"
            value={newPassword}
            onChange={(event) => setNewPassword(event.target.value)}
            minLength={10}
            autoComplete="new-password"
            className={fieldClass}
            required
          />
        </label>
        <label className="space-y-2 text-sm font-medium">
          새 비밀번호 확인
          <input
            type="password"
            value={confirm}
            onChange={(event) => setConfirm(event.target.value)}
            minLength={10}
            autoComplete="new-password"
            className={fieldClass}
            required
          />
        </label>
        <button
          disabled={saving}
          className="h-11 rounded-[var(--radius-lg)] border border-[var(--border)] text-sm font-semibold disabled:opacity-60 sm:col-start-2"
        >
          {saving
            ? "저장 중..."
            : hasPassword
              ? "비밀번호 변경"
              : "비밀번호 설정"}
        </button>
      </form>
    </Card>
  );
}

export function SessionSection({
  sessions,
  onRevoke,
}: {
  sessions: AccountSession[];
  onRevoke: (session: AccountSession) => Promise<void>;
}) {
  const [confirmId, setConfirmId] = useState<string | null>(null);
  return (
    <Card title="로그인 세션" description="현재 로그인된 기기를 확인하고 원격으로 해제합니다.">
      <div className="divide-y divide-[var(--border)]">
        {sessions.map((session) => (
          <div key={session.id} className="flex flex-col gap-3 py-4 first:pt-0 sm:flex-row sm:items-center">
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-medium">
                {session.user_agent || "알 수 없는 기기"}
                {session.is_current && (
                  <span className="ml-2 text-xs text-[var(--success)]">현재 세션</span>
                )}
              </p>
              <p className="mt-1 text-xs text-[var(--fg-subtle)]">
                {session.ip_address || "IP 없음"} · 최근 사용{" "}
                {formatDate(session.last_used_at)}
              </p>
            </div>
            {confirmId === session.id ? (
              <div className="flex gap-2 text-xs">
                <button
                  onClick={() => void onRevoke(session)}
                  className="rounded-[var(--radius-md)] bg-[var(--error)] px-3 py-2 font-semibold text-[var(--on-error)]"
                >
                  해제 확인
                </button>
                <button onClick={() => setConfirmId(null)} className="px-2">
                  취소
                </button>
              </div>
            ) : (
              <button
                onClick={() => setConfirmId(session.id)}
                className="self-start rounded-[var(--radius-md)] border border-[var(--border)] px-3 py-2 text-xs font-semibold"
              >
                세션 해제
              </button>
            )}
          </div>
        ))}
        {!sessions.length && (
          <p className="text-sm text-[var(--fg-muted)]">활성 세션이 없습니다.</p>
        )}
      </div>
    </Card>
  );
}


export function DangerSection({
  hasPassword,
  onDeactivate,
}: {
  hasPassword: boolean;
  onDeactivate: (password: string) => Promise<void>;
}) {
  const [password, setPassword] = useState("");
  const [confirming, setConfirming] = useState(false);
  return (
    <Card
      title="계정 비활성화"
      description="계정 정보를 보존하고 모든 로그인 세션을 해제합니다. 이메일 계정 복구로 다시 활성화할 수 있습니다."
    >
      {!hasPassword ? (
        <p className="text-sm text-[var(--fg-muted)]">
          계정을 비활성화하려면 먼저 비밀번호를 설정해주세요.
        </p>
      ) : (
      <div className="flex flex-col gap-3 sm:flex-row">
        <input
          aria-label="현재 비밀번호"
          type="password"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
          placeholder="현재 비밀번호"
          autoComplete="current-password"
          className={fieldClass}
        />
        {confirming ? (
          <div className="flex shrink-0 gap-2">
            <button
              disabled={!password}
              onClick={() => void onDeactivate(password)}
              className="rounded-[var(--radius-lg)] bg-[var(--error)] px-4 py-2.5 text-sm font-semibold text-[var(--on-error)] disabled:opacity-50"
            >
              비활성화 확인
            </button>
            <button onClick={() => setConfirming(false)} className="px-2 text-sm">
              취소
            </button>
          </div>
        ) : (
          <button
            disabled={!password}
            onClick={() => setConfirming(true)}
            className="shrink-0 rounded-[var(--radius-lg)] border border-[color-mix(in_srgb,var(--error)_40%,var(--border))] px-4 py-2.5 text-sm font-semibold text-[var(--error)] disabled:opacity-50"
          >
            계정 비활성화
          </button>
        )}
      </div>
      )}
    </Card>
  );
}
