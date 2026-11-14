"use client";
import { useState } from "react";
import Gauge from "../../../components/Gauge";
import PaperShell from "../../../components/PaperShell";
import { Skeleton } from "../../../components/paper/ui";
import { usePrincipal } from "../../../components/ResearchShell";
import { pctText } from "../../../lib/format";
import { meterRatio } from "../../../lib/overview";
import type { Alert, FeedsStatus, PaperPortfolio, PaperRisk } from "../../../lib/paper";
import { canWriteResearch, explain, post } from "../../../lib/research";
import { assetExposure, gaugeTone, lossRatio, reasonText, rejectionRows } from "../../../lib/risk";
import { usePoll } from "../../../lib/usePoll";

type Incident = { id: string; kind: string; severity: string; detail: string; opened_at: string; resolved_at: string | null };

const sevTone = (s: string) => (["CRITICAL", "HIGH", "ERROR"].includes(s.toUpperCase()) ? "bad" : ["MEDIUM", "WARNING", "WARN"].includes(s.toUpperCase()) ? "warn" : "na");

function Body({ sid }: { sid: string | null }) {
  const me = usePrincipal();
  const risk = usePoll<PaperRisk>(sid ? `/v1/paper/sessions/${sid}/risk` : "/v1/paper/health", 5000);
  const inc = usePoll<{ incidents: Incident[]; alerts: Alert[] }>(sid ? `/v1/paper/sessions/${sid}/incidents` : "/v1/paper/health", 5000);
  const port = usePoll<PaperPortfolio>(sid ? `/v1/paper/sessions/${sid}/portfolio` : null, 15000);
  const feeds = usePoll<FeedsStatus>("/v1/market-feeds/status", 5000);
  const [msg, setMsg] = useState<string | null>(null);
  const r = sid ? (risk.data as PaperRisk | null) : null;
  if (!r) return sid ? <><h1>Risk &amp; Incidents</h1><div className="grid kpis"><Skeleton h={150} /><Skeleton h={150} /><Skeleton h={150} /></div></> : <p className="muted">No risk data yet.</p>;
  const incidents = (inc.data as { incidents: Incident[] } | null)?.incidents ?? [];
  const alerts = (inc.data as { alerts: Alert[] } | null)?.alerts ?? [];
  const p = r.policy;
  const cur = r.current;
  const assets = port.data ? assetExposure(port.data.exposure_by_asset, String(p.starting_equity ?? "0"), String(p.max_asset_pct ?? "0")) : [];
  const top = assets[0] ?? null;
  const refusals = rejectionRows(r.rejections_by_reason);
  const halted = r.halts.drawdown_halted || r.halts.daily_loss_tripped;
  const dd = meterRatio(cur.drawdown_pct, p.max_drawdown_pct);
  const dl = lossRatio(cur.daily_pnl_pct, p.daily_loss_pct);
  const ex = meterRatio(cur.invested_exposure_pct, p.max_total_pct);
  return (
    <>
      <h1>Risk &amp; Incidents</h1>

      <section className="card gauges-card">
        <div className="gauges">
          <Gauge ratio={dd} tone={gaugeTone(dd)} value={pctText(cur.drawdown_pct)} caption="Drawdown from peak" limit={p.max_drawdown_pct ? pctText(p.max_drawdown_pct) : "—"} />
          <Gauge ratio={dl} tone={gaugeTone(dl)} value={pctText(cur.daily_pnl_pct)} caption="Result today" limit={p.daily_loss_pct ? `${pctText(p.daily_loss_pct)} loss` : "—"} />
          <Gauge ratio={ex} tone={gaugeTone(ex)} value={pctText(cur.invested_exposure_pct)} caption="Total exposure" limit={p.max_total_pct ? pctText(p.max_total_pct) : "—"} />
          {top && <Gauge ratio={top.ratio} tone={gaugeTone(top.ratio)} value={`${top.pct}%`} caption={`${top.asset} of its cap`} limit={`${top.cap} USDT`} />}
        </div>
        <p className="muted footnote">A full arc means the limit is reached; entries that would cross it are refused.</p>
      </section>

      <div className="grid p-risk">
        <section className="card" data-testid="limits-card">
          <h2 className="card-title">Exposure limits (global, across venues and strategies)</h2>
          <div className="limit-tiles">
            <div className="ltile"><span className="muted">Per asset</span><b>{pctText(p.max_asset_pct ?? "0")}</b></div>
            <div className="ltile"><span className="muted">Total invested</span><b>{pctText(p.max_total_pct ?? "0")}</b></div>
            <div className="ltile"><span className="muted">Daily loss halt</span><b>{pctText(p.daily_loss_pct ?? "0")}</b></div>
            <div className="ltile"><span className="muted">Drawdown halt</span><b>{pctText(p.max_drawdown_pct ?? "0")}</b></div>
          </div>
          <p>Per asset ≤ {pctText(p.max_asset_pct ?? "0")} · total invested ≤ {pctText(p.max_total_pct ?? "0")} of starting equity {p.starting_equity} USDT</p>
          <p>Daily-loss halt at {pctText(p.daily_loss_pct ?? "0")} · drawdown halt at {pctText(p.max_drawdown_pct ?? "0")} of peak · no leverage, no short sales</p>
          <p className="muted">A daily halt never resets at midnight by itself ({r.daily_auto_reset ? "auto-reset ON" : "operator re-arm required, later UTC day only"}); a drawdown halt is latched for the session.</p>
        </section>

        <section className="card" data-testid="current-card">
          <h2 className="card-title">Current</h2>
          <p data-testid="halts">{r.halts.drawdown_halted ? <span className="pill bad big">drawdown halt (latched)</span> : r.halts.daily_loss_tripped ? <span className="pill bad big">daily-loss halt</span> : <span className="pill ok big">no active halt</span>}</p>
          <p>Drawdown {pctText(cur.drawdown_pct)} · today {pctText(cur.daily_pnl_pct)} · exposure {pctText(cur.invested_exposure_pct)}</p>
          {halted && <p className="warnbox">New simulated buys are suspended until an operator re-arms the session.</p>}
          <p className="muted">Rejected signals by reason: {Object.entries(r.rejections_by_reason).map(([k, v]) => `${k || "—"}: ${v}`).join(" · ") || "none"}</p>
          {refusals.length > 0 && (
            <ul className="xbars">
              {refusals.map((x) => (
                <li key={x.code} className="xbar">
                  <div className="xbar-top"><span className="xbar-name">{x.label}</span><span className="muted">{x.count.toLocaleString("en-US")} · {Math.round(x.share * 100)}%</span></div>
                  <div className="xbar-track"><div className="xbar-fill accent" style={{ width: `${Math.max(2, Math.round(x.share * 100))}%` }} /></div>
                </li>
              ))}
            </ul>
          )}
        </section>

        <section className="card" data-testid="health-card">
          <h2 className="card-title">Data health</h2>
          <div className="health">
            {Object.entries(r.market_health).map(([k, v]) => <span key={k} className={`hchip ${v === "OK" ? "ok" : "bad"}`}><i />{k}: {v}</span>)}
          </div>
          {(feeds.data?.gaps ?? []).length > 0 && <h3 className="sub-h">Recent data gaps</h3>}
          <ul className="gaps">
            {(feeds.data?.gaps ?? []).slice(0, 5).map((g, i) => (
              <li key={i}><span className="gap-id">{g.venue} {g.symbol}</span><span className="muted">{g.start.slice(0, 16).replace("T", " ")} · {g.missing} min</span><span className={`pill ${g.status === "FILLED" ? "ok" : "warn"}`}>{g.status}</span></li>
            ))}
          </ul>
        </section>
      </div>

      <section className="card">
        <h2 className="card-title">Incidents</h2>
        {msg && <p role="status">{msg}</p>}
        <div className="scroll tall"><table data-testid="incidents-table">
          <thead><tr><th>Opened</th><th>Kind</th><th>Severity</th><th>Detail</th><th>Status</th></tr></thead>
          <tbody>{incidents.map((i) => <tr key={i.id}><td>{i.opened_at.slice(0, 16).replace("T", " ")}</td><td><span className="reason-chip">{i.kind}</span></td><td><span className={`pill ${sevTone(i.severity)}`}>{i.severity}</span></td><td className="wrap">{i.detail.slice(0, 140)}</td><td>{i.resolved_at ? <span className="pill ok">resolved</span> : <span className="pill bad">OPEN</span>}</td></tr>)}
            {incidents.length === 0 && <tr><td colSpan={5} className="muted">No incidents recorded.</td></tr>}</tbody>
        </table></div>
      </section>

      <section className="card">
        <h2 className="card-title">In-app alerts</h2>
        <div className="scroll tall"><table data-testid="alerts-table">
          <thead><tr><th>When</th><th>Kind</th><th>Severity</th><th>Message</th><th /></tr></thead>
          <tbody>{alerts.map((a) => <tr key={a.id}><td>{a.created_at.slice(0, 16).replace("T", " ")}</td><td><span className="reason-chip">{a.kind}</span></td><td><span className={`pill ${sevTone(a.severity)}`}>{a.severity}</span></td><td className="wrap">{a.message.slice(0, 120)}</td>
            <td>{a.acknowledged_at ? <span className="muted">acknowledged</span> : canWriteResearch(me?.role) ? <button className="ghost" onClick={async () => { try { await post(`/v1/paper/alerts/${a.id}/ack`, {}, me!.csrf_token); window.location.reload(); } catch (e) { setMsg(await explain(e)); } }}>Acknowledge</button> : "—"}</td></tr>)}
            {alerts.length === 0 && <tr><td colSpan={5} className="muted">No alerts.</td></tr>}</tbody>
        </table></div>
      </section>

      <section className="card">
        <h2 className="card-title">Risk events</h2>
        <div className="scroll tall"><table>
          <thead><tr><th>#</th><th>Time</th><th>Kind</th><th>Detail</th></tr></thead>
          <tbody>{r.events.map((e) => <tr key={e.seq}><td>{e.seq}</td><td>{e.ts.slice(0, 16).replace("T", " ")}</td><td><span className="reason-chip" title={reasonText(e.kind)}>{e.kind}</span></td><td className="wrap">{e.detail}</td></tr>)}
            {r.events.length === 0 && <tr><td colSpan={4} className="muted">No risk events: no halt or trip has been recorded in this session.</td></tr>}</tbody>
        </table></div>
      </section>
    </>
  );
}

export default function PaperRiskPage() {
  return <PaperShell active="/paper/risk">{({ sid }) => <Body sid={sid} />}</PaperShell>;
}
