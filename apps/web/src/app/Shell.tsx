"use client";
import Link from "next/link";
import type { ReactNode } from "react";
import { api, type Me, type Risk } from "../lib/api";
import { canSee } from "../lib/format";
import { usePoll } from "../lib/usePoll";

export default function Shell({ children }: { children: ReactNode }) {
  const me = usePoll<Me>("/v1/auth/me", 60000);
  const risk = usePoll<Risk>("/v1/risk", 5000);
  const halted = risk.data?.halt_new_buys ?? false;

  async function logout() {
    // Root cause of the intermittent e2e failure: a click before /v1/auth/me had loaded was a silent
    // no-op. Fetch the principal on demand so Sign out always works.
    const principal = me.data ?? (await api<{ csrf_token: string }>("/v1/auth/me"));
    await api("/v1/auth/logout", {
      method: "POST",
      headers: { "X-CSRF-Token": principal.csrf_token },
    });
    window.location.href = "/login";
  }

  return (
    <>
      <div className={halted ? "banner halt" : "banner"} role="status" data-testid="banner">
        {halted ? `RISK HALT — new buys suspended (${risk.data?.halt_reasons.join(", ")}). ` : ""}
        PAPER · PUBLIC MARKET DATA ONLY · NO TRADING · NO REAL FUNDS
      </div>
      <nav>
        <strong>ASTA</strong>
        <Link href="/">Overview</Link>
        <Link href="/markets">Markets</Link>
        <Link href="/risk">Risk</Link>
        <Link href="/research/datasets">Research</Link>
        <Link href="/paper">Paper trading</Link>
        {me.data && canSee(me.data.role, "audit") && <Link href="/audit">Audit</Link>}
        <span className="sp" />
        {me.data && (
          <span className="muted">
            {me.data.username} ({me.data.role})
          </span>
        )}
        <button onClick={logout}>Sign out</button>
      </nav>
      <main>{children}</main>
    </>
  );
}
