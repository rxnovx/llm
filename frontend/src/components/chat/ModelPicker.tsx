"use client";

import { useState } from "react";
import { chatJson } from "@/lib/chat-api";
import type { Connection } from "@/types/chat";
import { ComposerDropdown } from "./ComposerDropdown";
import { Icon } from "./Icons";

type Props = {
  connections: Connection[];
  selected: string;
  model: string;
  open: boolean;
  disabled: boolean;
  onOpenChange: (open: boolean) => void;
  onSelect: (connection: string, model: string) => void;
};

type Check = { models: string[]; error?: string };

function modelNames(connection: Connection, result: Check | undefined, current: string) {
  if (connection.is_test) return [connection.default_model || "demo"];
  if (connection.models.length) return connection.models.map(profile => profile.name);
  return [...new Set([connection.default_model, current, ...(result?.models ?? [])].filter(Boolean))];
}

export function ModelPicker({ connections, selected, model, open, disabled, onOpenChange, onSelect }: Props) {
  const connection = connections.find(item => item.id === selected);
  const current = connection?.is_test ? "테스트 모드" : connection && (model || connection.name);
  const [checking, setChecking] = useState("");
  const [results, setResults] = useState<Record<string, Check>>({});
  const [manual, setManual] = useState<{ id: string; model: string } | null>(null);
  const changeOpen = (value: boolean) => { setManual(null); onOpenChange(value); };
  const select = (id: string, name: string) => {
    onSelect(id, name);
    changeOpen(false);
  };

  const check = async (item: Connection) => {
    setChecking(item.id);
    try {
      const result = await chatJson<Check>(`/connections/${item.id}/check`, {
        method: "POST", body: "{}", signal: AbortSignal.timeout(20_000),
      });
      setResults(previous => ({ ...previous, [item.id]: result }));
    } catch (error) {
      setResults(previous => ({ ...previous, [item.id]: { models: previous[item.id]?.models ?? [],
        error: error instanceof Error ? error.message : "연결을 확인하지 못했습니다." } }));
    } finally {
      setChecking("");
    }
  };

  return <ComposerDropdown open={open} disabled={disabled} align="end" className="cw-model-picker"
    label={current ? `모델 선택 · ${current}` : "모델 선택"} panelLabel="모델 목록"
    onOpenChange={changeOpen}
    trigger={<><span>{current || "모델 선택"}</span><Icon name="chevron" size={14} /></>}>
    {!connections.length && <p className="cw-composer-popover-note">등록된 모델 연결이 없습니다.</p>}
    {connections.map(item => {
      const active = item.id === selected;
      const result = results[item.id];
      const names = modelNames(item, result, active ? model : "");
      const manualValue = manual?.id === item.id ? manual.model : null;
      const applyManual = () => { if (manualValue?.trim()) select(item.id, manualValue.trim()); };
      return <section key={item.id} className="cw-model-group" aria-label={item.name}>
        {!item.is_test && <header>
          <span title={item.name}>{item.name}</span>
          <button type="button" className="cw-text-button" disabled={!!checking}
            onClick={() => void check(item)}>{checking === item.id ? "확인 중…" : "연결 확인"}</button>
        </header>}
        {names.map(name => {
          const checked = active && (item.is_test || model === name);
          return <button key={name} type="button" className="cw-composer-option" aria-pressed={checked}
            onClick={() => select(item.id, name)}>
            <Icon name={item.is_test ? "chat" : "server"} size={16} />
            <span>{item.is_test ? "테스트 모드" : name}</span>
            {checked && <Icon name="check" size={15} />}
          </button>;
        })}
        {!item.is_test && !item.models.length && <div className="cw-model-manual">
          {manualValue === null ? <button type="button" className="cw-text-button"
            onClick={() => setManual({ id: item.id, model: active ? model : "" })}>모델 ID 직접 입력</button>
            : <div>
              <input autoFocus aria-label="모델 ID" placeholder="모델 ID" value={manualValue} maxLength={200}
                onChange={event => setManual({ id: item.id, model: event.target.value })}
                onKeyDown={event => {
                  if (event.key !== "Enter") return;
                  event.preventDefault();
                  if (!event.nativeEvent.isComposing) applyManual();
                }} />
              <button type="button" className="cw-small-button" disabled={!manualValue.trim()} onClick={applyManual}>선택</button>
            </div>}
        </div>}
        {result?.error ? <p className="cw-composer-popover-note cw-inline-error" role="alert">{result.error}</p>
          : result && <p className="cw-composer-popover-note" role="status">모델 {result.models.length}개 확인됨</p>}
      </section>;
    })}
    {selected && <footer className="cw-model-popover-footer"><button type="button" className="cw-text-button"
      onClick={() => select("", "")}>연결 해제</button></footer>}
  </ComposerDropdown>;
}
