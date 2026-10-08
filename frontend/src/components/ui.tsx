import type { ReactNode, ButtonHTMLAttributes, InputHTMLAttributes } from "react";
import type { Step } from "../types";

export function Button({ variant = "primary", className = "", ...p }: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: "primary" | "secondary" | "danger" }) {
  const base = "inline-flex items-center justify-center gap-2 rounded px-4 py-2 text-sm font-semibold transition-colors disabled:opacity-50 disabled:cursor-not-allowed";
  const v = {
    primary: "bg-accent text-white hover:bg-accent-dark",
    secondary: "bg-surface text-ink border border-line hover:bg-canvas",
    danger: "bg-surface text-danger border border-danger hover:bg-red-50",
  }[variant];
  return <button className={`${base} ${v} ${className}`} {...p} />;
}

export function Field({ label, hint, ...p }: InputHTMLAttributes<HTMLInputElement> & { label: string; hint?: string }) {
  return (
    <label className="block">
      <span className="mb-1 block text-sm font-medium">{label}</span>
      <input className="w-full rounded border border-line bg-surface px-3 py-2 text-sm placeholder:text-muted/70" {...p} />
      {hint && <span className="mt-1 block text-xs text-muted">{hint}</span>}
    </label>
  );
}

export function Toggle({ label, checked, onChange, hint }: { label: string; checked: boolean; onChange: (v: boolean) => void; hint?: string }) {
  return (
    <label className="flex cursor-pointer items-start gap-3">
      <input type="checkbox" className="mt-1 h-4 w-4 accent-[#0E5A63]" checked={checked} onChange={(e) => onChange(e.target.checked)} />
      <span>
        <span className="block text-sm font-medium">{label}</span>
        {hint && <span className="block text-xs text-muted">{hint}</span>}
      </span>
    </label>
  );
}

export function Panel({ title, children, actions }: { title?: string; children: ReactNode; actions?: ReactNode }) {
  return (
    <section className="rounded border border-line bg-surface p-5">
      {(title || actions) && (
        <div className="mb-4 flex items-center justify-between gap-3">
          {title && <h2 className="font-display text-lg">{title}</h2>}
          {actions}
        </div>
      )}
      {children}
    </section>
  );
}

export function ErrorBanner({ code, message, onDismiss }: { code?: string | null; message: string; onDismiss?: () => void }) {
  return (
    <div role="alert" className="flex items-start justify-between gap-3 rounded border border-danger bg-red-50 p-3 text-sm text-danger">
      <div>
        <p className="font-semibold">{message}</p>
        {code && <p className="mt-0.5 text-xs opacity-80">Error code: {code}</p>}
      </div>
      {onDismiss && <button onClick={onDismiss} className="text-xs underline">Dismiss</button>}
    </div>
  );
}

export function Warnings({ items }: { items: string[] }) {
  if (!items.length) return null;
  return (
    <div className="rounded border border-best/60 bg-amber-50 p-3 text-sm">
      <p className="mb-1 font-semibold">Worth a look</p>
      <ul className="list-disc space-y-0.5 pl-5">{items.map((w, i) => <li key={i}>{w}</li>)}</ul>
    </div>
  );
}

function Mark({ status }: { status: Step["status"] }) {
  if (status === "running") return <span className="spin inline-block h-4 w-4 rounded-full border-2 border-accent border-t-transparent" aria-label="running" />;
  if (status === "done") return <span className="flex h-4 w-4 items-center justify-center rounded-full bg-ok text-[10px] text-white" aria-label="done">✓</span>;
  if (status === "error") return <span className="flex h-4 w-4 items-center justify-center rounded-full bg-danger text-[10px] text-white" aria-label="error">×</span>;
  if (status === "skipped") return <span className="flex h-4 w-4 items-center justify-center text-muted" aria-label="skipped">–</span>;
  return <span className="inline-block h-4 w-4 rounded-full border border-line" aria-label="pending" />;
}

export function ProgressList({ steps }: { steps: Step[] }) {
  return (
    <ol className="space-y-1.5">
      {steps.map((s) => (
        <li key={s.key} className={`flex items-start gap-3 text-sm ${s.status === "pending" ? "text-muted" : ""}`}>
          <span className="mt-0.5"><Mark status={s.status} /></span>
          <span>
            {s.label}
            {s.detail && s.status !== "pending" && <span className="ml-2 break-all text-xs text-muted">{s.detail}</span>}
          </span>
        </li>
      ))}
    </ol>
  );
}

export const STEP_NAMES = ["Connect", "Find product", "Source URL", "Analysis", "Selection", "Review", "Publish", "Result"];

export function Rail({ screen, reachable, onGo }: { screen: number; reachable: number; onGo: (n: number) => void }) {
  return (
    <nav aria-label="Workflow" className="lg:sticky lg:top-6">
      <ol className="flex gap-1 overflow-x-auto lg:flex-col">
        {STEP_NAMES.map((n, i) => {
          const num = i + 1;
          const active = num === screen;
          const ok = num <= reachable;
          return (
            <li key={n}>
              <button
                disabled={!ok || num === 8}
                onClick={() => onGo(num)}
                aria-current={active ? "step" : undefined}
                className={`flex w-full items-center gap-3 whitespace-nowrap rounded px-3 py-2 text-left text-sm ${
                  active ? "bg-accent text-white" : ok ? "hover:bg-surface" : "text-muted/60"
                }`}
              >
                <span className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-xs ${active ? "bg-white text-accent" : "border border-current"}`}>{num}</span>
                {n}
              </button>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}

export function Star({ className = "" }: { className?: string }) {
  return <span className={`text-best ${className}`} aria-label="best image">★</span>;
}
