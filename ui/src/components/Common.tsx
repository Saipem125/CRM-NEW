import { useEffect, type ReactNode } from "react";
import type { Condition, Confidence } from "../api/types";

/** Confidence badge: colour + label + icon, never colour alone (§4.1). */
export function Badge({ level, large }: { level: Confidence; large?: boolean }) {
  const icon = level === "HIGH" ? "●" : level === "MEDIUM" ? "◐" : "○";
  const text = level === "HIGH" ? "HIGH confidence" : level === "MEDIUM" ? "MEDIUM confidence" : "LOW · screening only";
  return <span className={`badge ${level} ${large ? "lg" : ""}`} role="status" aria-label={text}><span aria-hidden>{icon}</span>{text}</span>;
}

export function TypeTag({ type }: { type: string }) {
  const cls = type === "INJ" ? "inj" : type === "PROD" ? "prod" : type === "MIXED" ? "mixed" : "";
  const glyph = type === "INJ" ? "▼" : type === "PROD" ? "●" : type === "MIXED" ? "◐" : "·";
  return <span className={`tag ${cls}`}>{glyph} {type}</span>;
}

export function Msg({ severity, message, action }: { severity: "info" | "warning" | "error"; message: string; action?: string }) {
  return (
    <div className={`msg ${severity}`} role={severity === "error" ? "alert" : "note"}>
      <div>{message}</div>
      {action && <div className="action">{action}</div>}
    </div>
  );
}

/** Conditions are rendered only through their plain-language sentence + action (§17). */
export function Conditions({ items, max = 8 }: { items: Condition[]; max?: number }) {
  const shown = [...items].sort((a, b) => rank(b.severity) - rank(a.severity)).slice(0, max);
  if (!shown.length) return <div className="sub">No issues raised.</div>;
  return <div className="stack">{shown.map((c, k) => <Msg key={k} severity={c.severity} message={c.message} action={c.action} />)}</div>;
}
const rank = (s: string) => (s === "error" ? 2 : s === "warning" ? 1 : 0);

export function Skeleton({ lines = 4, height = 14 }: { lines?: number; height?: number }) {
  return <div className="stack" aria-busy>{Array.from({ length: lines }).map((_, k) => <div key={k} className="skeleton" style={{ height, width: `${100 - (k % 3) * 12}%` }} />)}</div>;
}

export function Steps({ items, active }: { items: string[]; active: number }) {
  return (
    <ol className="steps" aria-label="Steps">
      {items.map((s, k) => <li key={s} className={`step ${k === active ? "active" : k < active ? "done" : ""}`}><span className="n">{k < active ? "✓" : k + 1}</span>{s}</li>)}
    </ol>
  );
}

export function Drawer({ open, title, onClose, children }: { open: boolean; title: string; onClose: () => void; children: ReactNode }) {
  useEffect(() => {
    if (!open) return;
    const h = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [open, onClose]);
  if (!open) return null;
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" role="dialog" aria-modal="false" aria-label={title}>
        <div className="head"><h2>{title}</h2><button className="sm" onClick={onClose} autoFocus>Close ✕</button></div>
        {children}
      </aside>
    </>
  );
}

export function KV({ rows }: { rows: Array<[string, ReactNode]> }) {
  return <div className="kv">{rows.map(([k, v]) => [<span key={k + "k"} className="k">{k}</span>, <span key={k + "v"} className="v">{v}</span>])}</div>;
}

export function Light({ state, label }: { state: "green" | "amber" | "red"; label: string }) {
  const word = state === "green" ? "good" : state === "amber" ? "check" : "problem";
  return <span className="row" style={{ gap: 6 }}><span className={`light ${state}`} aria-hidden /> <span>{label}</span> <span className="tag">{word}</span></span>;
}

/** Numbers: thousands separators, unit next to it (§4.4, §4.6 "every number has a unit"). */
export function Num({ v, unit, digits = 0 }: { v: number | null | undefined; unit?: string; digits?: number }) {
  if (v === null || v === undefined || Number.isNaN(v)) return <span className="num dim">—</span>;
  return <span className="num">{v.toLocaleString(undefined, { maximumFractionDigits: digits, minimumFractionDigits: digits })}{unit ? <span className="unit"> {unit}</span> : null}</span>;
}

export function Empty({ text, action }: { text: string; action?: ReactNode }) {
  return <div className="card" style={{ textAlign: "center", color: "var(--dim)", padding: 28 }}><div>{text}</div>{action && <div style={{ marginTop: 10 }}>{action}</div>}</div>;
}
