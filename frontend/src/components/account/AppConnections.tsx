"use client";
import { useEffect, useState } from "react";
import { appsJson, connectGitlab, notifyAppsChanged, watchAppsChanged, type AppCatalog } from "@/lib/app-connections";

export function AppConnections() {
  const [catalog, setCatalog] = useState<AppCatalog | null>(null);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    const load = () => { void appsJson<AppCatalog>("/catalog", { signal: abort.signal }).then(setCatalog)
      .catch((e: unknown) => { if (!abort.signal.aborted) setError(e instanceof Error ? e.message : "앱 연결을 불러오지 못했습니다."); }); };
    load(); const unwatch = watchAppsChanged(load);
    return () => { abort.abort(); unwatch(); };
  }, []);
  const perform = async (key: string, action: () => Promise<void>) => {
    setBusy(key); setError(""); setNotice("");
    try { await action(); }
    catch (e) { setError(e instanceof Error ? e.message : "앱 연결을 변경하지 못했습니다."); }
    finally { setBusy(""); }
  };
  return <div className="app-connections"><p className="settings-app-intro">내 GitLab 계정을 연결한 뒤 채팅에서 <strong>@GitLab</strong>으로 파일을 선택하세요. 선택한 자료가 현재 대화의 모델 서버로 전달됩니다.</p>
    {error && <p className="account-notice text-[var(--error)]" role="alert">{error}</p>}
    {notice && <p className="account-notice" role="status">{notice}</p>}
    {!catalog && !error && <p role="status">앱 연결을 불러오는 중…</p>}
    {catalog?.apps.map((app) => <section key={app.id}><h3 className="settings-app-heading">GitLab</h3>
      {!app.providers.length && <p className="settings-app-empty">등록된 GitLab 서버가 없습니다. 운영자가 GitLab OAuth 앱과 서버 환경 변수를 등록하면 여기에 표시됩니다.</p>}
      {app.providers.map((provider) => {
        const connection = app.connections.find((item) => item.provider_id === provider.id);
        return <div className="settings-app-card" key={provider.id}><div><strong>{provider.name}</strong><small>{provider.base_url}</small>
          {connection && <p>@{connection.username} · {connection.status === "connected" ? connection.can_publish ? "MR 작성 권한 연결됨" : "읽기 연결됨" : "다시 인증 필요"}</p>}
          {!provider.ready && <p>{provider.reason}</p>}</div><div className="settings-app-actions">
          {(!connection || connection.status !== "connected") && <button className="settings-outline-button" disabled={!!busy || !provider.ready}
            onClick={() => void perform(provider.id, () => connectGitlab(provider.id))}>{busy === provider.id ? "연결 중…" : connection ? "다시 연결" : "연결"}</button>}
          {provider.publish_enabled && !connection?.can_publish && <button className="settings-outline-button" disabled={!!busy || !provider.ready}
            onClick={() => void perform(provider.id, () => connectGitlab(provider.id, true))}>MR 작성 권한 연결</button>}
          {connection && <button className="settings-text-button" disabled={!!busy} onClick={() => void perform(connection.id, async () => {
            const result = await appsJson<{ remote_revoked: boolean }>(`/connections/${connection.id}`, { method: "DELETE" });
            setNotice(result.remote_revoked ? "GitLab 연결과 원격 인증을 해제했습니다." : "앱에서 연결을 해제했습니다. GitLab에 접속할 수 없어 원격 철회는 확인하지 못했습니다. GitLab의 승인된 애플리케이션에서도 해제할 수 있습니다.");
            notifyAppsChanged();
          })}>연결 해제</button>}</div></div>;
      })}
      {app.connections.filter((item) => !app.providers.some((p) => p.id === item.provider_id)).map((connection) => <div key={connection.id} className="settings-app-card"><div><strong>@{connection.username}</strong><p>서버 등록이 제거된 연결입니다.</p></div>
        <button className="settings-text-button" disabled={!!busy} onClick={() => void perform(connection.id, async () => {
          await appsJson(`/connections/${connection.id}`, { method: "DELETE" }); setNotice("앱에서 연결을 제거했습니다. 원격 인증은 GitLab에서도 해제해 주세요."); notifyAppsChanged();
        })}>연결 제거</button></div>)}
    </section>)}
    <p className="settings-app-footnote">기본 연결은 읽기 권한을 사용합니다.{catalog?.apps.some(app =>
      app.providers.some(provider => provider.publish_enabled)) && <> MR 작성 권한 연결은 GitLab의 <code>api</code> 권한을 요청합니다.</>}</p>
  </div>;
}
