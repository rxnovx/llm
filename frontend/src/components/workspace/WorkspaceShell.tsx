"use client";
import { Suspense, useEffect, useLayoutEffect, useRef, type ReactNode, type RefObject } from "react";
import { usePathname, useRouter } from "next/navigation";
import { ModalNavigationContext } from "@/components/auth/modal-navigation";
import { ChatWorkspace } from "@/components/chat/ChatWorkspace";
import { Icon } from "@/components/chat/Icons";
import { useDialogDismiss } from "@/components/shared/useDialogDismiss";
import { conversationIdFromPath, conversationLoginUrl, modalContentScope, workspaceConversationId, workspaceModal, workspaceReturnUrl } from "@/lib/workspace-navigation";
import { useAuthStore } from "@/stores/authStore";
import { useWorkspaceRoute, WorkspaceLocationContext, WorkspaceLocationObserver } from "./workspace-location";
import "./workspace-shell.css";

export function WorkspaceShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const location = useWorkspaceRoute(pathname);
  const identity = useAuthStore((state) => state.user?.subject ?? "guest");
  const initialized = useAuthStore((state) => state.initialized);
  const router = useRouter();
  const modal = workspaceModal(pathname);
  useEffect(() => {
    const id = conversationIdFromPath(pathname);
    if (id && initialized && identity === "guest" && window.location.pathname === pathname) {
      router.replace(conversationLoginUrl(id), { scroll: false });
    }
  }, [pathname, initialized, identity, router]);
  return <WorkspaceLocationContext.Provider value={location}>
    <Suspense fallback={null}><WorkspaceLocationObserver /></Suspense>
    <div className="workspace-app"><div className="workspace-background" data-workspace-background inert={modal ? true : undefined} aria-hidden={modal ? true : undefined}>
      <ChatWorkspace conversationId={workspaceConversationId(location)} pathname={location.pathname} />
    </div>{modal && <RouteModal kind={modal.kind} title={modal.title} route={pathname} identity={identity}>{children}</RouteModal>}</div>
  </WorkspaceLocationContext.Provider>;
}

function RouteModal({ children, kind, title, route, identity }: { children: ReactNode; kind: string; title: string; route: string; identity: string }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const heading = useRef<HTMLHeadingElement>(null);
  const closing = useRef(false);
  const router = useRouter();
  const close = () => {
    closing.current = true;
    router.replace(workspaceReturnUrl(window.location.search), { scroll: false });
  };
  const dismiss = useDialogDismiss(close);
  useLayoutEffect(() => { closing.current = false; }, [route]);
  useEffect(() => {
    const element = dialog.current!;
    const opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    element.showModal();
    return () => {
      element.close();
      const visible = (target: HTMLElement | null) => {
        if (!target?.isConnected || target === document.body || target.closest("dialog, [inert]")) return false;
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
    data-workspace-modal={kind} {...dismiss}>
    <header className="app-modal-header"><h1 ref={heading} id="app-modal-heading" tabIndex={-1}>{title}</h1>
      <button type="button" className="app-modal-close" aria-label={`${title} 닫기`} onClick={close}><Icon name="close" size={19} /></button></header>
    <RouteModalBody key={route} route={route} scope={modalContentScope(kind, identity)} closing={closing}>{children}</RouteModalBody>
  </dialog>;
}

function RouteModalBody({ children, route, scope, closing }: {
  children: ReactNode; route: string; scope: string; closing: RefObject<boolean>;
}) {
  const body = useRef<HTMLDivElement>(null);
  const canNavigate = () => body.current !== null && !closing.current && window.location.pathname === route;
  return <ModalNavigationContext.Provider value={canNavigate}>
    <div className="app-modal-body" ref={body} key={scope}>{children}</div>
  </ModalNavigationContext.Provider>;
}
