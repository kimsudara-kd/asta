"use client";
import { useState } from "react";
import PaperShell, { } from "../../../components/PaperShell";
import { usePrincipal } from "../../../components/ResearchShell";
import { pctText } from "../../../lib/format";
import type { Alert, FeedsStatus, PaperRisk } from "../../../lib/paper";
import { canWriteResearch, explain, post } from "../../../lib/research";
import { usePoll } from "../../../lib/usePoll";

function Body({ sid }: { sid: string | null }) {
  const me = usePrincipal();
  const risk = usePoll<PaperRisk>(sid ? `/v1/paper/sessions/${sid}/risk` : "/v1/paper/health", 5000);
  const inc = usePoll<{ incidents: { id: string; kind: string; severity: string; detail: string; opened_at: string; resolved_at: string | null }[]; alerts: Alert[] }>(sid ? `/v1/paper/sessions/${sid}/incidents` : "/v1/paper/health", 5000);
  const feeds = usePoll<FeedsStatus>("/v1/market-feeds/status", 5000);
  const [msg, setMsg] = useState<string | null>(null);
  const r = sid ? (risk.data as PaperRisk | null) : null;
  if (!r) return <p className="muted">No risk data yet.</p>;
  const incidents = (inc.data as { incidents: { id: string; kind: string; severity: string; detail: string; opened_at: string; resolved_at: string | null }[]; alerts: Alert[] } | null)?.incidents ?? [];
  const alerts = (inc.data as { alerts: Alert[] } | null)?.alerts ?? [];
  const p = r.policy;
  return (
    <>
      <h1>Risk &amp; Incidents</h1>
      <div className="grid">
        <div className="card" data-testid="limits-card">
          <h3>Exposure limits (global, across venues and strategies)</h3>
          <p>Per asset ≤ {pctText(p.max_asset_pct ?? "0")} · total invested ≤ {pctText(p.max_total_pct ?? "0")} of starting equity {p.starting_equity} USDT</p>
          <p>Daily-loss halt at {pctText(p.daily_loss_pct ?? "0")} · drawdown halt at {pctText(p.max_drawdown_pct ?? "0")} of peak · no leverage, no short sales</p>
          <p className="muted">A daily halt never resets at midnight by itself ({r.daily_auto_reset ? "auto-reset ON" : "operator re-arm required, later UTC day only"}); a drawdown halt is latched for the session.</p>
        </div>
        <div className="card" data-testid="current-card">
          <h3>Current</h3>
          <p>Drawdown {pctText(r.current.drawdown_pct)} · today {pctText(r.current.daily_pnl_pct)} · exposure {pctText(r.current.invested_exposure_pct)}</p>
          <p data-testid="halts">{r.halts.drawdown_halted ? <span className="pill bad">drawdown halt (latched)</span> : r.halts.daily_loss_tripped ? <span className="pill bad">daily-loss halt</span> : <span className="pill ok">no active halt</span>}</p>
          <p className="muted">Rejected signals by reason: {Object.entries(r.rejections_by_reason).map(([k, v]) => `${k || "—"}: ${v}`).join(" · ") || "none"}</p>
        </div>
        <div className="card" data-testid="health-card">
          <h3>Data health</h3>
          {Object.entries(r.market_health).map(([k, v]) => <p key={k} className="muted">{k}: <span className={v === "OK" ? "pill ok" : "pill bad"}>{v}</span></p>)}
          {(feeds.data?.gaps ?? []).slice(0, 5).map((g, i) => <p key={i} className="muted">gap {g.venue} {g.symbol} {g.start.slice(0, 16)} ({g.missing} min) — {g.status}</p>)}
        </div>
      </div>
      <div className="card">
        <h3>Incidents</h3>
        {msg && <p role="status">{msg}</p>}
        <div className="scroll"><table data-testid="incidents-table">
          <thead><tr><th>Opened</th><th>Kind</th><th>Severity</th><th>Detail</th><th>Status</th></tr></thead>
          <tbody>{incidents.map((i) => <tr key={i.id}><td>{i.opened_at.slice(0, 16).replace("T", " ")}</td><td>{i.kind}</td><td>{i.severity}</td><td>{i.detail.slice(0, 140)}</td><td>{i.resolved_at ? "resolved" : "OPEN"}</td></tr>)}
            {incidents.length === 0 && <tr><td colSpan={5} className="muted">No incidents recorded.</td></tr>}</tbody>
        </table></div>
        <h3>In-app alerts</h3>
        <div className="scroll"><table data-testid="alerts-table">
          <thead><tr><th>When</th><th>Kind</th><th>Severity</th><th>Message</th><th /></tr></thead>
          <tbody>{alerts.map((a) => <tr key={a.id}><td>{a.created_at.slice(0, 16).replace("T", " ")}</td><td>{a.kind}</td><td>{a.severity}</td><td>{a.message.slice(0, 120)}</td>
            <td>{a.acknowledged_at ? "acknowledged" : canWriteResearch(me?.role) ? <button onClick={async () => { try { await post(`/v1/paper/alerts/${a.id}/ack`, {}, me!.csrf_token); window.location.reload(); } catch (e) { setMsg(await explain(e)); } }}>Acknowledge</button> : "—"}</td></tr>)}
            {alerts.length === 0 && <tr><td colSpan={5} className="muted">No alerts.</td></tr>}</tbody>
        </table></div>
        <h3>Risk events</h3>
        <div className="scroll"><table>
          <thead><tr><th>#</th><th>Time</th><th>Kind</th><th>Detail</th></tr></thead>
          <tbody>{r.events.map((e) => <tr key={e.seq}><td>{e.seq}</td><td>{e.ts.slice(0, 16).replace("T", " ")}</td><td>{e.kind}</td><td>{e.detail}</td></tr>)}
            {r.events.length === 0 && <tr><td colSpan={4} className="muted">No risk events.</td></tr>}</tbody>
        </table></div>
      </div>
    </>
  );
}

export default function PaperRiskPage() {
  return <PaperShell active="/paper/risk">{({ sid }) => <Body sid={sid} />}</PaperShell>;
}
