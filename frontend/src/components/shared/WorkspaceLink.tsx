"use client";
import Link from "next/link";
import { useSyncExternalStore, type ComponentProps } from "react";
import { workspaceHref, WORKSPACE_LOCATION_EVENT } from "@/lib/workspace-navigation";

function subscribe(callback: () => void) {
  window.addEventListener("popstate", callback);
  window.addEventListener(WORKSPACE_LOCATION_EVENT, callback);
  return () => { window.removeEventListener("popstate", callback); window.removeEventListener(WORKSPACE_LOCATION_EVENT, callback); };
}
export function WorkspaceLink({ href, ...props }: Omit<ComponentProps<typeof Link>, "href"> & { href: string }) {
  const search = useSyncExternalStore(subscribe, () => window.location.search, () => "");
  return <Link {...props} href={workspaceHref(href, search)} scroll={false} />;
}
