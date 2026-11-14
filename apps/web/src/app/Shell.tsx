"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";
import { api, type Me, type Risk } from "../lib/api";
import { canSee } from "../lib/format";
import { usePoll } from "../lib/usePoll";

type IconName = "overview" | "markets" | "risk" | "research" | "paper" | "audit";

/** Small stroke icons (no dependency). Decorative: the link text carries the accessible name. */
function Icon({ name }: { name: IconName }) {
  const common = {
    viewBox: "0 0 24 24",
    fill: "none",
    stroke: "currentColor",
    strokeWidth: 1.8,
    strokeLinecap: "round" as const,
    strokeLinejoin: "round" as const,
    "aria-hidden": true,
  };
  switch (name) {
    case "overview":
      return (
        <svg {...common}>
          <rect x="3.5" y="3.5" width="7" height="7" rx="1.6" />
          <rect x="13.5" y="3.5" width="7" height="7" rx="1.6" />
          <rect x="3.5" y="13.5" width="7" height="7" rx="1.6" />
          <rect x="13.5" y="13.5" width="7" height="7" rx="1.6" />
        </svg>
      );
    case "markets":
      return (
        <svg {...common}>
          <path d="M4 19V5" />
          <path d="M4 19h16" />
          <path d="M8 15l3.5-4 3 2.5L19 8" />
        </svg>
      );
    case "risk":
      return (
        <svg {...common}>
          <path d="M12 3.5l7 2.8v5.4c0 4.2-2.9 7.3-7 8.8-4.1-1.5-7-4.6-7-8.8V6.3l7-2.8z" />
          <path d="M9.2 12l2.1 2.1 3.6-4" />
        </svg>
      );
    case "research":
      return (
        <svg {...common}>
          <path d="M9.5 3.5h5" />
          <path d="M10.5 3.5v5.2L5.6 17a2.2 2.2 0 0 0 1.9 3.3h9a2.2 2.2 0 0 0 1.9-3.3l-4.9-8.3V3.5" />
          <path d="M8 14.5h8" />
        </svg>
      );
    case "paper":
      return (
        <svg {...common}>
          <path d="M6 3.5h8l4 4v13H6z" />
          <path d="M14 3.5v4h4" />
          <path d="M9 15l2-2.2 2 1.4 2.2-3" />
        </svg>
      );
    case "audit":
      return (
        <svg {...common}>
          <rect x="5" y="4.5" width="14" height="16" rx="2" />
          <path d="M9 4.5v-1h6v1" />
          <path d="M8.5 11h7M8.5 14.5h7M8.5 8h3" />
        </svg>
      );
  }
}

const NAV: { href: string; label: string; icon: IconName; group: string; match: (p: string) => boolean }[] = [
  { href: "/", label: "Overview", icon: "overview", group: "Monitor", match: (p) => p === "/" },
  { href: "/markets", label: "Markets", icon: "markets", group: "Monitor", match: (p) => p.startsWith("/markets") },
  { href: "/risk", label: "Risk", icon: "risk", group: "Monitor", match: (p) => p === "/risk" },
  { href: "/research/datasets", label: "Research", icon: "research", group: "Simulate", match: (p) => p.startsWith("/research") },
  { href: "/paper", label: "Paper trading", icon: "paper", group: "Simulate", match: (p) => p.startsWith("/paper") },
];

export default function Shell({ children }: { children: ReactNode }) {
  const me = usePoll<Me>("/v1/auth/me", 60000);
  const risk = usePoll<Risk>("/v1/risk", 5000);
  const pathname = usePathname() ?? "/";
  const halted = risk.data?.halt_new_buys ?? false;
  const showAudit = !!me.data && canSee(me.data.role, "audit");

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

  const groups = ["Monitor", "Simulate"];
  return (
    <div className="app">
      <aside className="sidebar">
        <Link href="/" className="brand" aria-label="ASTA home">
          <span className="brand-mark" aria-hidden="true">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 18l5.5-7 4 4L20 6" />
            </svg>
          </span>
          <span className="brand-name">
            ASTA<span className="brand-sub">Spot research platform</span>
          </span>
        </Link>
        <nav className="nav" aria-label="Main">
          {groups.map((g, gi) => (
            <div key={g} style={{ display: "contents" }}>
              <div className={gi === 0 ? "nav-label first" : "nav-label"} aria-hidden="true">
                {g}
              </div>
              {NAV.filter((n) => n.group === g).map((n) => (
                <Link key={n.href} href={n.href} aria-current={n.match(pathname) ? "page" : undefined}>
                  <Icon name={n.icon} />
                  {n.label}
                </Link>
              ))}
            </div>
          ))}
          {showAudit && (
            <>
              <div className="nav-label" aria-hidden="true">
                Governance
              </div>
              <Link href="/audit" aria-current={pathname.startsWith("/audit") ? "page" : undefined}>
                <Icon name="audit" />
                Audit
              </Link>
            </>
          )}
        </nav>
        <div className="sidebar-foot">
          {me.data && (
            <div className="user-chip">
              <span className="avatar" aria-hidden="true">
                {me.data.username.slice(0, 2)}
              </span>
              <span className="user-meta">
                <span className="user-name">{me.data.username}</span>
                <span className="user-role">{me.data.role}</span>
              </span>
            </div>
          )}
          <button onClick={logout}>Sign out</button>
        </div>
      </aside>
      <div className="content-wrap">
        <div className={halted ? "banner halt" : "banner"} role="status" data-testid="banner">
          {halted ? `RISK HALT — new buys suspended (${risk.data?.halt_reasons.join(", ")}). ` : ""}
          PAPER · PUBLIC MARKET DATA ONLY · NO TRADING · NO REAL FUNDS
        </div>
        <main>{children}</main>
      </div>
    </div>
  );
}
