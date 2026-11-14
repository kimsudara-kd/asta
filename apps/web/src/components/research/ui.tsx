"use client";
import type { ReactNode } from "react";

/** Backtest lifecycle -> pill. Text is the status code, unchanged. */
export function RunStatus({ status, progress, testId }: { status: string; progress?: number; testId?: string }) {
  const tone = status === "COMPLETED" ? "ok" : status === "FAILED" || status === "CANCELLED" ? "bad" : "warn";
  return <span className={`pill ${tone}`} data-testid={testId}>{status}{status === "RUNNING" && progress !== undefined ? ` ${progress}%` : ""}</span>;
}

export function Verdict({ verdict, long = false }: { verdict: string | null; long?: boolean }) {
  if (!verdict) return <>—</>;
  const ok = verdict === "PASSED";
  return <span className={`pill ${ok ? "ok" : "bad"}`}>{long ? (ok ? "WITHIN RISK BUDGET" : "FAILED RISK BUDGET") : ok ? "within budget" : "FAILED budget"}</span>;
}

/** Numbered configuration step (Strategy lab). */
export function Step({ n, title, hint, children }: { n: number; title: string; hint?: string; children: ReactNode }) {
  return (
    <section className="card step">
      <div className="step-head">
        <span className="step-n" aria-hidden="true">{n}</span>
        <div>
          <h2 className="card-title">{title}</h2>
          {hint && <p className="muted step-hint">{hint}</p>}
        </div>
      </div>
      {children}
    </section>
  );
}

/** Small key/value fact used in headers. */
export function Fact({ k, children }: { k: string; children: ReactNode }) {
  return <span className="p-fact"><span className="p-fact-k">{k}</span>{children}</span>;
}
