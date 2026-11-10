"use client";
import { useEffect, useRef, type ReactNode } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ChatWorkspace } from "@/components/chat/ChatWorkspace";
import { Icon } from "@/components/chat/Icons";
import { modalContentScope, workspaceModal, workspaceReturnUrl, WORKSPACE_LOCATION_EVENT } from "@/lib/workspace-navigation";
import { useAuthStore } from "@/stores/authStore";
import "./workspace-shell.css";

export function WorkspaceShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const identity = useAuthStore((state) => state.user?.subject ?? "guest");
  const modal = workspaceModal(pathname);
  useEffect(() => { window.dispatchEvent(new Event(WORKSPACE_LOCATION_EVENT)); }, [pathname]);
  return <div className="workspace-app"><div className="workspace-background" data-workspace-background inert={modal ? true : undefined} aria-hidden={modal ? true : undefined}>
    <ChatWorkspace />
  </div>{modal && <RouteModal kind={modal.kind} title={modal.title} route={pathname} identity={identity}>{children}</RouteModal>}</div>;
}

function RouteModal({ children, kind, title, route, identity }: { children: ReactNode; kind: string; title: string; route: string; identity: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const backdropDown = useRef(false);
  const router = useRouter();
  const close = () => router.replace(workspaceReturnUrl(window.location.search), { scroll: false });
  useEffect(() => {
    const element = dialog.current!;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element.showModal();
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      element.close(); document.body.style.overflow = previousOverflow;
      const visible = (target: HTMLElement | null) => {
        if (!target?.isConnected || target.closest("dialog")) return false;
        const box = target.getBoundingClientRect();
        return box.width > 0 && box.height > 0 && box.right > 0 && box.left < window.innerWidth && box.bottom > 0 && box.top < window.innerHeight;
      };
      const fallback = document.querySelector<HTMLElement>("[data-workspace-focus]");
      const target = visible(opener) ? opener : visible(fallback) ? fallback : document.getElementById("chat-message");
      target?.focus({ preventScroll: true });
    };
  }, []);
  useEffect(() => { heading.current?.focus({ preventScroll: true }); }, [route, identity]);
  return <dialog ref={dialog} className={`app-modal app-modal-${kind}`} aria-modal="true" aria-labelledby="app-modal-heading"
    data-workspace-modal={kind} onCancel={(event) => { event.preventDefault(); close(); }}
    onPointerDown={(event) => { const box = event.currentTarget.getBoundingClientRect(); backdropDown.current = event.target === event.currentTarget &&
      (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom); }}
    onClick={(event) => { if (backdropDown.current && event.target === event.currentTarget) {
      const box = event.currentTarget.getBoundingClientRect();
      if (event.clientX < box.left || event.clientX > box.right || event.clientY < box.top || event.clientY > box.bottom) close();
    } }}>
    <header className="app-modal-header"><h1 ref={heading} id="app-modal-heading" tabIndex={-1}>{title}</h1>
      <button type="button" className="app-modal-close" aria-label={`${title} 닫기`} onClick={close}><Icon name="close" size={19} /></button></header>
    <div className="app-modal-body" key={modalContentScope(kind, identity)}>{children}</div>
  </dialog>;
}
