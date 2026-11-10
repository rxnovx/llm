"use client";
import { useEffect } from "react";
import { AUTH_EXPIRED_EVENT, AUTH_TOKEN_UPDATED_EVENT } from "@/lib/auth";
import { useAuthStore } from "@/stores/authStore";

export function AuthInitializer() {
  useEffect(() => {
    const theme = (() => { try { return localStorage.getItem("llm:accounts:theme"); } catch { return null; } })();
    document.documentElement.dataset.theme = theme ?? (matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light");
    void useAuthStore.getState().initialize();
    const clear = () => useAuthStore.getState().clear();
    const refresh = () => { void useAuthStore.getState().refreshUser().catch(clear); };
    window.addEventListener(AUTH_EXPIRED_EVENT, clear);
    window.addEventListener(AUTH_TOKEN_UPDATED_EVENT, refresh);
    return () => {
      window.removeEventListener(AUTH_EXPIRED_EVENT, clear);
      window.removeEventListener(AUTH_TOKEN_UPDATED_EVENT, refresh);
    };
  }, []);
  return null;
}
