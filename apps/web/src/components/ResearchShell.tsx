"use client";
import Link from "next/link";
import type { ReactNode } from "react";
import Shell from "../app/Shell";
import type { Me } from "../lib/api";
import { RESEARCH_BANNER } from "../lib/format";
import { usePoll } from "../lib/usePoll";

const TABS = [
  { href: "/research/datasets", label: "Historical datasets" },
  { href: "/research/lab", label: "Strategy lab" },
  { href: "/research/results", label: "Backtest results" },
  { href: "/research/compare", label: "Research comparison" },
];

/** Every research page: the standard shell + a persistent SIMULATED banner + research sub-navigation. */
export default function ResearchShell({ active, children }: { active: string; children: ReactNode }) {
  const me = usePoll<Me>("/v1/auth/me", 60000);
  return (
    <Shell>
      <div className="sim-banner" role="note" data-testid="sim-banner">
        {RESEARCH_BANNER}
      </div>
      <p className="muted" data-testid="sim-note">
        Results below are simulated from stored historical candles. Fills are estimated, nothing here
        reaches an exchange, and no figure is a realised cash profit.
      </p>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <Link key={t.href} href={t.href} className={t.href === active ? "tab active" : "tab"} role="tab" aria-selected={t.href === active}>
            {t.label}
          </Link>
        ))}
        <span className="sp" />
        {me.data && <span className="muted">role: {me.data.role}</span>}
      </div>
      {children}
    </Shell>
  );
}

export function usePrincipal(): Me | null {
  return usePoll<Me>("/v1/auth/me", 60000).data;
}
