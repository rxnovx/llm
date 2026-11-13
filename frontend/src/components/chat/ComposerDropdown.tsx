"use client";

import { useEffect, useEffectEvent, useId, useLayoutEffect, useRef, type ReactNode } from "react";
import "./composer-controls.css";

type Props = {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  label: string;
  panelLabel: string;
  className: string;
  trigger: ReactNode;
  align?: "start" | "end";
  disabled?: boolean;
  children: ReactNode;
};

export function ComposerDropdown({ open, onOpenChange, label, panelLabel, className, trigger,
  align = "start", disabled, children }: Props) {
  const id = useId();
  const root = useRef<HTMLDivElement>(null);
  const button = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const dismiss = useEffectEvent(() => onOpenChange(false));
  const restoreFocus = useRef(false);

  useLayoutEffect(() => {
    if (!open || disabled || !button.current || !panel.current) return;
    const anchor = button.current;
    const popup = panel.current;
    const viewport = window.visualViewport;
    const place = () => {
      const rect = anchor.getBoundingClientRect();
      const gap = 8;
      const left = viewport?.offsetLeft ?? 0;
      const top = viewport?.offsetTop ?? 0;
      const right = left + (viewport?.width ?? document.documentElement.clientWidth);
      const bottom = top + (viewport?.height ?? window.innerHeight);
      const width = Math.min(304, right - left - 2 * gap);
      const idealLeft = align === "start" ? rect.left : rect.right - width;
      const above = rect.top - top - 2 * gap;
      const below = bottom - rect.bottom - 2 * gap;
      const upward = above >= 280 || above >= below;
      Object.assign(popup.style, {
        width: `${width}px`,
        left: `${Math.max(left + gap, Math.min(idealLeft, right - width - gap))}px`,
        maxHeight: `${Math.max(0, Math.min(360, upward ? above : below))}px`,
        top: `${upward ? rect.top - gap : rect.bottom + gap}px`,
        transform: upward ? "translateY(-100%)" : "",
      });
    };
    place();
    restoreFocus.current = true;
    popup.focus({ preventScroll: true });
    const observer = new ResizeObserver(place);
    observer.observe(anchor);
    observer.observe(anchor.closest("form") ?? anchor);
    window.addEventListener("scroll", place, { capture: true, passive: true });
    window.addEventListener("resize", place);
    viewport?.addEventListener("resize", place);
    viewport?.addEventListener("scroll", place);
    return () => {
      if (restoreFocus.current && (popup.contains(document.activeElement) || document.activeElement === document.body)) {
        anchor.focus({ preventScroll: true });
      }
      observer.disconnect();
      window.removeEventListener("scroll", place, true);
      window.removeEventListener("resize", place);
      viewport?.removeEventListener("resize", place);
      viewport?.removeEventListener("scroll", place);
    };
  }, [align, disabled, open]);

  useEffect(() => {
    if (!open || disabled) return;
    const outside = (event: PointerEvent) => {
      if (event.target instanceof Node && !root.current?.contains(event.target)) {
        restoreFocus.current = false;
        dismiss();
      }
    };
    document.addEventListener("pointerdown", outside);
    return () => document.removeEventListener("pointerdown", outside);
  }, [disabled, open]);

  const expanded = open && !disabled;
  return <div ref={root} className="cw-composer-control"
    onBlur={event => {
      if (expanded && event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget)) {
        restoreFocus.current = false;
        onOpenChange(false);
      }
    }}
    onKeyDown={event => {
      if (!expanded || event.nativeEvent.isComposing) return;
      if (event.key === "Escape") {
        event.preventDefault();
        event.stopPropagation();
        onOpenChange(false);
      } else if (event.target instanceof HTMLElement && !event.target.matches("input") &&
        ["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) {
        const options = Array.from(panel.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? []);
        if (!options.length) return;
        event.preventDefault();
        const current = options.indexOf(event.target as HTMLButtonElement);
        const next = event.key === "Home" ? 0 : event.key === "End" ? options.length - 1
          : event.key === "ArrowDown" ? (current + 1) % options.length
          : (current <= 0 ? options.length : current) - 1;
        options[next]?.focus();
      }
    }}>
    <button ref={button} type="button" className={className} aria-label={label} disabled={disabled}
      aria-expanded={expanded} aria-controls={expanded ? id : undefined} onClick={() => onOpenChange(!expanded)}
      onKeyDown={event => {
        if (!expanded && event.key === "ArrowDown") {
          event.preventDefault();
          onOpenChange(true);
        }
      }}>
      {trigger}
    </button>
    {expanded && <div ref={panel} id={id} className="cw-composer-popover" role="group" aria-label={panelLabel} tabIndex={-1}>
      {children}
    </div>}
  </div>;
}
