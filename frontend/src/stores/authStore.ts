"use client";
import { create } from "zustand";
import { api, authApi } from "@/lib/api";
import { clearAccessToken, consumeRefreshedUser, getAuthSessionVersion,
  refreshAccessToken, setAccessToken, withAuthSessionLock } from "@/lib/auth";
import type { AuthSessionResponse, AuthUser } from "@/types/auth";

type State = {
  user: AuthUser | null; initialized: boolean; error: string | null;
  initialize: () => Promise<void>; refreshUser: () => Promise<void>;
  login: (data: { loginId: string; password: string }) => Promise<void>;
  loginWithGoogle: (credential: string, acceptTerms?: boolean) => Promise<void>;
  logout: (allSessions?: boolean, expectedSessionVersion?: number) => Promise<boolean>;
  clear: () => void;
};
let initialization: Promise<void> | null = null;
export const useAuthStore = create<State>((set, get) => ({
  user: null, initialized: false, error: null,
  initialize: async () => {
    if (get().initialized) return;
    if (initialization) return initialization;
    initialization = (async () => {
      const token = await refreshAccessToken();
      if (token) {
        const user = consumeRefreshedUser();
        if (user) set({ user });
        else await get().refreshUser();
      }
      set({ initialized: true });
    })().catch(() => set({ initialized: true, error: "계정 정보를 불러오지 못했습니다. 다시 시도해주세요." }))
      .finally(() => { initialization = null; });
    return initialization;
  },
  refreshUser: async () => {
    const version = getAuthSessionVersion();
    const response = await api.get<AuthUser>("/users/me");
    if (version === getAuthSessionVersion()) set({ user: response.data, initialized: true, error: null });
  },
  login: async (data) => withAuthSessionLock(async () => {
    const response = await authApi.post<AuthSessionResponse>("/login", data);
    setAccessToken(response.data.access_token);
    set({ user: response.data.user, initialized: true, error: null });
  }),
  loginWithGoogle: async (credential, acceptTerms = false) => withAuthSessionLock(async () => {
    const response = await authApi.post<AuthSessionResponse>("/google", { credential, accept_terms: acceptTerms });
    setAccessToken(response.data.access_token);
    set({ user: response.data.user, initialized: true, error: null });
  }),
  logout: async (allSessions = false, expectedSessionVersion) => withAuthSessionLock(async () => {
    if (expectedSessionVersion !== undefined && expectedSessionVersion !== getAuthSessionVersion()) return false;
    // Keep the session available for retry if the API could not revoke it.
    await authApi.post("/logout", { all_sessions: allSessions });
    clearAccessToken(true);
    set({ user: null, initialized: true, error: null });
    return true;
  }),
  clear: () => set({ user: null, initialized: true, error: null }),
}));
