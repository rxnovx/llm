"use client";
import { useEffect, useState } from "react";
import { WorkspaceLink as Link } from "@/components/shared/WorkspaceLink";
import { useRouter } from "next/navigation";
import { useAuthStore } from "@/stores/authStore";
import { api } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/api-error";
import { AvatarSection, ProfileSection, EmailChangeSection, EmailVerificationSection,
  OAuthConnectionSection, PasswordSection, SessionSection, DangerSection } from "./AccountSections";
import { ThemeToggle } from "@/components/shared/ThemeToggle";
import type { AccountSession } from "@/types/auth";
import { Icon, type IconName } from "@/components/chat/Icons";
import { UserAvatar } from "@/components/shared/UserAvatar";
import { workspaceHref } from "@/lib/workspace-navigation";

type Section = "general" | "profile" | "security" | "sessions" | "account";
const sections: { id: Section; label: string; title: string; icon: IconName }[] = [
  { id: "general", label: "일반", title: "일반 설정", icon: "settings" },
  { id: "profile", label: "프로필", title: "프로필 설정", icon: "user" },
  { id: "security", label: "보안", title: "계정 및 보안", icon: "lock" },
  { id: "sessions", label: "로그인 기기", title: "로그인 기기", icon: "devices" },
  { id: "account", label: "계정", title: "계정 관리", icon: "user" },
];
export function AccountPage({ initialSection = "general" }: { initialSection?: Section }) {
  const router = useRouter();
  const user = useAuthStore((s) => s.user);
  const initialized = useAuthStore((s) => s.initialized);
  const [tab, setTab] = useState<Section>(initialSection);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [sessions, setSessions] = useState<AccountSession[]>([]);
  const [loggingOut, setLoggingOut] = useState(false);
  const [sessionsLoading, setSessionsLoading] = useState(false);
  const fail = (caught: unknown, fallback: string) => { setMessage(""); setError(getApiErrorMessage(caught, fallback)); };
  useEffect(() => {
    if (!user || tab !== "sessions") return;
    let cancelled = false;
    void api.get<AccountSession[]>("/users/me/sessions").then(({ data }) => { if (!cancelled) setSessions(data); })
      .catch((e) => { if (!cancelled) setError(getApiErrorMessage(e, "로그인 세션을 불러오지 못했습니다.")); })
      .finally(() => { if (!cancelled) setSessionsLoading(false); });
    return () => { cancelled = true; };
  }, [user, tab]);
  const success = async (text: string) => {
    await useAuthStore.getState().refreshUser();
    setError(""); setMessage(text);
  };
  const logout = async (all = false) => {
    if (loggingOut) return;
    setLoggingOut(true);
    try { await useAuthStore.getState().logout(all); router.replace(workspaceHref("/login", window.location.search), { scroll: false }); }
    catch (caught) { fail(caught, "로그아웃을 완료하지 못했습니다. 다시 시도해주세요."); }
    finally { setLoggingOut(false); }
  };
  if (!initialized) return <section className="settings-access"><p role="status">계정 정보를 불러오는 중…</p></section>;
  if (!user) return <section className="settings-access"><Icon name="user" size={30} /><h2>계정 설정</h2><p>설정을 변경하려면 로그인하세요.</p>
    <Link className="account-button" href="/login?next=/user/settings">로그인</Link><Link href="/user/signup">계정 만들기</Link></section>;
  return <div className="settings-layout"><aside className="settings-sidebar"><nav aria-label="설정 항목">{sections.map((item) => <button key={item.id}
    type="button" aria-current={tab === item.id ? "page" : undefined} onClick={() => { if (tab === item.id) return; setTab(item.id); setMessage(""); setError(""); if (item.id === "sessions") setSessionsLoading(true); }}>
    <Icon name={item.icon} size={18} /><span>{item.label}</span></button>)}</nav>
    {user.is_global_admin && <Link className="settings-admin-link" href="/admin"><Icon name="server" size={17} />운영 대시보드</Link>}
    <div className="settings-identity"><UserAvatar src={user.avatar_url} name={user.display_name || user.username} className="size-8 shrink-0 rounded-full" /><div><strong>{user.display_name || user.username}</strong><span>@{user.username}</span></div></div>
  </aside><section className="settings-content" aria-labelledby="settings-section-title"><header><h2 id="settings-section-title">{sections.find((item) => item.id === tab)!.title}</h2></header>
    {message && <p className="account-notice mb-5 text-[var(--success)]" role="status">{message}</p>}
    {error && <p className="account-notice mb-5 text-[var(--error)]" role="alert">{error}</p>}
    <div className="space-y-5">
      {tab === "general" && <div className="settings-general"><div className="settings-row"><div><h3>화면 테마</h3><p>이 브라우저의 대화와 설정에 함께 적용됩니다.</p></div><ThemeToggle /></div>
        <div className="settings-row"><div><h3>계정 시간대</h3><p>계정에 등록된 시간대입니다.</p></div><span>{user.time_zone || "UTC"}</span></div>
        <div className="settings-row"><div><h3>로그인 기기</h3><p>연결된 기기를 확인하고 로그인 세션을 해제합니다.</p></div><button type="button" className="settings-text-button" onClick={() => { setSessionsLoading(true); setTab("sessions"); }}>기기 관리</button></div></div>}
      {tab === "profile" && <><AvatarSection key={`avatar-${user.subject}`} user={user} onSuccess={success} onError={fail} />
        <ProfileSection key={`profile-${user.subject}`} user={user} onSuccess={success} onError={fail} /></>}
      {tab === "security" && <>
        <EmailVerificationSection user={user} onSuccess={setMessage} onError={fail} />
        <EmailChangeSection user={user} onSuccess={setMessage} onError={fail} />
        <OAuthConnectionSection user={user} onSuccess={success} onError={fail} />
        <PasswordSection hasPassword={user.has_password} onSuccess={async () => { await logout(); }} onError={fail} />
      </>}
      {tab === "sessions" && <>{sessionsLoading ? <p className="settings-loading" role="status">로그인 기기를 불러오는 중…</p> : <SessionSection sessions={sessions} onRevoke={async (session) => {
          try { await api.delete(`/users/me/sessions/${session.id}`);
            if (session.is_current) await logout();
            else { setSessions((old) => old.filter((s) => s.id !== session.id)); setMessage("로그인 세션을 해제했습니다."); }
          } catch (caught) { fail(caught, "세션을 해제하지 못했습니다."); }
        }} />}
        <div className="flex justify-end"><button className="text-sm underline" disabled={loggingOut} onClick={() => void logout(true)}>모든 기기에서 로그아웃</button></div>
      </>}
      {tab === "account" && <><div className="settings-row"><div><h3>이메일</h3><p>{user.email}</p></div><button className="settings-text-button" onClick={() => setTab("security")}>변경</button></div>
        <div className="settings-row"><div><h3>현재 기기에서 로그아웃</h3><p>저장한 대화는 다시 로그인하면 볼 수 있습니다.</p></div><button className="settings-outline-button" disabled={loggingOut} onClick={() => void logout()}>로그아웃</button></div>
        <DangerSection hasPassword={user.has_password} onDeactivate={async (password) => {
          try { await api.post("/users/me/deactivate", { current_password: password }); await logout(); }
          catch (caught) { fail(caught, "계정을 비활성화하지 못했습니다."); }
        }} />
      </>}
    </div>
  </section></div>;
}
