"use client";
import Link from "next/link";
import type { ComponentProps } from "react";
import { useWorkspaceLocation } from "@/components/workspace/workspace-location";
import { workspaceHref } from "@/lib/workspace-navigation";

export function WorkspaceLink({ href, ...props }: Omit<ComponentProps<typeof Link>, "href"> & { href: string }) {
  const location = useWorkspaceLocation();
  return <Link {...props} href={workspaceHref(href, location)} scroll={false} />;
}
