import Link from "next/link";
import type { ReactNode } from "react";

import { ThemeToggle } from "@/components/shared/ThemeToggle";

export const authInputClassName =
  "h-11 w-full rounded-[var(--radius-lg)] border border-[var(--control-border)] bg-[var(--bg)] px-3 text-sm text-[var(--fg)] outline-none transition placeholder:text-[var(--fg-disabled)] hover:border-[var(--control-border-hover)] focus:border-[var(--focus)] focus:ring-2 focus:ring-[color-mix(in_srgb,var(--focus)_16%,transparent)]";

export function AuthPageShell({ children }: { children: ReactNode }) {
  return (
    <main className="relative flex min-h-dvh items-center justify-center bg-[var(--bg)] px-4 py-10 text-[var(--fg)]">
      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>
      {children}
    </main>
  );
}

export function AuthCard({
  title,
  description,
  children,
  footer,
  wide = false,
}: {
  title: string;
  description: string;
  children: ReactNode;
  footer: ReactNode;
  wide?: boolean;
}) {
  return (
    <section className={`w-full ${wide ? "max-w-lg" : "max-w-sm"} rounded-[var(--radius-xl)] border border-[var(--border)] bg-[var(--surface)] p-6 shadow-[var(--shadow-md)]`}>
      <Link data-ui-feedback="surface" href="/" className="mb-7 inline-flex items-center gap-2">
        <span aria-hidden="true" className="grid size-8 place-items-center rounded-lg bg-[var(--primary)] text-[var(--on-primary)]">L</span>
        <span className="text-sm font-semibold">LLM</span>
      </Link>
      <h1 data-design-part="heading" className="text-2xl font-bold tracking-[-0.025em]">{title}</h1>
      <p className="mt-2 text-sm leading-6 text-[var(--fg-muted)]">
        {description}
      </p>
      {children}
      <div className="mt-5 text-center text-sm text-[var(--fg-muted)]">
        {footer}
      </div>
    </section>
  );
}

export function AuthField({
  label,
  htmlFor,
  children,
}: {
  label: string;
  htmlFor: string;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-2 text-sm font-medium text-[var(--fg)]">
      <label htmlFor={htmlFor}>{label}</label>
      {children}
    </div>
  );
}

export function AuthError({ children }: { children: ReactNode }) {
  return (
    <p
      role="alert"
      aria-live="assertive"
      className="whitespace-pre-line rounded-[var(--radius-lg)] border border-[color-mix(in_srgb,var(--error)_30%,var(--border))] bg-[var(--error-soft)] px-3 py-2 text-sm text-[var(--error)]"
    >
      {children}
    </p>
  );
}

export function AuthStatus({ children }: { children: ReactNode }) {
  return (
    <p
      role="status"
      aria-live="polite"
      className="rounded-[var(--radius-lg)] border border-[color-mix(in_srgb,var(--success)_30%,var(--border))] bg-[var(--success-soft)] px-3 py-2 text-sm text-[var(--success)]"
    >
      {children}
    </p>
  );
}
