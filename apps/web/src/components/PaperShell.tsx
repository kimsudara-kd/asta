"use client";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { Suspense, type ReactNode } from "react";
import Shell from "../app/Shell";
import { pctChange } from "../lib/overview";
import { PAPER_BANNER, type PaperSession } from "../lib/paper";
import { trimDecimal } from "../lib/format";
import { usePoll } from "../lib/usePoll";
import { StatePill } from "./paper/ui";
import { usePrincipal } from "./ResearchShell";

const TABS = [
  { href: "/paper", label: "Paper Trading Overview" },
  { href: "/paper/portfolio", label: "Paper Portfolio" },
  { href: "/paper/orders", label: "Signals & Paper Orders" },
  { href: "/paper/risk", label: "Risk & Incidents" },
  { href: "/paper/performance", label: "Performance & Reports" },
];

/** Selected session = ?session=<id>, else the newest one. Pages must render inside <Suspense>. */
export function usePaperSession(): { sid: string | null; sessions: PaperSession[]; session: PaperSession | null; select: (id: string) => void } {
  const q = useSearchParams();
  const router = useRouter();
  const list = usePoll<{ sessions: PaperSession[] }>("/v1/paper/sessions", 5000);
  const sessions = list.data?.sessions ?? [];
  const sid = q.get("session") ?? sessions[0]?.id ?? null;
  const session = sessions.find((s) => s.id === sid) ?? null;
  return { sid, sessions, session, select: (id: string) => router.replace(`?session=${id}`) };
}

function Inner({ active, children, sessionHook }: { active: string; children: ReactNode; sessionHook: ReturnType<typeof usePaperSession> }) {
  const me = usePrincipal();
  const { sid, sessions, session, select } = sessionHook;
  const suffix = sid ? `?session=${sid}` : "";
  const chg = session ? pctChange(session.equity, session.starting_equity) : null;
  return (
    <Shell>
      <div className="sim-banner" role="note" data-testid="paper-banner">{PAPER_BANNER}</div>
      <p className="muted" data-testid="paper-note">
        Simulated balances and fills only. Nothing on these pages can reach an exchange, and no figure is a realised cash profit.
      </p>
      <div className="p-nav">
        <div className="tabs" role="tablist">
          {TABS.map((t) => (
            <Link key={t.href} href={`${t.href}${suffix}`} className={t.href === active ? "tab active" : "tab"} role="tab" aria-selected={t.href === active}>
              {t.label}
            </Link>
          ))}
          <span className="sp" />
          {me && <span className="muted">role: {me.role}</span>}
        </div>
      </div>
      {sessions.length > 0 && (
        <div className="p-session card">
          <label className="p-sel">
            <span className="p-sel-k">Session</span>
            <select value={sid ?? ""} onChange={(e) => select(e.target.value)} data-testid="session-select">
              {sessions.map((s) => <option key={s.id} value={s.id}>{s.name} · {s.state}</option>)}
            </select>
          </label>
          {session && (
            <div className="p-facts">
              <span className="p-fact"><span className="p-fact-k">State</span><StatePill state={session.state} /></span>
              <span className="p-fact"><span className="p-fact-k">Equity</span><b>{trimDecimal(session.equity)}</b> <span className="muted">USDT</span></span>
              {chg && <span className="p-fact"><span className="p-fact-k">vs start</span><span className={`chg ${chg.startsWith("-") && /[1-9]/.test(chg) ? "down" : /[1-9]/.test(chg) ? "up" : "flat"}`}>{chg}</span></span>}
              <span className="p-fact"><span className="p-fact-k">Timeframe</span><b>{session.timeframe}</b></span>
              <span className="p-fact"><span className="p-fact-k">Last step</span><span>{session.last_step_ts ? `${session.last_step_ts.slice(0, 16).replace("T", " ")} UTC` : "—"}</span></span>
            </div>
          )}
        </div>
      )}
      {children}
    </Shell>
  );
}

export default function PaperShell({ active, children }: { active: string; children: (hook: ReturnType<typeof usePaperSession>) => ReactNode }) {
  return (
    <Suspense fallback={<p className="muted">Loading…</p>}>
      <Wrapper active={active}>{children}</Wrapper>
    </Suspense>
  );
}

function Wrapper({ active, children }: { active: string; children: (hook: ReturnType<typeof usePaperSession>) => ReactNode }) {
  const hook = usePaperSession();
  return <Inner active={active} sessionHook={hook}>{children(hook)}</Inner>;
}
