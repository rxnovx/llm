"use client";
import { useEffect, useState } from "react";
import { appsJson, connectGitlab, notifyAppsChanged, watchAppsChanged, type AppCatalog } from "@/lib/app-connections";
import { GitlabWebhookSettings } from "./GitlabWebhookSettings";

export function AppConnections() {
  const [catalog, setCatalog] = useState<AppCatalog | null>(null);
  const [error, setError] = useState(""), [notice, setNotice] = useState(""), [busy, setBusy] = useState("");
  const [webhookAccount, setWebhookAccount] = useState("");
  useEffect(() => {
    const abort = new AbortController();
    const load = () => { void appsJson<AppCatalog>("/catalog", { signal: abort.signal }).then(value => {
      if (!abort.signal.aborted) setCatalog(value);
    }).catch((e: unknown) => { if (!abort.signal.aborted) setError(e instanceof Error ? e.message : "앱 연결을 불러오지 못했습니다."); }); };
    load(); const unwatch = watchAppsChanged(load);
    return () => { abort.abort(); unwatch(); };
  }, []);
  const perform = async (key: string, action: () => Promise<void>) => {
    setBusy(key); setError(""); setNotice("");
    try { await action(); }
    catch (e) { setError(e instanceof Error ? e.message : "앱 연결을 변경하지 못했습니다."); }
    finally { setBusy(""); }
  };
  const disconnect = (id: string) => perform(id, async () => {
    const result = await appsJson<{ remote_revoked: boolean }>(`/connections/${id}`, { method: "DELETE" });
    setNotice(result.remote_revoked ? "GitLab 연결과 원격 인증을 해제했습니다." : "앱에서 연결을 해제했습니다. GitLab에 접속할 수 없어 원격 철회는 확인하지 못했습니다. GitLab의 승인된 애플리케이션에서도 해제해 주세요.");
    if (webhookAccount === id) setWebhookAccount("");
    notifyAppsChanged();
  });
  return <div className="app-connections"><p className="settings-app-intro">GitLab 계정을 연결하면 채팅에서 <strong>@gitlab</strong> 또는 자연어로 프로젝트를 찾고 분석할 수 있습니다. 조회한 자료는 현재 대화의 모델 서버로 전달됩니다.</p>
    {error && <p className="account-notice text-[var(--error)]" role="alert">{error}</p>}
    {notice && <p className="account-notice" role="status">{notice}</p>}
    {!catalog && !error && <p role="status">앱 연결을 불러오는 중…</p>}
    {catalog?.apps.map(app => <section key={app.id}><h3 className="settings-app-heading">GitLab</h3>
      {!app.providers.length && <p className="settings-app-empty">등록된 GitLab 서버가 없습니다. 운영자가 GitLab OAuth 앱과 서버 환경 변수를 등록하면 여기에 표시됩니다.</p>}
      {app.providers.map(provider => {
        const accounts = app.connections.filter(item => item.provider_id === provider.id);
        return <section className="settings-gitlab-provider" key={provider.id} aria-label={`${provider.name} 연결 계정`}>
          <div className="settings-app-provider"><strong>{provider.name}</strong><small>{provider.base_url}</small>
            {!provider.ready && <p>{provider.reason}</p>}</div>
          {accounts.map(connection => <div key={connection.id} className="settings-gitlab-account">
            <div className="settings-app-card"><div><strong>@{connection.username}</strong><small>{connection.display_name}</small>
              <p>{connection.status === "connected" ? connection.can_publish ? "원격 쓰기 권한 연결됨" : "읽기 연결됨" : "다시 인증 필요"}</p></div>
              <div className="settings-app-actions">
                {connection.status !== "connected" && <button type="button" className="settings-outline-button" disabled={!!busy || !provider.ready}
                  onClick={() => void perform(connection.id, () => connectGitlab(provider.id))}>{busy === connection.id ? "연결 중…" : "다시 연결"}</button>}
                {provider.publish_enabled && !connection.can_publish && <button type="button" className="settings-outline-button" disabled={!!busy || !provider.ready}
                  onClick={() => void perform(connection.id, () => connectGitlab(provider.id, true))}>원격 쓰기 권한 연결</button>}
                {connection.status === "connected" && <button type="button" className="settings-outline-button" disabled={!!busy}
                  onClick={() => setWebhookAccount(current => current === connection.id ? "" : connection.id)}>{webhookAccount === connection.id ? "상태 구독 닫기" : "MR·파이프라인 상태"}</button>}
                <button type="button" className="settings-text-button" disabled={!!busy} onClick={() => void disconnect(connection.id)}>연결 해제</button>
              </div></div>
            {webhookAccount === connection.id && connection.status === "connected" && <GitlabWebhookSettings connectionId={connection.id} />}
          </div>)}
          <div className="settings-app-actions settings-gitlab-add"><button type="button" className="settings-outline-button" disabled={!!busy || !provider.ready}
            onClick={() => void perform(provider.id, () => connectGitlab(provider.id))}>{busy === provider.id ? "연결 중…" : accounts.length ? "다른 계정 연결" : "계정 연결"}</button>
            {provider.publish_enabled && !accounts.length && <button type="button" className="settings-outline-button" disabled={!!busy || !provider.ready}
              onClick={() => void perform(provider.id, () => connectGitlab(provider.id, true))}>원격 쓰기 권한으로 연결</button>}</div>
          {!!accounts.length && <p className="settings-app-footnote">다른 계정을 추가하려면 GitLab 인증 창에서 해당 계정으로 로그인하세요.</p>}
        </section>;
      })}
      {app.connections.filter(item => !app.providers.some(provider => provider.id === item.provider_id)).map(connection => <div key={connection.id} className="settings-app-card"><div><strong>@{connection.username}</strong><p>서버 등록이 제거된 연결입니다.</p></div>
        <button type="button" className="settings-text-button" disabled={!!busy} onClick={() => void perform(connection.id, async () => {
          await appsJson(`/connections/${connection.id}`, { method: "DELETE" }); setNotice("앱에서 연결을 제거했습니다. 원격 인증은 GitLab에서도 해제해 주세요."); notifyAppsChanged();
        })}>연결 제거</button></div>)}
    </section>)}
    <p className="settings-app-footnote">기본 연결은 읽기 권한을 사용합니다.{catalog?.apps.some(app => app.providers.some(provider => provider.publish_enabled))
      && <> 원격 쓰기는 GitLab의 <code>api</code> 권한과 현재 질문에서 허용한 범위 안에서 실행합니다.</>}</p>
  </div>;
}
