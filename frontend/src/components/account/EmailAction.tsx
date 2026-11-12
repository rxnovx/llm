"use client";
import { useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import { WorkspaceLink as Link } from "@/components/shared/WorkspaceLink";
import { AuthCard, AuthError, AuthField, AuthPageShell, AuthStatus, authInputClassName } from "@/components/auth/AuthCard";
import { useModalNavigation } from "@/components/auth/modal-navigation";
import { authApi } from "@/lib/api";
import { getAuthSessionVersion } from "@/lib/auth";
import { getApiErrorMessage } from "@/lib/api-error";
import { useAuthStore } from "@/stores/authStore";
import type { AuthMessage } from "@/types/auth";
import { notifyWorkspaceLocation, workspaceHref } from "@/lib/workspace-navigation";

export function EmailAction({ mode }: { mode: "verify" | "reset" | "recover" }) {
  const query = useSearchParams();
  const canNavigate = useModalNavigation();
  const token = query.get("token");
  const emailChange = mode === "verify" && query.get("action") === "email-change";
  const [email, setEmail] = useState(query.get("email") ?? "");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  if (emailChange) return <AuthPageShell><AuthCard title="이메일 변경 링크"
    description="로그인 이메일은 가입 후 변경할 수 없습니다."
    footer={<Link href="/login" className="underline">로그인으로 돌아가기</Link>}>
    <p className="mt-6 text-sm"><Link href="/user/verify-email" className="underline">이메일 인증</Link></p>
  </AuthCard></AuthPageShell>;
  const title = mode === "verify" ? "이메일 인증" : mode === "reset" ? "비밀번호 재설정" : "계정 복구";
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || done || !canNavigate()) return;
    if (token && mode !== "verify" && password !== confirm) { setError("비밀번호가 일치하지 않습니다."); return; }
    setBusy(true); setError(""); setMessage("");
    const sessionVersion = getAuthSessionVersion();
    try {
      let text: string;
      if (mode === "verify") {
        if (token) { await authApi.post("/email-verification/confirm", { token }); text = "이메일을 인증했습니다. 로그인해주세요."; }
        else { const response = await authApi.post<AuthMessage>("/email-verification/resend", { email }); text = response.data.message; }
      } else {
        const base = mode === "reset" ? "password-reset" : "account-recovery";
        const response = await authApi.post<AuthMessage>(`/${base}/${token ? "confirm" : "request"}`,
          token ? { token, new_password: password } : { email });
        text = response.data.message;
        if (token) await useAuthStore.getState().logout(false, sessionVersion);
      }
      if (!canNavigate()) return;
      const current = new URLSearchParams(window.location.search);
      if (current.get("token") !== token || (mode === "verify" && current.get("action") === "email-change")) return;
      setMessage(text); setDone(Boolean(token)); setPassword(""); setConfirm("");
      if (token) {
        window.history.replaceState(null, "", workspaceHref(window.location.pathname, window.location));
        notifyWorkspaceLocation();
      }
    } catch (caught) { setError(getApiErrorMessage(caught, "요청을 완료하지 못했습니다.")); }
    finally { setBusy(false); }
  };
  return <AuthPageShell><AuthCard title={title} description={token ? "확인 버튼을 눌러 계정 변경을 완료하세요." : "계정 이메일로 확인 링크를 보내드립니다."}
    footer={<Link href="/login" className="underline">로그인으로 돌아가기</Link>}>
    <form className="mt-6 space-y-4" onSubmit={submit}>
      {error && <AuthError>{error}</AuthError>}{message && <AuthStatus>{message}</AuthStatus>}
      {!token && !done && <AuthField label="이메일" htmlFor="action-email"><input id="action-email" type="email" autoComplete="email" required value={email} onChange={(e) => setEmail(e.target.value)} className={authInputClassName} /></AuthField>}
      {token && mode !== "verify" && !done && <>
        <AuthField label="새 비밀번호" htmlFor="action-password"><input id="action-password" type="password" minLength={10} maxLength={128} autoComplete="new-password" required value={password} onChange={(e) => setPassword(e.target.value)} className={authInputClassName} /></AuthField>
        <AuthField label="새 비밀번호 확인" htmlFor="action-confirm"><input id="action-confirm" type="password" minLength={10} maxLength={128} autoComplete="new-password" required value={confirm} onChange={(e) => setConfirm(e.target.value)} className={authInputClassName} /></AuthField>
        <p className="text-xs text-[var(--fg-muted)]">대문자·소문자·숫자·특수문자 중 3종류 이상 사용해주세요.</p>
      </>}
      {!done && <button className="account-button w-full" disabled={busy}>{busy ? "처리 중..." : token ? "확인" : "안내 메일 받기"}</button>}
    </form>
  </AuthCard></AuthPageShell>;
}
