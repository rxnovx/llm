"use client";
import { createContext, useContext } from "react";
import { useRouter } from "next/navigation";
import { loginReturnUrl, replaceConversationLocation } from "@/lib/workspace-navigation";

export const ModalNavigationContext = createContext<() => boolean>(() => true);

/** Keeps an async action's navigation scoped to the modal where it began. */
export function useModalNavigation() {
  return useContext(ModalNavigationContext);
}

export function useCompleteLogin() {
  const canNavigate = useModalNavigation();
  const router = useRouter();
  return () => {
    if (!canNavigate()) return;
    const search = window.location.search;
    const destination = loginReturnUrl(search);
    if (destination === "/" && new URLSearchParams(search).has("chat")) {
      // The authenticated workspace reads the current URL on its first mount.
      replaceConversationLocation(null);
    }
    router.replace(destination, { scroll: false });
  };
}
