"use client";
import { useState, type FormEvent } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { AuthCard, AuthError, AuthField, AuthPageShell, AuthStatus, authInputClassName } from "@/components/auth/AuthCard";
import { api, authApi } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/api-error";
import { useAuthStore } from "@/stores/authStore";
import type { AuthMessage } from "@/types/auth";

export function EmailAction({ mode }: { mode: "verify" | "reset" | "recover" }) {
  const query = useSearchParams();
  const token = query.get("token");
  const emailChange = mode === "verify" && query.get("action") === "email-change";
  const [email, setEmail] = useState(query.get("email") ?? "");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const title = emailChange ? "로그인 이메일 변경" : mode === "verify" ? "이메일 인증" : mode === "reset" ? "비밀번호 재설정" : "계정 복구";
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || done) return;
    if (token && mode !== "verify" && password !== confirm) { setError("비밀번호가 일치하지 않습니다."); return; }
    setBusy(true); setError(""); setMessage("");
    try {
      let text: string;
      if (emailChange && token) {
        const response = await api.post<AuthMessage>("/users/me/email-change/confirm", { token });
        text = response.data.message;
        await useAuthStore.getState().logout();
      } else if (mode === "verify") {
        if (token) { await authApi.post("/email-verification/confirm", { token }); text = "이메일을 인증했습니다. 로그인해주세요."; }
        else { const response = await authApi.post<AuthMessage>("/email-verification/resend", { email }); text = response.data.message; }
      } else {
        const base = mode === "reset" ? "password-reset" : "account-recovery";
        const response = await authApi.post<AuthMessage>(`/${base}/${token ? "confirm" : "request"}`,
          token ? { token, new_password: password } : { email });
        text = response.data.message;
        if (token) await useAuthStore.getState().logout();
      }
      setMessage(text); setDone(Boolean(token)); setPassword(""); setConfirm("");
      if (token) window.history.replaceState(null, "", window.location.pathname);
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
