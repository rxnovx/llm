"use client";
import { createContext, useContext } from "react";
import { useRouter } from "next/navigation";
import { loginReturnUrl, updateConversationLocation, workspaceConversationId } from "@/lib/workspace-navigation";

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
    const destination = loginReturnUrl(window.location.search);
    // Drop the guest modal's background before activating a fresh workspace.
    if (destination === "/" && workspaceConversationId(window.location)) updateConversationLocation(null, "replace");
    router.replace(destination, { scroll: false });
  };
}
