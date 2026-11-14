"use client";
import Link from "next/link";
import type { ReactNode } from "react";
import { AreaChart, Sparkline, type Line, type Pt } from "../components/Charts";
import type { Market, Overview, Venue } from "../lib/api";
import { DATA_DISCLAIMER, pctText, stateLabel, stateTone, trimDecimal } from "../lib/format";
import { direction, meterRatio, pctChange, systemStatus, uptimeText, type Tone } from "../lib/overview";
import type { PaperPerf, PaperRisk, PaperSession } from "../lib/paper";
import { usePoll } from "../lib/usePoll";
import Shell from "./Shell";

const pts = (rows: { ts: string; v: string | null }[]): Pt[] =>
  rows.filter((r) => r.v !== null).map((r) => ({ t: Date.parse(r.ts), y: Number(r.v) })); // chart geometry only

function Icon({ name }: { name: "ok" | "warn" | "bad" | "lock" | "key" | "coins" | "bolt" | "up" | "down" | "flat" }) {
  const c = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (name) {
    case "ok": return <svg {...c}><path d="M12 3.5l7 2.8v5.4c0 4.2-2.9 7.3-7 8.8-4.1-1.5-7-4.6-7-8.8V6.3z" /><path d="M9 12l2.2 2.2 3.8-4.2" /></svg>;
    case "warn": return <svg {...c}><path d="M12 4l9 15.5H3z" /><path d="M12 10v4.2M12 17.2v.01" /></svg>;
    case "bad": return <svg {...c}><circle cx="12" cy="12" r="9" /><path d="M9 9l6 6M15 9l-6 6" /></svg>;
    case "lock": return <svg {...c}><rect x="5" y="10.5" width="14" height="9.5" rx="2.2" /><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" /></svg>;
    case "key": return <svg {...c}><circle cx="8.5" cy="12" r="3.5" /><path d="M12 12h8M17 12v3" /></svg>;
    case "coins": return <svg {...c}><ellipse cx="12" cy="7" rx="7" ry="3" /><path d="M5 7v5c0 1.7 3.1 3 7 3s7-1.3 7-3V7M5 12v5c0 1.7 3.1 3 7 3s7-1.3 7-3v-5" /></svg>;
    case "bolt": return <svg {...c}><path d="M13 3L5 13.5h6L10 21l8-10.5h-6z" /></svg>;
    case "up": return <svg {...c}><path d="M7 14l5-5 5 5" /></svg>;
    case "down": return <svg {...c}><path d="M7 10l5 5 5-5" /></svg>;
    case "flat": return <svg {...c}><path d="M6 12h12" /></svg>;
  }
}

function Delta({ text, dir }: { text: string | null; dir: "up" | "down" | "flat" }) {
  if (text === null) return null;
  return (
    <span className={`delta ${dir === "up" ? "up" : dir === "down" ? "down" : "flat"}`}>
      <Icon name={dir} />
      {text}
    </span>
  );
}

function Meter({ ratio, tone }: { ratio: number; tone: Tone }) {
  return (
    <div className="meter" role="presentation">
      <div className={`meter-fill ${tone}`} style={{ width: `${Math.max(2, Math.round(ratio * 100))}%` }} />
    </div>
  );
}

function Skeleton({ h }: { h: number }) {
  return <div className="skeleton" style={{ height: h }} aria-busy="true" />;
}

function stateTonePill(state: string): string {
  if (state === "RUNNING") return "ok";
  if (state === "PAUSED" || state === "RECOVERING") return "warn";
  if (state === "RISK_HALTED" || state === "FAILED") return "bad";
  return "na";
}

function sourceTag(m: Market): { text: string; tone: string } {
  if (m.stale) return { text: "stale", tone: "bad" };
  if (m.source === "LIVE") return { text: "real-time", tone: "ok" };
  if (m.source === "FIXTURE") return { text: "fixture", tone: "warn" };
  return { text: "no data", tone: "bad" };
}

function VenueCard({ v }: { v: Venue }) {
  return (
    <div className="card venue" data-testid="venue-card">
      <div className="card-head">
        <strong>{v.venue}</strong>
        <span className={`pill ${stateTone(v.state)}`}>{stateLabel(v.state)}</span>
      </div>
      <div className="muted venue-line">
        {v.state === "FIXTURE"
          ? "clock: n/a (fixture data)"
          : `clock offset ${v.clock_offset_ms ?? "?"} ms ${v.clock_ok ? "(ok)" : "(NOT ok)"}`}{" "}
        · reconnects {v.reconnects}
        {v.gap_open ? " · data gap open" : ""}
      </div>
      {v.error && <div className="err">{v.error}</div>}
    </div>
  );
}

