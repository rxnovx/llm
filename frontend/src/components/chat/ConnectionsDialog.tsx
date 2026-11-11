"use client";
import { useEffect, useRef, useState } from "react";
import { useDialogDismiss } from "@/components/shared/useDialogDismiss";
import { chatJson } from "@/lib/chat-api";
import type { Connection } from "@/types/chat";
import { Icon } from "./Icons";

type Props = { open: boolean; onClose: () => void; connections: Connection[]; selected: string; model: string;
  onSelect: (id: string, model: string) => void; disabled: boolean };

export function ConnectionsDialog({ open, onClose, connections, selected, model, onSelect, disabled }: Props) {
  const dialog = useRef<HTMLDialogElement>(null);
  const dismiss = useDialogDismiss(onClose);
  const [checking, setChecking] = useState("");
  const [results, setResults] = useState<Record<string, { models: string[]; error?: string }>>({});
  useEffect(() => {
    if (open && !dialog.current?.open) dialog.current?.showModal();
    else if (!open && dialog.current?.open) dialog.current.close();
  }, [open]);
  const check = async (item: Connection) => {
    setChecking(item.id);
    try {
      const result = await chatJson<{ models: string[] }>(`/connections/${item.id}/check`, { method: "POST", body: "{}" });
      setResults((old) => ({ ...old, [item.id]: result }));
      if (dialog.current?.open && item.id === selected && !model && result.models[0]) onSelect(item.id, result.models[0]);
    } catch (error) {
      setResults((old) => ({ ...old, [item.id]: { models: [], error: error instanceof Error ? error.message : "연결을 확인하지 못했습니다." } }));
    } finally { setChecking(""); }
  };
  return <dialog ref={dialog} className="cw-dialog" aria-labelledby="connections-title" {...dismiss} onClose={onClose}>
    <header className="cw-dialog-header"><div><span className="cw-overline">워크스페이스 설정</span><h2 id="connections-title">모델 연결</h2></div>
      <button className="cw-icon-button" aria-label="연결 설정 닫기" onClick={onClose}><Icon name="close" /></button></header>
    <p className="cw-dialog-description">대화에 사용할 연결을 선택하세요. 모델은 언제든 바꿀 수 있습니다.</p>
    <div className="cw-connections">{connections.map((item) => {
      const result = results[item.id];
      const active = item.id === selected;
      const availableModels = item.models?.length ? item.models.map((profile) => profile.name) : result?.models ?? [];
      return <section key={item.id} className={`cw-connection ${active ? "is-selected" : ""}`}>
        <div className="cw-connection-heading"><span className="cw-connection-icon"><Icon name={item.is_test ? "chat" : "server"} size={20} /></span>
          <div><h3>{item.name}</h3><span>{item.is_test ? "서버 없이 사용" : item.provider === "vllm" ? "vLLM · 호환 API" : "Ollama"}</span></div>
          <button className="cw-small-button" disabled={disabled} onClick={() => onSelect(item.id, item.default_model || availableModels[0] || "")}
            aria-pressed={active}>{active ? <><Icon name="check" size={14} />선택됨</> : "선택"}</button></div>
        {active && <div className="cw-connection-detail">{item.is_test ? <p>랜덤 예시 답변으로 대화 흐름을 테스트합니다. 실제 모델을 호출하지 않습니다.</p> : <>
          <label htmlFor={`model-${item.id}`}>모델 이름</label>
          {availableModels.length ? <select id={`model-${item.id}`} value={model} disabled={disabled} onChange={(e) => onSelect(item.id, e.target.value)}>
            {model && !availableModels.includes(model) && <option value={model} disabled>{model} (현재 목록에 없음)</option>}
            {availableModels.map((name) => <option key={name}>{name}</option>)}</select> : <input id={`model-${item.id}`} value={model}
              placeholder="예: gpt-oss-120b" maxLength={200} disabled={disabled} onChange={(e) => onSelect(item.id, e.target.value)} />}
          <div className="cw-connection-check"><button className="cw-text-button" disabled={!!checking} onClick={() => void check(item)}>
            <Icon name="refresh" size={14} />{checking === item.id ? "확인 중…" : "연결 확인"}</button>
            {result && !result.error && <span className="cw-connected">모델 {result.models.length}개 확인됨</span>}</div>
          {result?.error && <p className="cw-inline-error" role="alert">{result.error}</p>}</>}</div>}
      </section>;
    })}</div>
    {!connections.length && <p className="cw-dialog-description">등록된 연결이 없습니다. 서버 관리자에게 연결 등록을 요청하세요.</p>}
    <footer className="cw-dialog-footer"><span>사용 가능한 서버 연결은 운영자가 관리합니다.</span>
      {selected && <button className="cw-text-button" disabled={disabled} onClick={() => onSelect("", "")}>연결 해제</button>}</footer>
  </dialog>;
}
