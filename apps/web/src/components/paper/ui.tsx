"use client";
import type { ReactNode } from "react";

export type PillTone = "ok" | "warn" | "bad" | "na";

/** Session lifecycle state -> colour. */
export function stateTone(state: string): PillTone {
  if (state === "RUNNING") return "ok";
  if (["PAUSED", "RECOVERING", "INITIALIZING", "READY", "CREATED"].includes(state)) return "warn";
  if (["RISK_HALTED", "FAILED"].includes(state)) return "bad";
  return "na"; // STOPPED and anything unknown
}

export function StatePill({ state, testId }: { state: string; testId?: string }) {
  return <span className={`pill ${stateTone(state)}`} data-testid={testId}>{state}</span>;
}

/** Simulated order status -> colour (text is the status code, unchanged). */
export function orderTone(status: string): PillTone {
  if (status === "FILLED") return "ok";
  if (status === "OPEN" || status === "PARTIALLY_FILLED") return "warn";
  if (status === "REJECTED") return "bad";
  return "na";
}
export function OrderStatus({ status }: { status: string }) {
  return <span className={`pill ${orderTone(status)}`}>{status}</span>;
}

/** BUY / SELL as a coloured word (text unchanged: it is table data, never a control). */
export function Side({ side }: { side: string }) {
  return <span className={`side ${side === "BUY" ? "buy" : side === "SELL" ? "sell" : ""}`}>{side}</span>;
}

/** Signed number text coloured by sign (the text itself is never altered). */
export function Signed({ value, children }: { value: string; children: ReactNode }) {
  const neg = value.trim().startsWith("-") && /[1-9]/.test(value);
  const pos = !neg && /[1-9]/.test(value);
  return <span className={neg ? "neg" : pos ? "pos" : "zero"}>{children}</span>;
}

export function Tile({ label, children, sub, className = "" }: { label: string; children: ReactNode; sub?: ReactNode; className?: string }) {
  return (
    <div className={`card kpi ${className}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{children}</div>
      {sub !== undefined && <div className="kpi-sub">{sub}</div>}
    </div>
  );
}

export function Skeleton({ h }: { h: number }) {
  return <div className="skeleton" style={{ height: h }} aria-busy="true" />;
}

/** One stacked horizontal bar (share of a whole), with a legend of exact figures supplied by the caller. */
export function StackBar({ parts }: { parts: { label: string; value: string; share: number; tone: "accent" | "violet" | "ok" | "warn" | "muted" }[] }) {
  return (
    <div className="stack">
      <div className="stack-bar" role="img" aria-label={parts.map((p) => `${p.label} ${p.value}`).join(", ")}>
        {parts.filter((p) => p.share > 0).map((p) => <div key={p.label} className={`stack-seg ${p.tone}`} style={{ width: `${Math.max(1.5, p.share * 100)}%` }} />)}
      </div>
      <ul className="stack-legend">
        {parts.map((p) => <li key={p.label}><i className={`stack-dot ${p.tone}`} />{p.label}<b>{p.value}</b></li>)}
      </ul>
    </div>
  );
}
