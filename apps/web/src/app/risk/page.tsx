"use client";
import type { ReactNode } from "react";
import Gauge from "../../components/Gauge";
import { type Risk } from "../../lib/api";
import { pctText } from "../../lib/format";
import { meterRatio } from "../../lib/overview";
import type { PaperPortfolio, PaperRisk, PaperSession } from "../../lib/paper";
import { assetExposure, gaugeTone, lossRatio, policyRows, reasonText, rejectionRows, riskVerdict, type Tone } from "../../lib/risk";
import { usePoll } from "../../lib/usePoll";
import Shell from "../Shell";

type Incident = { severity: string; kind: string; detail: string; opened_at: string };

function Icon({ name }: { name: "shield" | "gate" | "lock" | "ok" | "warn" | "bad" }) {
  const c = { viewBox: "0 0 24 24", fill: "none", stroke: "currentColor", strokeWidth: 2, strokeLinecap: "round" as const, strokeLinejoin: "round" as const, "aria-hidden": true };
  switch (name) {
    case "shield": return <svg {...c}><path d="M12 3.5l7 2.8v5.4c0 4.2-2.9 7.3-7 8.8-4.1-1.5-7-4.6-7-8.8V6.3z" /><path d="M9 12l2.2 2.2 3.8-4.2" /></svg>;
    case "gate": return <svg {...c}><path d="M4 20V6l8-3 8 3v14" /><path d="M9 20v-6h6v6M4 20h16" /></svg>;
    case "lock": return <svg {...c}><rect x="5" y="10.5" width="14" height="9.5" rx="2.2" /><path d="M8.5 10.5V8a3.5 3.5 0 0 1 7 0v2.5" /></svg>;
    case "ok": return <svg {...c}><path d="M12 3.5l7 2.8v5.4c0 4.2-2.9 7.3-7 8.8-4.1-1.5-7-4.6-7-8.8V6.3z" /><path d="M9 12l2.2 2.2 3.8-4.2" /></svg>;
    case "warn": return <svg {...c}><path d="M12 4l9 15.5H3z" /><path d="M12 10v4.2M12 17.2v.01" /></svg>;
    case "bad": return <svg {...c}><circle cx="12" cy="12" r="9" /><path d="M9 9l6 6M15 9l-6 6" /></svg>;
  }
}

function Skeleton({ h }: { h: number }) {
  return <div className="skeleton" style={{ height: h }} aria-busy="true" />;
}

function stateTonePill(state: string): string {
  return state === "RUNNING" ? "ok" : state === "PAUSED" || state === "RECOVERING" ? "warn" : state === "RISK_HALTED" || state === "FAILED" ? "bad" : "na";
}

