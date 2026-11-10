"use client";
import { useSyncExternalStore } from "react";

function subscribe(callback: () => void) {
  window.addEventListener("llm:theme", callback);
  return () => window.removeEventListener("llm:theme", callback);
}
function snapshot() { return document.documentElement.dataset.theme === "dark"; }
export function ThemeToggle() {
  const dark = useSyncExternalStore(subscribe, snapshot, () => false);
  const toggle = () => {
    document.documentElement.dataset.theme = dark ? "light" : "dark";
    try { localStorage.setItem("llm:accounts:theme", dark ? "light" : "dark"); } catch { /* Private browsing can disable storage. */ }
    window.dispatchEvent(new Event("llm:theme"));
  };
  return <button type="button" onClick={toggle} aria-label={dark ? "라이트 모드" : "다크 모드"}
    className="rounded-lg border border-[var(--border)] px-3 py-2 text-sm">{dark ? "라이트 모드" : "다크 모드"}</button>;
}
