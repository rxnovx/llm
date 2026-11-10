"use client";
import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/authStore";
import { api } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/api-error";
import { AvatarSection, ProfileSection, EmailChangeSection, EmailVerificationSection,
  OAuthConnectionSection, PasswordSection, SessionSection, DangerSection } from "./AccountSections";
import { ThemeToggle } from "@/components/shared/ThemeToggle";
import type { AccountSession } from "@/types/auth";

export function AccountPage() {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const initialized = useAuthStore((s) => s.initialized);
  const [tab, setTab] = useState<"profile" | "security">("profile");
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [sessions, setSessions] = useState<AccountSession[]>([]);
  const [loggingOut, setLoggingOut] = useState(false);
  const fail = (caught: unknown, fallback: string) => { setMessage(""); setError(getApiErrorMessage(caught, fallback)); };
  useEffect(() => {
    if (!user || tab !== "security") return;
    let cancelled = false;
    void api.get<AccountSession[]>("/users/me/sessions").then(({ data }) => { if (!cancelled) setSessions(data); })
      .catch((e) => { if (!cancelled) setError(getApiErrorMessage(e, "로그인 세션을 불러오지 못했습니다.")); });
    return () => { cancelled = true; };
  }, [user, tab]);
  const success = async (text: string) => {
    await useAuthStore.getState().refreshUser();
    setError(""); setMessage(text);
  };
  const logout = async (all = false) => {
    if (loggingOut) return;
    setLoggingOut(true);
    try { await useAuthStore.getState().logout(all); router.replace("/login"); }
    catch (caught) { fail(caught, "로그아웃을 완료하지 못했습니다. 다시 시도해주세요."); }
    finally { setLoggingOut(false); }
  };
  if (!initialized) return <main className="grid min-h-dvh place-items-center"><p role="status">계정 정보를 불러오는 중...</p></main>;
  if (!user) return <main className="grid min-h-dvh place-items-center px-5"><div className="space-y-5 text-center">
    <h1 className="text-2xl font-bold">LLM 계정</h1><p>계속하려면 로그인해주세요.</p>
    <Link className="account-button" href="/login">로그인</Link><Link className="ml-5 underline" href="/user/signup">회원가입</Link>
  </div></main>;
  return <main className="mx-auto max-w-4xl px-4 py-8 sm:px-6 sm:py-12">
    <header className="mb-8 flex flex-wrap items-center justify-between gap-4 border-b border-[var(--border)] pb-6">
      <div><Link href="/" className="text-xs font-semibold tracking-widest text-[var(--fg-subtle)]">LLM</Link>
        <h1 className="mt-2 text-2xl font-bold">계정 설정</h1><p className="mt-2 text-sm text-[var(--fg-muted)]">{user.display_name || user.username} · {user.email}</p></div>
      <div className="flex items-center gap-3"><ThemeToggle /><button disabled={loggingOut} onClick={() => void logout()} className="text-sm underline">로그아웃</button></div>
    </header>
    <nav aria-label="계정 설정" className="mb-6 flex gap-2">
      {([['profile','프로필'],['security','계정 및 보안']] as const).map(([id,label]) => <button key={id} aria-pressed={tab === id} onClick={() => setTab(id)}
        className={`rounded-lg px-4 py-2 text-sm font-semibold ${tab === id ? 'bg-[var(--primary)] text-[var(--on-primary)]' : 'text-[var(--fg-muted)]'}`}>{label}</button>)}
    </nav>
    {message && <p className="account-notice mb-5 text-[var(--success)]" role="status">{message}</p>}
    {error && <p className="account-notice mb-5 text-[var(--error)]" role="alert">{error}</p>}
    <div className="space-y-5">
      {tab === "profile" ? <><AvatarSection key={`avatar-${user.subject}`} user={user} onSuccess={success} onError={fail} />
        <ProfileSection key={`profile-${user.subject}`} user={user} onSuccess={success} onError={fail} /></> : <>
        <EmailVerificationSection user={user} onSuccess={setMessage} onError={fail} />
        <EmailChangeSection user={user} onSuccess={setMessage} onError={fail} />
        <OAuthConnectionSection user={user} onSuccess={success} onError={fail} />
        <PasswordSection hasPassword={user.has_password} onSuccess={async () => { await logout(); }} onError={fail} />
        <SessionSection sessions={sessions} onRevoke={async (session) => {
          try { await api.delete(`/users/me/sessions/${session.id}`);
            if (session.is_current) await logout();
            else { setSessions((old) => old.filter((s) => s.id !== session.id)); setMessage("로그인 세션을 해제했습니다."); }
          } catch (caught) { fail(caught, "세션을 해제하지 못했습니다."); }
        }} />
        <div className="flex justify-end"><button className="text-sm underline" disabled={loggingOut} onClick={() => void logout(true)}>모든 기기에서 로그아웃</button></div>
        <DangerSection hasPassword={user.has_password} onDeactivate={async (password) => {
          try { await api.post("/users/me/deactivate", { current_password: password }); await logout(); }
          catch (caught) { fail(caught, "계정을 비활성화하지 못했습니다."); }
        }} />
      </>}
    </div>
  </main>;
}