export default function RiskPage() {
  const { data, error } = usePoll<Risk>("/v1/risk");
  const inc = usePoll<Incident[]>("/v1/incidents", 15000);
  const ss = usePoll<{ sessions: PaperSession[] }>("/v1/paper/sessions", 10000);
  const session = ss.data?.sessions?.[0] ?? null;
  const prisk = usePoll<PaperRisk>(session ? `/v1/paper/sessions/${session.id}/risk` : null, 8000);
  const port = usePoll<PaperPortfolio>(session ? `/v1/paper/sessions/${session.id}/portfolio` : null, 15000);

  const pr = prisk.data;
  const pol = pr?.policy;
  const cur = pr?.current;
  const assets = pol && port.data ? assetExposure(port.data.exposure_by_asset, String(pol.starting_equity ?? "0"), String(pol.max_asset_pct ?? "0")) : [];
  const top = assets[0] ?? null;
  const ratios = pr && pol && cur
    ? [
        { label: "Drawdown", ratio: meterRatio(cur.drawdown_pct, pol.max_drawdown_pct) },
        { label: "Daily loss", ratio: lossRatio(cur.daily_pnl_pct, pol.daily_loss_pct) },
        { label: "Total exposure", ratio: meterRatio(cur.invested_exposure_pct, pol.max_total_pct) },
        ...(top ? [{ label: `${top.asset} exposure`, ratio: top.ratio }] : []),
      ]
    : [];
  const verdict = data ? riskVerdict({ haltNewBuys: data.halt_new_buys, haltReasons: data.halt_reasons, ratios }) : null;
  const refusals = pr ? rejectionRows(pr.rejections_by_reason) : [];
  const refusalTotal = refusals.reduce((n, r) => n + r.count, 0);
  const sysIncidents = inc.data ?? [];
  const openIncidents = (pr?.open_incidents ?? []).length + sysIncidents.length;
  const trips = (pr?.events ?? []).filter((e) => e.kind !== "ORDER_REJECTED").slice(-8).reverse();
  const health = Object.entries(pr?.market_health ?? {});

  return (
    <Shell>
      <div className="page-head">
        <div>
          <h1>Risk &amp; controls</h1>
          <p className="muted">The limits, switches and layers that keep this system simulation-only, and how close each one is to being used.</p>
        </div>
        {data && <span className="updated"><span className="dot" aria-hidden="true" />Checked every 5 s</span>}
      </div>
      {error && <p className="err">{error}</p>}
      {!data || !verdict ? (
        <>
          <Skeleton h={150} />
          <div className="grid rk-ctl"><Skeleton h={150} /><Skeleton h={150} /><Skeleton h={150} /></div>
          <Skeleton h={220} />
        </>
      ) : (
        <>
          <section className={`card hero tone-${verdict.tone}`} aria-label="Risk status">
            <div className="hero-main">
              <div className="hero-icon"><Icon name={verdict.tone} /></div>
              <div className="hero-text">
                <div className="hero-kicker">Risk status</div>
                <p className="hero-title">{verdict.title}</p>
                <p className="hero-detail">{verdict.detail}</p>
              </div>
            </div>
            <div className="mini-stats">
              <div className="mini-stat"><span className="mini-k">Policy</span><span className="mini-v">{pol ? String(pol.version ?? "—") : "default"}</span></div>
              <div className="mini-stat"><span className="mini-k">Open incidents</span><span className="mini-v">{openIncidents}</span></div>
              <div className="mini-stat"><span className="mini-k">Refusals</span><span className="mini-v">{refusalTotal.toLocaleString("en-US")}</span></div>
              <div className="mini-stat"><span className="mini-k">Session</span><span className="mini-v rk-sess">{session ? <span className={`pill ${stateTonePill(session.state)}`}>{session.state}</span> : "none"}</span></div>
            </div>
          </section>

          <div className="grid rk-ctl">
            <div className="card ctl" data-testid="risk-newbuys">
              <div className="ctl-head"><span className={`ctl-icon ${data.halt_new_buys ? "bad" : "ok"}`}><Icon name="shield" /></span><span className="kpi-label">New buys</span></div>
              <div className="ctl-value">{data.halt_new_buys ? "HALTED" : "No policy breach (execution still disabled)"}</div>
              <div className="ctl-sub">
                {data.halt_new_buys
                  ? data.halt_reasons.map((r) => <span key={r} className="reason"><code>{r}</code> {reasonText(r)}</span>)
                  : "no breach"}
              </div>
            </div>
            <div className="card ctl" data-testid="risk-gateway">
              <div className="ctl-head"><span className="ctl-icon ok"><Icon name="gate" /></span><span className="kpi-label">Execution gateway</span></div>
              <div className="ctl-value">{data.gateway_probe.decision}</div>
              <div className="ctl-sub">
                {data.gateway_probe.reasons.map((r) => <span key={r} className="reason"><code>{r}</code> {reasonText(r)}</span>)}
              </div>
            </div>
            <div className="card ctl" data-testid="risk-live">
              <div className="ctl-head"><span className={`ctl-icon ${data.live_trading_enabled ? "bad" : "ok"}`}><Icon name="lock" /></span><span className="kpi-label">Live trading</span></div>
              <div className="ctl-value">{data.live_trading_enabled ? "ENABLED" : "DISABLED (hard-coded)"}</div>
              <div className="ctl-sub">No such environment exists in this build.</div>
            </div>
          </div>

          <h2>Limits in use</h2>
          {pr && pol && cur ? (
            <section className="card gauges-card">
              <div className="gauges">
                <Gauge ratio={meterRatio(cur.drawdown_pct, pol.max_drawdown_pct)} tone={gaugeTone(meterRatio(cur.drawdown_pct, pol.max_drawdown_pct))} value={pctText(cur.drawdown_pct)} caption="Drawdown from peak" limit={pol.max_drawdown_pct ? pctText(pol.max_drawdown_pct) : "—"} />
                <Gauge ratio={lossRatio(cur.daily_pnl_pct, pol.daily_loss_pct)} tone={gaugeTone(lossRatio(cur.daily_pnl_pct, pol.daily_loss_pct))} value={pctText(cur.daily_pnl_pct)} caption="Result today" limit={pol.daily_loss_pct ? `${pctText(pol.daily_loss_pct)} loss` : "—"} />
                <Gauge ratio={meterRatio(cur.invested_exposure_pct, pol.max_total_pct)} tone={gaugeTone(meterRatio(cur.invested_exposure_pct, pol.max_total_pct))} value={pctText(cur.invested_exposure_pct)} caption="Total exposure" limit={pol.max_total_pct ? pctText(pol.max_total_pct) : "—"} />
                {top && <Gauge ratio={top.ratio} tone={gaugeTone(top.ratio)} value={`${top.pct}%`} caption={`${top.asset} of its cap`} limit={`${top.cap} USDT`} />}
              </div>
              <p className="muted footnote">Measured on the latest paper session{session ? ` (${session.name})` : ""}. A full arc means the limit is reached; entries that would cross it are refused.</p>
            </section>
          ) : (
            <section className="card"><p className="muted">No paper session yet. Once one runs, drawdown, daily loss and exposure are measured against their limits here.</p></section>
          )}

          {pr && (
            <div className="rk-two">
              <section className="card">
                <h2 className="card-title">Exposure by asset vs the single-asset cap</h2>
                {assets.length === 0 ? <p className="muted">No open exposure.</p> : (
                  <ul className="xbars">
                    {assets.map((a) => (
                      <li key={a.asset} className="xbar">
                        <div className="xbar-top"><span className="xbar-name">{a.asset}</span><span className="muted">{a.value} of {a.cap} USDT · {a.pct}% of cap</span></div>
                        <div className="xbar-track"><div className={`xbar-fill ${gaugeTone(a.ratio)}`} style={{ width: `${Math.max(2, Math.round(a.ratio * 100))}%` }} /><i className="xbar-cap" aria-hidden="true" /></div>
                      </li>
                    ))}
                  </ul>
                )}
                {top && top.ratio >= 0.85 && <p className="insight">{top.asset} is at {top.pct}% of its cap, so new {top.asset} entries are refused until exposure falls.</p>}
              </section>
              <section className="card">
                <h2 className="card-title">Why simulated entries were refused</h2>
                {refusals.length === 0 ? <p className="muted">No refusals recorded.</p> : (
                  <ul className="xbars">
                    {refusals.map((r) => (
                      <li key={r.code} className="xbar">
                        <div className="xbar-top"><span className="xbar-name">{r.label}</span><span className="muted">{r.count.toLocaleString("en-US")} · {Math.round(r.share * 100)}%</span></div>
                        <div className="xbar-track"><div className="xbar-fill accent" style={{ width: `${Math.max(2, Math.round(r.share * 100))}%` }} /></div>
                      </li>
                    ))}
                  </ul>
                )}
                <p className="muted footnote">Refusals are normal: they show a limit doing its job. A strategy that repeats the same refused signal on every bar inflates this count.</p>
              </section>
            </div>
          )}

          <div className="rk-two">
            <section className="card">
              <h2 className="card-title">Halts, incidents and data health</h2>
              <div className="flags">
                <span className="flag"><span className="muted">Daily loss trip</span><span className={`pill ${pr?.halts.daily_loss_tripped ? "bad" : "ok"}`}>{pr?.halts.daily_loss_tripped ? "TRIPPED" : "armed"}</span></span>
                <span className="flag"><span className="muted">Drawdown halt</span><span className={`pill ${pr?.halts.drawdown_halted ? "bad" : "ok"}`}>{pr?.halts.drawdown_halted ? "HALTED" : "armed"}</span></span>
                <span className="flag"><span className="muted">Re-arm</span><span className="pill na">{pr?.daily_auto_reset ? "automatic" : "manual only"}</span></span>
              </div>
              {health.length > 0 && (
                <div className="health">
                  {health.map(([k, v]) => <span key={k} className={`hchip ${v === "OK" ? "ok" : "bad"}`}><i />{k.replace(":", " · ").replace("-", "/")}</span>)}
                </div>
              )}
              <h3 className="sub-h">Open incidents</h3>
              {openIncidents === 0 ? <p className="muted empty-ok">No open incidents.</p> : (
                <ul className="incidents">
                  {[...(pr?.open_incidents ?? []).map((i) => ({ severity: i.severity, kind: i.kind, detail: i.detail, opened_at: i.opened_at })), ...sysIncidents].map((i, n) => (
                    <li key={n}><span className={`pill ${i.severity === "CRITICAL" || i.severity === "HIGH" ? "bad" : "warn"}`}>{i.severity}</span><span className="inc-kind">{i.kind}</span><span className="muted">{i.detail}</span></li>
                  ))}
                </ul>
              )}
              <h3 className="sub-h">Recent halts and trips</h3>
              {trips.length === 0 ? <p className="muted empty-ok">No halts or trips recorded in this session.</p> : (
                <ol className="timeline">
                  {trips.map((e) => (
                    <li key={e.seq}><span className="tl-dot" /><span className="tl-t">{e.ts.slice(0, 16).replace("T", " ")}</span><b>{reasonText(e.kind)}</b><span className="muted">{e.detail}</span></li>
                  ))}
                </ol>
              )}
            </section>
            <section className="card">
              <h2 className="card-title">Control layers</h2>
              <ol className="layers">
                <Layer tone={data.live_trading_enabled ? "bad" : "ok"} n={1} title="Environment lock" text="There is no live environment in this build, so nothing can be pointed at real funds." />
                <Layer tone={data.gateway_probe.decision === "DENY" ? "ok" : "bad"} n={2} title="Execution gateway" text="Default-deny: every order request is refused before it can leave." />
                <Layer tone={verdict.tone} n={3} title="Risk engine" text="Limits are checked on every simulated entry, with exact decimal arithmetic." />
                <Layer tone={data.halt_new_buys ? "bad" : "ok"} n={4} title="Halt switch" text="Any breach suspends new buys. Re-arming is a deliberate manual step." />
              </ol>
            </section>
          </div>

          <h2>Policy</h2>
          <div className="card scroll">
            <table className="qtable">
              <thead><tr><th>Setting</th><th>Meaning</th><th className="num">Value</th></tr></thead>
              <tbody>
                {policyRows(data.policy).map((p) => (
                  <tr key={p.key}>
                    <td data-label="Setting"><code>{p.key}</code></td>
                    <td data-label="Meaning" className="muted">{p.description || "—"}</td>
                    <td data-label="Value" className="num">{p.shown}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </Shell>
  );
}

function Layer({ tone, n, title, text }: { tone: Tone; n: number; title: string; text: ReactNode }) {
  return (
    <li className="layer">
      <span className={`layer-n ${tone}`}>{n}</span>
      <div><div className="layer-title">{title}</div><div className="muted layer-text">{text}</div></div>
      <span className={`pill ${tone}`}>{tone === "ok" ? "in place" : tone === "warn" ? "watch" : "attention"}</span>
    </li>
  );
}