export default function OverviewPage() {
  const ov = usePoll<Overview>("/v1/overview");
  const mk = usePoll<Market[]>("/v1/markets", 4000);
  const ss = usePoll<{ sessions: PaperSession[] }>("/v1/paper/sessions", 8000);
  const session = ss.data?.sessions?.[0] ?? null;
  const perf = usePoll<PaperPerf>(session ? `/v1/paper/sessions/${session.id}/performance?max_points=240` : null, 15000);
  const prisk = usePoll<PaperRisk>(session ? `/v1/paper/sessions/${session.id}/risk` : null, 10000);
  const o = ov.data;

  return (
    <Shell>
      <div className="page-head">
        <div>
          <h1>Overview</h1>
          <p className="muted">System status, data feeds and the hard execution lock at a glance.</p>
        </div>
        {o && (
          <span className="updated" title="Heartbeat of the API process">
            <span className="dot" aria-hidden="true" />
            Updated {o.heartbeat_utc.slice(11, 19)} UTC
          </span>
        )}
      </div>
      {ov.error && <p className="err">{ov.error}</p>}
      {!o ? (
        <>
          <Skeleton h={150} />
          <div className="grid kpis"><Skeleton h={130} /><Skeleton h={130} /><Skeleton h={130} /><Skeleton h={130} /></div>
          <Skeleton h={300} />
        </>
      ) : (
        <>
          <Hero o={o} />
          <Kpis o={o} session={session} perf={perf.data} prisk={prisk.data} />
          <div className="ov-grid">
            <div className="ov-col">
              <EquityCard session={session} perf={perf.data} />
              <section>
                <h2>Data feeds</h2>
                <div className="grid venues">
                  {o.venues.map((v) => <VenueCard key={v.venue} v={v} />)}
                </div>
              </section>
              <Locks o={o} />
            </div>
            <div className="ov-col">
              <MarketCard markets={mk.data} />
              <SessionCard session={session} prisk={prisk.data} />
            </div>
          </div>
          <p className="muted footnote" data-testid="data-disclaimer">{DATA_DISCLAIMER}</p>
        </>
      )}
    </Shell>
  );
}

function Hero({ o }: { o: Overview }) {
  const s = systemStatus(o);
  const stat = (k: string, v: ReactNode) => (
    <div className="mini-stat"><span className="mini-k">{k}</span><span className="mini-v">{v}</span></div>
  );
  return (
    <section className={`card hero tone-${s.tone}`} aria-label="System status">
      <div className="hero-main">
        <div className="hero-icon"><Icon name={s.tone} /></div>
        <div className="hero-text">
          <div className="hero-kicker">System status</div>
          <p className="hero-title">{s.title}</p>
          <p className="hero-detail">{s.detail}</p>
          <div className="chips">
            <span className="chip"><span className="chip-k">Environment</span><strong>{o.environment}</strong></span>
            <span className="chip"><span className="chip-k">Execution</span><span className={`pill ${o.risk.execution_enabled ? "bad" : "ok"}`}>{o.risk.execution_enabled ? "ENABLED" : "DISABLED"}</span></span>
          </div>
        </div>
      </div>
      <div className="mini-stats">
        {stat("Heartbeat", `${o.heartbeat_utc.slice(11, 19)} UTC`)}
        {stat("Uptime", uptimeText(o.uptime_seconds))}
        {stat("Events accepted", o.events_accepted.toLocaleString("en-US"))}
        {stat("Duplicates dropped", o.duplicates_dropped.toLocaleString("en-US"))}
      </div>
    </section>
  );
}

