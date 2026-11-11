"use client";
import { useState, type FormEvent } from "react";
import { WorkspaceLink as Link } from "@/components/shared/WorkspaceLink";
import { useRouter } from "next/navigation";
import { AuthCard, AuthError, AuthField, AuthPageShell, authInputClassName } from "@/components/auth/AuthCard";
import { GoogleSignInButton } from "@/components/auth/GoogleSignInButton";
import { useModalNavigation } from "@/components/auth/modal-navigation";
import { getApiErrorCode, getApiErrorMessage } from "@/lib/api-error";
import { getSafeNextPath } from "@/lib/auth-router";
import { useAuthStore } from "@/stores/authStore";
import { workspaceHref } from "@/lib/workspace-navigation";

export default function LoginPage() {
  const router = useRouter();
  const canNavigate = useModalNavigation();
  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [visible, setVisible] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [pendingGoogle, setPendingGoogle] = useState<string | null>(null);
  const complete = () => {
    if (!canNavigate()) return;
    const search = window.location.search;
    router.replace(workspaceHref(getSafeNextPath(new URLSearchParams(search).get("next")), search), { scroll: false });
  };
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !canNavigate()) return;
    setBusy(true); setError("");
    try { await useAuthStore.getState().login({ loginId: loginId.trim(), password }); complete(); }
    catch (caught) { setError(getApiErrorMessage(caught, "로그인에 실패했습니다.")); }
    finally { setBusy(false); }
  };
  const google = async (credential: string, acceptTerms = false) => {
    if (busy || !canNavigate()) return;
    setBusy(true); setError("");
    try { await useAuthStore.getState().loginWithGoogle(credential, acceptTerms); complete(); }
    catch (caught) {
      if (getApiErrorCode(caught) === "google_terms_required") setPendingGoogle(credential);
      else setError(getApiErrorMessage(caught, "Google 로그인에 실패했습니다."));
    } finally { setBusy(false); }
  };
  return <AuthPageShell><AuthCard title="다시 만나서 반가워요" description="사용자명 또는 이메일로 LLM에 로그인하세요."
    footer={<>계정이 없다면 <Link className="font-semibold underline" href="/user/signup">회원가입</Link></>}>
    <div className="my-5"><GoogleSignInButton onCredential={google} onError={(e) => setError(getApiErrorMessage(e, "Google 로그인을 준비하지 못했습니다."))} /></div>
    {pendingGoogle && <div className="mb-5 space-y-3 rounded-lg bg-[var(--surface-soft)] p-4">
      <p className="text-sm">이 Google 계정으로 새 LLM 계정을 생성합니다.</p>
      <button className="account-button" disabled={busy} onClick={() => void google(pendingGoogle, true)}>동의하고 계정 생성</button>
      <button className="px-3 text-sm" onClick={() => setPendingGoogle(null)}>취소</button>
    </div>}
    <form onSubmit={submit} className="space-y-4">
      {error && <AuthError>{error}</AuthError>}
      <AuthField label="사용자명 또는 이메일" htmlFor="login-id"><input id="login-id" autoComplete="username" required maxLength={320}
        value={loginId} onChange={(e) => setLoginId(e.target.value)} className={authInputClassName} /></AuthField>
      <AuthField label="비밀번호" htmlFor="login-password"><input id="login-password" type={visible ? "text" : "password"} autoComplete="current-password" required maxLength={128}
        value={password} onChange={(e) => setPassword(e.target.value)} className={authInputClassName} /></AuthField>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={visible} onChange={(e) => setVisible(e.target.checked)} />비밀번호 표시</label>
      <button disabled={busy} className="account-button w-full">{busy ? "로그인 중..." : "로그인"}</button>
    </form>
    <div className="mt-5 flex flex-wrap justify-center gap-4 text-sm text-[var(--fg-muted)]">
      <Link href="/user/reset-password" className="underline">비밀번호 찾기</Link>
      <Link href="/user/verify-email" className="underline">이메일 인증</Link>
      <Link href="/user/recover" className="underline">계정 복구</Link>
    </div>
  </AuthCard></AuthPageShell>;
}
