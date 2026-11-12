"use client";
import { useState } from "react";
import LineChart from "../../../components/LineChart";
import PaperShell from "../../../components/PaperShell";
import { usePrincipal } from "../../../components/ResearchShell";
import { pctText, trimDecimal } from "../../../lib/format";
import type { DailyReport, PaperPerf } from "../../../lib/paper";
import { canWriteResearch, download, explain, post } from "../../../lib/research";
import { usePoll } from "../../../lib/usePoll";

function Body({ sid }: { sid: string | null }) {
  const me = usePrincipal();
  const perf = usePoll<PaperPerf>(sid ? `/v1/paper/sessions/${sid}/performance` : "/v1/paper/health", 10000);
  const reps = usePoll<{ reports: DailyReport[] }>(sid ? `/v1/reports/daily?session_id=${sid}` : "/v1/paper/health", 20000);
  const [day, setDay] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [open, setOpen] = useState<DailyReport | null>(null);
  const p = sid ? (perf.data as PaperPerf | null) : null;
  if (!p) return <p className="muted">No performance data yet.</p>;
  const t = (s: string) => Date.parse(s);
  const pts = p.points;
  const reports = (reps.data as { reports: DailyReport[] } | null)?.reports ?? [];
  return (
    <>
      <h1>Performance &amp; Reports</h1>
      <p className="muted" data-testid="perf-note">{p.note}</p>
      <p>Net simulated P&amp;L <strong data-testid="perf-net">{trimDecimal(p.net_pnl)}</strong> USDT</p>
      <div className="card">
        <LineChart testId="equity-chart" title="Virtual equity vs benchmarks (USDT)"
          series={[
            { name: "paper portfolio", color: "#58a6ff", points: pts.map((x) => ({ t: t(x.ts), y: Number(x.equity) })) },
            { name: "reference-policy-constrained hold", color: "#3fb950", points: pts.filter((x) => x.constrained_hold).map((x) => ({ t: t(x.ts), y: Number(x.constrained_hold) })) },
            { name: "unconstrained passive hold", color: "#d29922", points: pts.filter((x) => x.unconstrained_hold).map((x) => ({ t: t(x.ts), y: Number(x.unconstrained_hold) })) },
            { name: "all cash", color: "#8b949e", points: pts.filter((x) => x.all_cash).map((x) => ({ t: t(x.ts), y: Number(x.all_cash) })) },
          ]} />
        <LineChart testId="dd-chart" title="Drawdown from peak" yFormat={(v) => `${(v * 100).toFixed(1)}%`}
          series={[{ name: "drawdown", color: "#f85149", points: pts.map((x) => ({ t: t(x.ts), y: Number(x.drawdown) })) }]} />
        <p className="muted">The unconstrained passive benchmark ignores the risk limits; the constrained one obeys them. Neither is a promise of future results.</p>
      </div>
      <div className="card scroll">
        <h3>Strategy comparison and fee analysis</h3>
        <table data-testid="strategy-table">
          <thead><tr><th>Strategy</th><th>Starting</th><th>Equity</th><th>Net P&amp;L</th><th>Realised</th><th>Fills</th><th>Fees</th><th>Est. slippage</th><th>Deployment candidate</th></tr></thead>
          <tbody>{p.strategies.map((s) => <tr key={s.strategy}><td>{s.strategy}</td><td>{trimDecimal(s.starting_cash)}</td><td>{trimDecimal(s.equity)}</td><td>{trimDecimal(s.net_pnl)}</td><td>{trimDecimal(s.realized_pnl)}</td><td>{s.fills}</td><td>{trimDecimal(s.fees, 4)}</td><td>{trimDecimal(s.estimated_slippage, 4)}</td><td>{s.deployment_candidate ? "yes" : "no"}</td></tr>)}</tbody>
        </table>
      </div>
      <div className="card">
        <h3>Data coverage and quality</h3>
        {Object.entries(p.data_coverage).map(([v, c]) => <p key={v} className="muted">{v}: {c.complete_and_fresh} of {c.steps} live steps had complete, fresh bars for every market{c.steps ? ` (${pctText(String(c.complete_and_fresh / c.steps))})` : ""}</p>)}
      </div>
      <div className="card">
        <h3>Daily CEO reports</h3>
        <div className="row">
          {canWriteResearch(me?.role) && (<>
            <label>Day (UTC, completed)<input type="date" value={day} onChange={(e) => setDay(e.target.value)} data-testid="report-day" /></label>
            <button data-testid="make-report" disabled={!day} onClick={async () => { setMsg(null); try { const r = await post<{ created: boolean }>("/v1/reports/daily", { session_id: sid, day }, me!.csrf_token); setMsg(r.created ? "Report created." : "Report already existed (unchanged)."); window.location.reload(); } catch (e) { setMsg(await explain(e)); } }}>Create report</button>
          </>)}
          {msg && <span role="status" data-testid="report-msg">{msg}</span>}
        </div>
        <div className="scroll"><table data-testid="reports-table">
          <thead><tr><th>Day (UTC)</th><th>Net P&amp;L</th><th>Return</th><th>Risk limits</th><th>Reproducible</th><th /></tr></thead>
          <tbody>{reports.map((r) => <tr key={r.date} data-testid="report-row"><td>{r.date}</td><td>{trimDecimal(r.body.financial.net_pnl)}</td><td>{pctText(r.body.financial.return_pct)}</td><td>{r.body.research_conclusion.risk_compliant_period ? "within" : "BREACHED"}</td><td>{r.reproducible ? "yes" : "NO"}</td><td><button onClick={() => setOpen(r)}>Open report</button> <button onClick={() => download(`paper-report-${r.date}.json`, "application/json", JSON.stringify(r.body, null, 1))}>Export JSON</button></td></tr>)}
            {reports.length === 0 && <tr><td colSpan={6} className="muted">No reports yet. Reports are produced for completed UTC days.</td></tr>}</tbody>
        </table></div>
        {open && (
          <div className="card" data-testid="report-detail">
            <h3>Report {open.date}</h3>
            <p className="muted">{open.body.research_conclusion.statement}</p>
            <p>Opening {trimDecimal(open.body.financial.opening_equity)} → closing {trimDecimal(open.body.financial.closing_equity)} · fees {trimDecimal(open.body.financial.fees, 4)} · est. slippage {trimDecimal(open.body.financial.estimated_slippage, 4)}</p>
            <p className="muted">Signals {open.body.trading.signals_generated} ({open.body.trading.signals_rejected} rejected) · fills {open.body.trading.fills} · uptime {pctText(open.body.operations.uptime_fraction)} · market data: {open.body.research_conclusion.market_data}</p>
            <p className="muted">content hash {open.content_hash.slice(0, 16)}…</p>
          </div>
        )}
      </div>
    </>
  );
}

export default function PaperPerformancePage() {
  return <PaperShell active="/paper/performance">{({ sid }) => <Body sid={sid} />}</PaperShell>;
}
