import { LoaderCircle } from "lucide-react";
import type { ButtonHTMLAttributes, ReactNode } from "react";
import clsx from "clsx";

export function Button({ className, children, busy, ...props }: ButtonHTMLAttributes<HTMLButtonElement> & { busy?: boolean }) {
  return (
    <button className={clsx("button", className)} disabled={busy || props.disabled} {...props}>
      {busy && <LoaderCircle size={15} className="spin" />}
      {children}
    </button>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span className="field-label">{label}</span>
      {children}
      {hint && <span className="field-hint">{hint}</span>}
    </label>
  );
}

export function Empty({ title, detail, action }: { title: string; detail: string; action?: ReactNode }) {
  return <div className="empty"><div className="empty-orb" /><h3>{title}</h3><p>{detail}</p>{action}</div>;
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: "neutral" | "good" | "warn" | "bad" | "accent" }) {
  return <span className={clsx("badge", `badge-${tone}`)}>{children}</span>;
}

export function Panel({ title, action, children, className }: { title?: string; action?: ReactNode; children: ReactNode; className?: string }) {
  return <section className={clsx("panel", className)}>{(title || action) && <header className="panel-header"><h3>{title}</h3>{action}</header>}{children}</section>;
}

