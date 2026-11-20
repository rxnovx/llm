"use client";

import { useEffect, useState } from "react";
import { appsJson, watchAppsChanged, type AppCatalog } from "@/lib/app-connections";
import { WorkspaceLink } from "@/components/shared/WorkspaceLink";
import type { WebhookEvent, WebhookSubscription } from "@/components/account/GitlabWebhookSettings";

/** 사용자가 구독에서 켠 상태 알림만 확인하며 이벤트로 도구를 실행하지 않습니다. */
export function GitlabNotifications() {
  const [count, setCount] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    let subscriptions: WebhookSubscription[] = [], pending = false, configuring = false, configureAgain = false;
    const poll = async () => {
      if (pending || document.visibilityState !== "visible") return;
      if (!subscriptions.length) { setCount(0); return; }
      pending = true;
      try {
        const results = await Promise.allSettled(subscriptions.map(item => appsJson<WebhookSubscription & { events: WebhookEvent[] }>(
          `/gitlab/webhook-subscriptions/${item.id}`, { signal: AbortSignal.any([abort.signal, AbortSignal.timeout(35000)]) })));
        if (!abort.signal.aborted) setCount(results.reduce((total, result) => total + (result.status === "fulfilled"
          ? result.value.events.filter(event => event.notify).length : 0), 0));
      } finally { pending = false; }
    };
    const configure = async () => {
      if (document.visibilityState !== "visible") return;
      if (configuring) { configureAgain = true; return; }
      configuring = true;
      try {
        const catalog = await appsJson<AppCatalog>("/catalog", { signal: AbortSignal.any([abort.signal, AbortSignal.timeout(15000)]) });
        const connections = catalog.apps.flatMap(app => app.connections.filter(item => item.status === "connected")).slice(0, 8);
        const lists = await Promise.allSettled(connections.map(item => appsJson<{ items: WebhookSubscription[] }>(
          `/connections/${item.id}/webhooks`, { signal: AbortSignal.any([abort.signal, AbortSignal.timeout(15000)]) })));
        if (abort.signal.aborted) return;
        subscriptions = lists.flatMap(result => result.status === "fulfilled" ? result.value.items.filter(item => item.notifications_enabled) : []).slice(0, 8);
        await poll();
      } catch { if (!abort.signal.aborted) { subscriptions = []; setCount(0); } }
      finally {
        configuring = false;
        if (configureAgain && !abort.signal.aborted) { configureAgain = false; void configure(); }
      }
    };
    void configure();
    const unwatch = watchAppsChanged(() => void configure());
    const timer = setInterval(() => void poll(), 30000);
    return () => { abort.abort(); unwatch(); clearInterval(timer); };
  }, []);
  if (!count) return null;
  return <WorkspaceLink href="/user/settings/apps" className="cw-gitlab-updates" aria-label={`GitLab 상태 업데이트 ${count}개`}>
    <span aria-hidden="true" />GitLab · {count}<span className="cw-sr-only">개의 새 상태 알림</span>
  </WorkspaceLink>;
}
