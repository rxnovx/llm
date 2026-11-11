"use client";
import { useEffect, useRef, useState } from "react";
import { WorkspaceLink } from "@/components/shared/WorkspaceLink";
import { useModalNavigation } from "@/components/auth/modal-navigation";
import { appsJson, notifyAppsChanged, oauthReturnPath } from "@/lib/app-connections";
import { getSafeNextPath } from "@/lib/workspace-navigation";
import { useAuthStore } from "@/stores/authStore";

export default function GitlabCallbackPage() {
  const canNavigate = useModalNavigation();
  const initialized = useAuthStore((s) => s.initialized), user = useAuthStore((s) => s.user);
  const values = useRef<{ state: string; code: string; error: string } | null>(null), started = useRef(false);
  const [result, setResult] = useState<{ username: string; path: string } | null>(null), [error, setError] = useState("");
  useEffect(() => {
    if (!values.current) {
      const query = new URLSearchParams(window.location.search);
      values.current = { state: query.get("state") ?? "", code: query.get("code") ?? "", error: query.get("error") ? "access_denied" : "" };
      window.history.replaceState(null, "", window.location.pathname); // Never retain OAuth codes in browser history.
    }
    if (!initialized || started.current) return;
    started.current = true;
    if (!user) return;
    void appsJson<{ connection: { username: string }; return_path: string; popup: boolean }>("/gitlab/oauth/complete", {
      method: "POST", body: JSON.stringify(values.current),
    }).then((data) => {
      notifyAppsChanged();
      if (!canNavigate()) return;
      setResult({ username: data.connection.username, path: data.return_path });
      if (data.popup) window.close();
      else window.location.replace(getSafeNextPath(data.return_path));
    }).catch((e: unknown) => setError(e instanceof Error ? e.message : "GitLab 인증을 완료하지 못했습니다."));
  }, [canNavigate, initialized, user]);
  const failure = error || (initialized && !user ? "인증을 시작한 앱 계정의 로그인이 필요합니다. 다시 로그인한 뒤 설정에서 GitLab 연결을 시작하세요." : "");
  return <section className="settings-access"><h2>GitLab 연결</h2>{result ? <><p role="status">@{result.username} 계정을 연결했습니다. 이 창을 닫고 채팅으로 돌아가세요.</p>
    <WorkspaceLink className="account-button" href={result.path}>앱 연결로 돌아가기</WorkspaceLink></> : failure ? <><p role="alert">{failure}</p>
      <a className="account-button" href="/user/settings/apps" onClick={(event) => { event.preventDefault(); window.location.replace(oauthReturnPath(user?.subject)); }}>앱 연결로 돌아가기</a></> : <p role="status">GitLab 인증을 확인하고 있습니다…</p>}</section>;
}
