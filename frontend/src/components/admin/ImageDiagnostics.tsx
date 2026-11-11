"use client";
import { useEffect, useRef, useState } from "react";
import { ApiRequestError, authenticatedFetch } from "@/lib/authenticated-fetch";
import type { ImageProbeMode, ImageProbeResult, ImageServers } from "@/types/admin";

const modes = ["generate", "edit"] as const;
const labels = { generate: "이미지 생성", edit: "이미지 편집" };
const envNames = { generate: "IMAGE_GENERATION_URL", edit: "IMAGE_EDIT_URL" };
type Result = { error: boolean; text: string };
type Probe = { id: string; controller: AbortController; cancelling: boolean };

function probeId() {
  const bytes = crypto.getRandomValues(new Uint8Array(16));
  bytes[6] = (bytes[6] & 15) | 64;
  bytes[8] = (bytes[8] & 63) | 128;
  const hex = Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join("");
  return [hex.slice(0, 8), hex.slice(8, 12), hex.slice(12, 16), hex.slice(16, 20), hex.slice(20)].join("-");
}

function cancelProbe(id: string) {
  return authenticatedFetch(`/api/admin/images/probe/${id}/cancel`, {
    method: "POST", body: "{}", keepalive: true, signal: AbortSignal.timeout(8_000),
  });
}

export function ImageDiagnostics({ onForbidden }: { onForbidden: () => void }) {
  const [servers, setServers] = useState<ImageServers | null>(null);
  const [loadError, setLoadError] = useState("");
  const [probing, setProbing] = useState<ImageProbeMode | null>(null);
  const [cancelling, setCancelling] = useState(false);
  const [results, setResults] = useState<Partial<Record<ImageProbeMode, Result>>>({});
  const [refresh, setRefresh] = useState(0);
  const activeProbe = useRef<Probe | null>(null);

  useEffect(() => {
    const abort = new AbortController();
    let pending = false;
    const load = async () => {
      if (pending) return;
      pending = true;
      try {
        const response = await authenticatedFetch("/api/admin/images", {
          signal: AbortSignal.any([abort.signal, AbortSignal.timeout(12_000)]),
        });
        const data: ImageServers = await response.json();
        if (!abort.signal.aborted) {
          setServers(data);
          setLoadError("");
        }
      } catch (caught) {
        if (abort.signal.aborted) return;
        if (caught instanceof ApiRequestError && caught.status === 403) onForbidden();
        else setLoadError("이미지 서버 설정과 대기열을 불러오지 못했습니다.");
      } finally {
        pending = false;
      }
    };
    void load();
    const timer = setInterval(() => {
      if (document.visibilityState === "visible") void load();
    }, 15_000);
    return () => {
      clearInterval(timer);
      abort.abort();
    };
  }, [refresh, onForbidden]);

  useEffect(() => () => {
    const probe = activeProbe.current;
    if (!probe) return;
    activeProbe.current = null;
    probe.controller.abort();
    void cancelProbe(probe.id).catch(() => {});
  }, []);

  const probe = async (mode: ImageProbeMode) => {
    if (!servers?.[mode].configured || activeProbe.current) return;
    const abort = new AbortController();
    const attempt: Probe = { id: probeId(), controller: abort, cancelling: false };
    activeProbe.current = attempt;
    setProbing(mode);
    setResults((old) => ({ ...old, [mode]: undefined }));
    const timeout = (servers.limits.queue_wait_seconds + servers.limits.timeout_seconds + 20) * 1_000;
    try {
      const response = await authenticatedFetch("/api/admin/images/probe", {
        method: "POST",
        body: JSON.stringify({ mode, request_id: attempt.id }),
        signal: AbortSignal.any([abort.signal, AbortSignal.timeout(timeout)]),
      });
      const result: ImageProbeResult = await response.json();
      if (!abort.signal.aborted) setResults((old) => ({ ...old, [mode]: {
        error: false, text: `응답 확인 · ${result.format.toUpperCase()} · ${Math.ceil(result.size / 1024)} KiB · ${(result.elapsed_ms / 1000).toFixed(1)}초`,
      } }));
    } catch (caught) {
      if (abort.signal.aborted) return;
      if (caught instanceof ApiRequestError && caught.status === 403) onForbidden();
      else if (caught instanceof ApiRequestError && caught.status === 409 && attempt.cancelling)
        setResults((old) => ({ ...old, [mode]: { error: false, text: "진단을 취소했습니다." } }));
      else setResults((old) => ({ ...old, [mode]: {
        error: true,
        text: caught instanceof ApiRequestError ? caught.message : "이미지 서버 진단을 완료하지 못했습니다. 다시 시도해 주세요.",
      } }));
    } finally {
      if (activeProbe.current === attempt) {
        activeProbe.current = null;
        setProbing(null);
        setCancelling(false);
        setRefresh((value) => value + 1);
      }
    }
  };

  const cancel = async (mode: ImageProbeMode) => {
    const attempt = activeProbe.current;
    if (!attempt || attempt.cancelling) return;
    attempt.cancelling = true;
    setCancelling(true);
    setResults((old) => ({ ...old, [mode]: undefined }));
    try {
      await cancelProbe(attempt.id);
    } catch (caught) {
      if (activeProbe.current !== attempt) return;
      attempt.cancelling = false;
      setCancelling(false);
      if (caught instanceof ApiRequestError && caught.status === 403) onForbidden();
      else setResults((old) => ({ ...old, [mode]: {
        error: true, text: caught instanceof ApiRequestError ? caught.message : "진단 취소를 요청하지 못했습니다.",
      } }));
    }
  };

  return <section className="ops-panel">
    <header>
      <div><h2>이미지 서버 진단</h2>
        <p>{servers
          ? `대기 ${servers.queue.queued} / ${servers.limits.queue_capacity}건 · 실행 ${servers.queue.running} / ${servers.limits.max_concurrent}건 · 사용자당 동시 실행 ${servers.limits.user_concurrency}건`
          : "환경 설정에 등록된 이미지 API를 확인합니다."}</p>
      </div>
      <button type="button" className="ops-button" onClick={() => setRefresh((value) => value + 1)}>이미지 서버 상태 새로고침</button>
    </header>
    {loadError && <p className="ops-error" role="alert">{loadError}</p>}
    {!servers && !loadError && <p className="ops-empty" role="status">이미지 서버 상태를 불러오는 중…</p>}
    {servers && <div className="ops-connection-list">{modes.map((mode) => {
      const server = servers[mode];
      const result = results[mode];
      return <div key={mode}>
        <div><strong>{labels[mode]}</strong>
          <span className="ops-status">{server.configured ? "설정됨" : "미설정"}</span>
          <small>{server.configured ? server.model || "서버 기본 모델" : `${envNames[mode]} 설정 필요`}</small>
          {result && <p className={result.error ? "ops-check-error" : undefined} role={result.error ? "alert" : "status"}>{result.text}</p>}
        </div>
        <div className="ops-actions">
          <button type="button" className="ops-button" disabled={!server.configured || probing !== null} onClick={() => void probe(mode)}>
            {probing === mode ? "진단 중…" : `${labels[mode]} 시험`}
          </button>
          {probing === mode && <button type="button" className="ops-button" disabled={cancelling} onClick={() => void cancel(mode)}>
            {cancelling ? "취소 중…" : "진단 취소"}
          </button>}
        </div>
      </div>;
    })}</div>}
  </section>;
}
