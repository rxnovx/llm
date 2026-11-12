"use client";

import { createContext, useContext, useEffect, useSyncExternalStore } from "react";
import { usePathname, useSearchParams } from "next/navigation";
import { notifyWorkspaceLocation, WORKSPACE_LOCATION_EVENT } from "@/lib/workspace-navigation";

export const WorkspaceLocationContext = createContext({ pathname: "/", search: "" });

function subscribe(callback: () => void) {
  window.addEventListener("popstate", callback);
  window.addEventListener(WORKSPACE_LOCATION_EVENT, callback);
  return () => {
    window.removeEventListener("popstate", callback);
    window.removeEventListener(WORKSPACE_LOCATION_EVENT, callback);
  };
}

export function useWorkspaceRoute(serverPathname: string) {
  // Read pathname and query together so a modal transition keeps its conversation.
  const href = useSyncExternalStore(subscribe,
    () => window.location.pathname + window.location.search,
    () => serverPathname);
  const queryStart = href.indexOf("?");
  return queryStart === -1
    ? { pathname: href, search: "" }
    : { pathname: href.slice(0, queryStart), search: href.slice(queryStart) };
}

export function useWorkspaceLocation() {
  return useContext(WorkspaceLocationContext);
}

// Isolate the useSearchParams boundary from the persistent workspace.
export function WorkspaceLocationObserver() {
  const pathname = usePathname();
  const search = useSearchParams();
  useEffect(() => { notifyWorkspaceLocation(); }, [pathname, search]);
  return null;
}