function Kpis({ o, session, perf, prisk }: { o: Overview; session: PaperSession | null; perf: PaperPerf | null; prisk: PaperRisk | null }) {
  if (!session) {
    return (
      <div className="grid kpis">
        <div className="card kpi">
          <div className="kpi-label">Simulated start equity</div>
          <div className="kpi-value">${o.equity.simulated_start}</div>
          <div className="kpi-sub">P&amp;L {o.equity.pnl} · fee drag {o.equity.fee_drag} (no trades exist)</div>
        </div>
        <div className="card kpi kpi-empty">
          <div className="kpi-label">Paper session</div>
          <div className="kpi-value">None yet</div>
          <div className="kpi-sub">Create one on the Paper trading page to see equity, drawdown and exposure here.</div>
        </div>
      </div>
    );
  }
  const equity = pts((perf?.points ?? []).map((p) => ({ ts: p.ts, v: p.equity })));
  const chg = pctChange(session.equity, session.starting_equity);
  const cur = prisk?.current;
  const pol = prisk?.policy;
  const ddTone: Tone = meterRatio(cur?.drawdown_pct, pol?.max_drawdown_pct) > 0.8 ? "bad" : meterRatio(cur?.drawdown_pct, pol?.max_drawdown_pct) > 0.5 ? "warn" : "ok";
  const exTone: Tone = meterRatio(cur?.invested_exposure_pct, pol?.max_total_pct) > 0.9 ? "warn" : "ok";
  const fills = (perf?.strategies ?? []).reduce((n, s) => n + s.fills, 0);
  return (
    <div className="grid kpis">
      <div className="card kpi">
        <div className="kpi-label">Virtual equity</div>
        <div className="kpi-value">{trimDecimal(session.equity)} <span className="unit">USDT</span></div>
        <div className="kpi-sub"><Delta text={chg} dir={direction(chg?.replace("%", ""))} /> vs {trimDecimal(session.starting_equity)} start</div>
        <Sparkline points={equity} label="Virtual equity trend" />
      </div>
      <div className="card kpi">
        <div className="kpi-label">Net simulated P&amp;L</div>
        <div className={`kpi-value ${perf ? direction(perf.net_pnl) : ""}`}>{perf ? trimDecimal(perf.net_pnl) : "—"} <span className="unit">USDT</span></div>
        <div className="kpi-sub">after estimated fees &amp; slippage · {fills} simulated fills</div>
      </div>
      <div className="card kpi">
        <div className="kpi-label">Drawdown from peak</div>
        <div className="kpi-value">{cur ? pctText(cur.drawdown_pct) : "—"}</div>
        <Meter ratio={meterRatio(cur?.drawdown_pct, pol?.max_drawdown_pct)} tone={ddTone} />
        <div className="kpi-sub">limit {pol?.max_drawdown_pct ? pctText(pol.max_drawdown_pct) : "—"}; beyond it new buys halt</div>
      </div>
      <div className="card kpi">
        <div className="kpi-label">Invested exposure</div>
        <div className="kpi-value">{cur ? pctText(cur.invested_exposure_pct) : "—"}</div>
        <Meter ratio={meterRatio(cur?.invested_exposure_pct, pol?.max_total_pct)} tone={exTone} />
        <div className="kpi-sub">{cur ? `${trimDecimal(cur.invested_exposure)} USDT` : "—"} of a {pol?.max_total_pct ? pctText(pol.max_total_pct) : "—"} limit</div>
      </div>
    </div>
  );
}

function EquityCard({ session, perf }: { session: PaperSession | null; perf: PaperPerf | null }) {
  if (!session) {
    return (
      <section className="card">
        <h2 className="card-title">Virtual equity vs passive benchmarks</h2>
        <p className="muted">No paper session yet. Once one runs, its equity curve is compared with simple buy-and-hold references here.</p>
      </section>
    );
  }
  const p = perf?.points ?? [];
  const candidates: Line[] = [
    { name: "paper portfolio", tone: "accent", area: true, points: pts(p.map((x) => ({ ts: x.ts, v: x.equity }))) },
    { name: "reference-policy-constrained hold", tone: "ok", dashed: true, points: pts(p.map((x) => ({ ts: x.ts, v: x.constrained_hold }))) },
    { name: "unconstrained passive hold", tone: "warn", dashed: true, points: pts(p.map((x) => ({ ts: x.ts, v: x.unconstrained_hold }))) },
    { name: "all cash", tone: "muted", points: pts(p.map((x) => ({ ts: x.ts, v: x.all_cash }))) },
  ];
  const lines = candidates.filter((l) => l.points.length > 1);
  return (
    <section className="card chart-card" data-testid="overview-equity">
      <div className="card-head">
        <div>
          <h2 className="card-title">Virtual equity vs passive benchmarks</h2>
          <div className="muted sub">
            {session.name} · <span className={`pill ${stateTonePill(session.state)}`}>{session.state}</span>
          </div>
        </div>
        <Link href={`/paper?session=${session.id}`} className="link-more">Open the simulation →</Link>
      </div>
      <AreaChart lines={lines} baseline={Number(session.starting_equity)} yFormat={(v) => v.toFixed(0)} label="Virtual equity compared with passive benchmarks" />
      <div className="legend">
        {lines.map((l) => (
          <span key={l.name} className="legend-item"><i className={`swatch ${l.tone}${l.dashed ? " dashed" : ""}`} />{l.name}</span>
        ))}
      </div>
      {perf?.note && <p className="muted footnote">{perf.note}</p>}
    </section>
  );
}

