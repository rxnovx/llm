"use client";
import { useEffect, useMemo, useState, type FormEvent } from "react";
import { WorkspaceLink as Link } from "@/components/shared/WorkspaceLink";
import { useRouter } from "next/navigation";
import { AuthCard, AuthError, AuthField, AuthPageShell, authInputClassName } from "@/components/auth/AuthCard";
import { useModalNavigation } from "@/components/auth/modal-navigation";
import { authApi } from "@/lib/api";
import { getApiErrorMessage } from "@/lib/api-error";
import { useAuthStore } from "@/stores/authStore";
import type { AuthConfig, AuthUser } from "@/types/auth";
import { workspaceHref } from "@/lib/workspace-navigation";
import { getSafeNextPath } from "@/lib/auth-router";

export default function SignupPage() {
  const router = useRouter();
  const canNavigate = useModalNavigation();
  const [config, setConfig] = useState<AuthConfig | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [values, setValues] = useState({ displayName: "", username: "", email: "", password: "", confirm: "", consent: false });
  useEffect(() => {
    let cancelled = false;
    void authApi.get<AuthConfig>("/config").then(({ data }) => { if (!cancelled) setConfig(data); })
      .catch((e) => { if (!cancelled) setError(getApiErrorMessage(e, "회원가입 설정을 불러오지 못했습니다.")); });
    return () => { cancelled = true; };
  }, []);
  const groups = useMemo(() => [/[a-z]/, /[A-Z]/, /\d/, /[^A-Za-z0-9]/].filter((r) => r.test(values.password)).length, [values.password]);
  const length = config?.password_min_length ?? 10;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (busy || !canNavigate() || !config?.public_registration_enabled) return;
    if (values.password !== values.confirm) { setError("비밀번호가 일치하지 않습니다."); return; }
    if (groups < 3 || values.password.length < length) { setError("비밀번호 보안 조건을 확인해주세요."); return; }
    setBusy(true); setError("");
    try {
      const { data } = await authApi.post<AuthUser>("/register", {
        username: values.username.trim().toLowerCase(), email: values.email.trim().toLowerCase(),
        password: values.password, display_name: values.displayName.trim() || null,
        accept_terms: values.consent, locale: "ko", time_zone: Intl.DateTimeFormat().resolvedOptions().timeZone,
      });
      if (!canNavigate()) return;
      if (data.email_verification_required) { router.replace(workspaceHref(`/user/verify-email?email=${encodeURIComponent(values.email)}`, window.location.search), { scroll: false }); return; }
      await useAuthStore.getState().login({ loginId: values.username, password: values.password });
      if (!canNavigate()) return;
      router.replace(workspaceHref(getSafeNextPath(new URLSearchParams(window.location.search).get("next")), window.location.search), { scroll: false });
    } catch (caught) { setError(getApiErrorMessage(caught, "회원가입을 완료하지 못했습니다.")); }
    finally { setBusy(false); }
  };
  const update = (name: keyof typeof values, value: string | boolean) => setValues((previous) => ({ ...previous, [name]: value }));
  return <AuthPageShell><AuthCard wide title="계정 만들기" description="LLM에서 사용할 계정을 만드세요."
    footer={<>이미 계정이 있다면 <Link href="/login" className="font-semibold underline">로그인</Link></>}>
    <form onSubmit={submit} className="mt-6 space-y-4">
      {error && <AuthError>{error}</AuthError>}
      {config && !config.public_registration_enabled && <AuthError>현재 공개 회원가입이 비활성화되어 있습니다.</AuthError>}
      <AuthField label="표시 이름" htmlFor="signup-name"><input id="signup-name" autoComplete="nickname" maxLength={100} value={values.displayName} onChange={(e) => update("displayName", e.target.value)} className={authInputClassName} /></AuthField>
      <AuthField label="사용자명" htmlFor="signup-username"><input id="signup-username" autoComplete="username" required minLength={3} maxLength={50} pattern="[a-zA-Z0-9_.-]+" value={values.username} onChange={(e) => update("username", e.target.value)} className={authInputClassName} /></AuthField>
      <p className="text-xs text-[var(--fg-subtle)]">영문·숫자·밑줄·점·하이픈 3~50자. 가입 후 변경할 수 없습니다.</p>
      <AuthField label="이메일" htmlFor="signup-email"><input id="signup-email" type="email" autoComplete="email" required maxLength={320} value={values.email} onChange={(e) => update("email", e.target.value)} className={authInputClassName} /></AuthField>
      <AuthField label="비밀번호" htmlFor="signup-password"><input id="signup-password" type="password" autoComplete="new-password" required minLength={length} maxLength={128} value={values.password} onChange={(e) => update("password", e.target.value)} className={authInputClassName} /></AuthField>
      <p className="text-xs text-[var(--fg-subtle)]">{length}자 이상 · 대문자·소문자·숫자·특수문자 중 3종류 이상 {values.password && `(${groups}/4)`}</p>
      <AuthField label="비밀번호 확인" htmlFor="signup-confirm"><input id="signup-confirm" type="password" autoComplete="new-password" required minLength={length} maxLength={128} value={values.confirm} onChange={(e) => update("confirm", e.target.value)} className={authInputClassName} /></AuthField>
      <label className="flex items-center gap-2 text-sm"><input type="checkbox" checked={values.consent} onChange={(e) => update("consent", e.target.checked)} required />입력한 정보로 LLM 계정을 생성하는 데 동의합니다.</label>
      <button disabled={busy || !config?.public_registration_enabled} className="account-button w-full">{busy ? "계정 생성 중..." : "회원가입"}</button>
    </form>
  </AuthCard></AuthPageShell>;
}
