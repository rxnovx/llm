"use client";
import { useEffect, useState, type FormEvent } from "react";
import { authenticatedFetch, ApiRequestError } from "@/lib/authenticated-fetch";
import type { ModelProfile } from "@/types/chat";

type Draft = { id: string; name: string; provider: "vllm" | "ollama"; base_url: string; model: string; enabled: boolean;
  allowed_users: string[]; models: ModelProfile[]; context_window: number; max_output_tokens: number; max_concurrent: number;
  auth_mode: "inherit" | "none" | "reference"; key_ref: string; revision: number; supports_tools:boolean };
type Managed = Draft & { source: "database" | "environment"; has_key: boolean; has_environment: boolean };
const empty = (): Draft => ({ id: "", name: "", provider: "vllm", base_url: "", model: "", enabled: true,
  allowed_users: [], models: [], context_window: 8192, max_output_tokens: 2048, max_concurrent: 2, auth_mode: "none", key_ref: "", revision: 0, supports_tools:false });
const draftOf = (item: Managed): Draft => Object.fromEntries(Object.keys(empty()).map((key) => [key, item[key as keyof Draft]])) as Draft;

export function ConnectionManager({ onForbidden }: { onForbidden: () => void }) {
  const [items, setItems] = useState<Managed[]>([]);
  const [keys, setKeys] = useState<string[]>([]);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [users, setUsers] = useState("");
  const [creating, setCreating] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [probing, setProbing] = useState('');
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const abort = new AbortController();
    void authenticatedFetch("/api/admin/connections", { signal: AbortSignal.any([abort.signal, AbortSignal.timeout(12000)]) })
      .then((response) => response.json()).then((data: { items: Managed[]; key_refs: string[] }) => {
        if (!abort.signal.aborted) { setItems(data.items); setKeys(data.key_refs); setError(""); }
      }).catch((caught) => {
        if (abort.signal.aborted) return;
        if (caught instanceof ApiRequestError && caught.status === 403) { setItems([]); setDraft(null); onForbidden(); }
        else setError(caught instanceof Error ? caught.message : "연결 목록을 불러오지 못했습니다.");
      }).finally(() => { if (!abort.signal.aborted) setLoading(false); });
    return () => abort.abort();
  }, [refresh, onForbidden]);
  const edit = (item?: Managed) => { setDraft(item ? draftOf(item) : empty()); setUsers(item?.allowed_users.join(", ") ?? ""); setCreating(!item); setError(""); setNotice(""); };
  const save = async (event: FormEvent) => {
    event.preventDefault(); if (!draft || saving) return;
    setSaving(true); setError(""); setNotice("");
    try {
      await authenticatedFetch(`/api/admin/connections${creating ? "" : `/${draft.id}`}`, {
        method: creating ? "POST" : "PATCH", signal: AbortSignal.timeout(15000),
        body: JSON.stringify({ ...draft, allowed_users: users.split(/[,\n]/).map((value) => value.trim()).filter(Boolean) }),
      });
      setDraft(null); setNotice("연결 설정을 저장했습니다. 다음 생성 요청부터 적용됩니다."); setLoading(true); setRefresh((value) => value + 1);
    } catch (caught) {
      if (caught instanceof ApiRequestError && caught.status === 403) { setItems([]); setDraft(null); onForbidden(); }
      else setError(caught instanceof Error ? caught.message : "연결을 저장하지 못했습니다.");
    } finally { setSaving(false); }
  };
  const probe = async (item: Managed) => {
    setProbing(item.id);
    setError('');
    setNotice('');
    try {
      const response = await authenticatedFetch(`/api/admin/connections/${item.id}/probe`, {
        method: 'POST', body: '{}', signal: AbortSignal.timeout(60000),
      });
      const result: { model: string; sample: string; elapsed_ms: number } = await response.json();
      setNotice(`${item.name} · ${result.model} · ${result.elapsed_ms}ms · ${result.sample}`);
    } catch (caught) {
      if (caught instanceof ApiRequestError && caught.status === 403) onForbidden();
      else setError(caught instanceof Error ? caught.message : '테스트 응답을 확인하지 못했습니다.');
    } finally {
      setProbing('');
    }
  };
  const profile = (index: number, patch: Partial<ModelProfile>) => setDraft((value) => value && ({ ...value,
    models: value.models.map((item, at) => at === index ? { ...item, ...patch } : item) }));
  return <section className="ops-panel"><header><div><h2>모델 연결 관리</h2><p>저장한 설정은 재시작 없이 적용됩니다. 이미 생성 중인 답변에는 기존 설정을 사용합니다.</p></div>
    <div className="ops-actions"><button className="ops-button" disabled={loading || saving} onClick={() => { setLoading(true); setRefresh((value) => value + 1); }}>목록 새로고침</button>
      <button className="ops-button" disabled={saving} onClick={() => edit()}>연결 등록</button></div></header>
    {error && <p className="ops-error" role="alert">{error}</p>}{notice && <p className="ops-notice" role="status">{notice}</p>}
    {loading && <p className="ops-empty" role="status">연결 목록을 불러오는 중…</p>}
    <div className="ops-connection-list">{items.map((item) => <div key={item.id}><div><strong>{item.name}</strong><span className={`ops-status ${item.enabled ? "status-complete" : "status-cancelled"}`}>{item.enabled ? "사용" : "비활성"}</span>
      <small>{item.provider} · {item.id} · {item.source === "environment" ? "환경 설정" : "운영 화면 설정"}</small>
      <p>{item.model || "기본 모델 미지정"} · 동시 생성 {item.max_concurrent}개 · {item.allowed_users.length ? `${item.allowed_users.length}명 허용` : "전체 사용자 허용"}</p>
      {item.auth_mode === "reference" && !item.has_key && <p className="ops-check-error">서버 인증키 설정이 필요합니다.</p>}</div>
      <div className="ops-actions">
        <button className="ops-button" disabled={!item.enabled || saving || !!probing} onClick={() => void probe(item)}>
          {probing === item.id ? '응답 확인 중…' : '테스트 응답'}</button>
        <button className="ops-button" disabled={saving} onClick={() => edit(item)}>설정 수정</button>
      </div></div>)}</div>
    {!loading && !items.length && <p className="ops-empty">등록된 모델 연결이 없습니다. 첫 연결을 등록하세요.</p>}
    {draft && <form className="ops-connection-form" onSubmit={(event) => void save(event)}><h3>{creating ? "새 연결 등록" : `${draft.name} 설정`}</h3>
      <fieldset disabled={saving}><div className="ops-form-grid">
        <label>연결 ID<input required pattern="[a-zA-Z0-9_-]{1,64}" maxLength={64} disabled={!creating} value={draft.id} onChange={(event) => setDraft({ ...draft, id: event.target.value })} placeholder="office-vllm" /></label>
        <label>표시 이름<input required maxLength={80} value={draft.name} onChange={(event) => setDraft({ ...draft, name: event.target.value })} placeholder="내부 모델 서버" /></label>
        <label>서버 종류<select value={draft.provider} onChange={(event) => setDraft({ ...draft, provider: event.target.value as Draft["provider"] })}><option value="vllm">vLLM / 호환 API</option><option value="ollama">Ollama</option></select></label>
        <label>서버 주소<input required type="url" maxLength={1000} value={draft.base_url} onChange={(event) => setDraft({ ...draft, base_url: event.target.value })} placeholder={draft.provider === "vllm" ? "http://192.168.0.10:8001/v1" : "http://192.168.0.10:11434"} /></label>
        <label>기본 모델 ID<input maxLength={200} value={draft.model} onChange={(event) => setDraft({ ...draft, model: event.target.value })} placeholder="서버에서 제공하는 모델 ID" /></label>
        <label>서버 동시 생성 한도<input required type="number" min={1} max={64} value={draft.max_concurrent} onChange={(event) => setDraft({ ...draft, max_concurrent: Number(event.target.value) })} /></label>
        <label>기본 문맥 한도 · 토큰<input required type="number" min={1024} max={1048576} value={draft.context_window} onChange={(event) => setDraft({ ...draft, context_window: Number(event.target.value) })} /></label>
        <label><input type="checkbox" checked={draft.supports_tools} onChange={event=>setDraft({...draft,supports_tools:event.target.checked})}/>기본 모델의 도구 호출 사용</label>
        <label>기본 최대 출력 · 토큰<input required type="number" min={64} max={16384} value={draft.max_output_tokens} onChange={(event) => setDraft({ ...draft, max_output_tokens: Number(event.target.value) })} /></label>
        <label>인증 방식<select value={draft.auth_mode} onChange={(event) => setDraft({ ...draft, auth_mode: event.target.value as Draft["auth_mode"] })}>
          <option value="none">인증키 없음</option>{items.some((item) => item.id === draft.id && item.has_environment) ? <option value="inherit">환경 설정의 기존 키 사용</option>
            : draft.auth_mode === "inherit" && <option value="inherit" disabled>기존 환경 연결 없음 · 변경 필요</option>}<option value="reference">서버에 등록한 키 사용</option></select></label>
        {draft.auth_mode === "reference" && <label>인증키 이름<select required value={draft.key_ref} onChange={(event) => setDraft({ ...draft, key_ref: event.target.value })}><option value="">선택하세요</option>{keys.map((key) => <option key={key}>{key}</option>)}</select></label>}
      </div><p className="ops-form-help">vLLM 주소는 /v1까지, Ollama는 서버 루트를 입력하세요. 문맥 한도는 실제 서빙 설정 이내로 지정하세요. 인증키 값은 서버의 LLM_PROVIDER_KEYS_JSON에 보관하며 화면에 전달하지 않습니다.</p>
      <label className="ops-wide-label">사용을 허용할 사용자명<textarea rows={2} maxLength={32000} value={users} onChange={(event) => setUsers(event.target.value)} placeholder="비우면 전체 사용자 · 쉼표 또는 줄바꿈으로 구분" /></label>
      <label className="ops-checkbox"><input type="checkbox" checked={draft.enabled} onChange={(event) => setDraft({ ...draft, enabled: event.target.checked })} />이 연결 사용</label>
      <div className="ops-model-profiles"><h4>모델별 설정</h4><p>등록하면 이 목록의 모델만 사용할 수 있습니다. 비워 두면 서버의 모델을 직접 선택합니다.</p>
        {draft.models.map((item, index) => <div className="ops-profile" key={index}>
          <label>모델 ID<input required maxLength={200} value={item.name} onChange={(event) => profile(index, { name: event.target.value })} /></label>
          <label>문맥 한도<input required type="number" min={1024} max={1048576} value={item.context_window} onChange={(event) => profile(index, { context_window: Number(event.target.value) })} /></label>
          <label>최대 출력<input required type="number" min={64} max={16384} value={item.max_output_tokens} onChange={(event) => profile(index, { max_output_tokens: Number(event.target.value) })} /></label>
          <label>동시 생성<input required type="number" min={1} max={64} value={item.max_concurrent} onChange={(event) => profile(index, { max_concurrent: Number(event.target.value) })} /></label>
          <label><input type="checkbox" checked={!!item.supports_tools} onChange={event=>profile(index,{supports_tools:event.target.checked})}/>도구 호출 사용</label>
          <button type="button" className="ops-button" aria-label={`${item.name || index + 1} 모델 설정 삭제`} onClick={() => setDraft({ ...draft, models: draft.models.filter((_, at) => at !== index) })}>삭제</button></div>)}
        <button type="button" className="ops-button" disabled={draft.models.length >= 100} onClick={() => setDraft({ ...draft, models: [...draft.models, { name: "", context_window: draft.context_window, max_output_tokens: draft.max_output_tokens, max_concurrent: draft.max_concurrent }] })}>모델 설정 추가</button>
      </div><footer className="ops-actions"><button type="button" className="ops-button" onClick={() => setDraft(null)}>취소</button><button className="ops-button">{saving ? "저장 중…" : "설정 저장"}</button></footer></fieldset>
    </form>}
  </section>;
}