function MarketCard({ markets }: { markets: Market[] | null }) {
  const rows = markets ?? [];
  return (
    <section className="card">
      <div className="card-head">
        <h2 className="card-title">Market snapshot</h2>
        <Link href="/markets" className="link-more" aria-label="See all quotes">See all quotes →</Link>
      </div>
      {rows.length === 0 ? (
        <Skeleton h={120} />
      ) : (
        <ul className="mkt">
          {rows.map((m) => {
            const t = sourceTag(m);
            return (
              <li key={`${m.venue}-${m.symbol}`} className="mkt-row">
                <div className="mkt-id">
                  <span className="mkt-sym">{m.symbol.replace("-", "/")}</span>
                  <span className="muted mkt-venue">{m.venue}</span>
                </div>
                <div className="mkt-px">
                  <span className="mkt-bid">{m.bid ?? "—"}</span>
                  <span className="muted mkt-spread">spread {m.spread_bps ?? "—"} bps</span>
                </div>
                <span className={`pill ${t.tone}`}>{t.text}</span>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function SessionCard({ session, prisk }: { session: PaperSession | null; prisk: PaperRisk | null }) {
  if (!session) return null;
  const rej = Object.entries(prisk?.rejections_by_reason ?? {}).sort((a, b) => b[1] - a[1]);
  const total = rej.reduce((n, [, c]) => n + c, 0);
  const reason = (r: string) => r.replace(/^RISK_/, "").replaceAll("_", " ").toLowerCase();
  return (
    <section className="card">
      <h2 className="card-title">Paper session</h2>
      <dl className="facts">
        <div><dt>Session</dt><dd>{session.name}</dd></div>
        <div><dt>State</dt><dd><span className={`pill ${stateTonePill(session.state)}`}>{session.state}</span></dd></div>
        <div><dt>Last step</dt><dd>{session.last_step_ts ? `${session.last_step_ts.slice(0, 16).replace("T", " ")} UTC` : "—"}</dd></div>
        <div><dt>Timeframe</dt><dd>{session.timeframe}</dd></div>
        <div><dt>Reconciliation</dt><dd>{session.last_reconcile_ok === null ? "not run yet" : session.last_reconcile_ok ? "exact" : "MISMATCH"}</dd></div>
        <div><dt>Strategies</dt><dd>{session.strategies.length} · none is a deployment candidate</dd></div>
      </dl>
      {total > 0 && (
        <div className="insight">
          <strong>{total.toLocaleString("en-US")}</strong> simulated signals were refused by safeguards
          <span className="muted"> ({rej.slice(0, 2).map(([r, c]) => `${c} ${reason(r)}`).join(", ")})</span>. Refusals are normal: the limits are doing their job.
        </div>
      )}
    </section>
  );
}

function Locks({ o }: { o: Overview }) {
  const item = (icon: "lock" | "bolt" | "key" | "coins", title: string, pill: ReactNode, sub: string) => (
    <li className="lock-row">
      <span className="lock-icon"><Icon name={icon} /></span>
      <div className="lock-text"><span className="lock-title">{title}</span><span className="muted lock-sub">{sub}</span></div>
      {pill}
    </li>
  );
  return (
    <section className="card">
      <h2 className="card-title">Safety locks</h2>
      <ul className="locks">
        {item("bolt", "Order execution", <span className={`pill ${o.risk.execution_enabled ? "bad" : "ok"}`}>{o.risk.execution_enabled ? "ENABLED" : "DISABLED"}</span>, `Gateway probe: ${o.risk.gateway_probe.decision}`)}
        {item("lock", "Live trading", <span className={`pill ${o.risk.live_trading_enabled ? "bad" : "ok"}`}>{o.risk.live_trading_enabled ? "ENABLED" : "DISABLED"}</span>, "No such environment exists in this build")}
        {item("key", "Exchange credentials", <span className="pill ok">NONE</span>, "None stored; the app refuses to start if one is set")}
        {item("coins", "Funds", <span className="pill ok">VIRTUAL</span>, "Every balance is simulated")}
      </ul>
    </section>
  );
}
