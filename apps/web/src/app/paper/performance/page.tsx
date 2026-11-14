"use client";
import { useState } from "react";
import { AreaChart, type Line } from "../../../components/Charts";
import PaperShell from "../../../components/PaperShell";
import { Signed, Skeleton, Tile } from "../../../components/paper/ui";
import { usePrincipal } from "../../../components/ResearchShell";
import { pctText, trimDecimal } from "../../../lib/format";
import { direction, meterRatio, pctChange } from "../../../lib/overview";
import type { DailyReport, PaperPerf, PaperSession } from "../../../lib/paper";
import { sumDecimals } from "../../../lib/paperStats";
import { canWriteResearch, download, explain, post } from "../../../lib/research";
import { usePoll } from "../../../lib/usePoll";

function Body({ sid, session }: { sid: string | null; session: PaperSession | null }) {
  const me = usePrincipal();
  const perf = usePoll<PaperPerf>(sid ? `/v1/paper/sessions/${sid}/performance` : "/v1/paper/health", 10000);
  const reps = usePoll<{ reports: DailyReport[] }>(sid ? `/v1/reports/daily?session_id=${sid}` : "/v1/paper/health", 20000);
  const [day, setDay] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [open, setOpen] = useState<DailyReport | null>(null);
  const p = sid ? (perf.data as PaperPerf | null) : null;
  if (!p) return sid ? <><h1>Performance &amp; Reports</h1><div className="grid kpis"><Skeleton h={120} /><Skeleton h={120} /><Skeleton h={120} /><Skeleton h={120} /></div></> : <p className="muted">No performance data yet.</p>;
  const t = (s: string) => Date.parse(s);
  const pts = p.points;
  const reports = (reps.data as { reports: DailyReport[] } | null)?.reports ?? [];
  const mk = (name: string, tone: Line["tone"], pick: (x: PaperPerf["points"][number]) => string | null, extra: Partial<Line> = {}): Line => ({
    name, tone, points: pts.filter((x) => pick(x) !== null && pick(x) !== "").map((x) => ({ t: t(x.ts), y: Number(pick(x)) })), ...extra, // chart geometry only
  });
  const equityLines: Line[] = [
    mk("paper portfolio", "accent", (x) => x.equity, { area: true }),
    mk("reference-policy-constrained hold", "ok", (x) => x.constrained_hold, { dashed: true }),
    mk("unconstrained passive hold", "warn", (x) => x.unconstrained_hold, { dashed: true }),
    mk("all cash", "muted", (x) => x.all_cash),
  ];
  const ddLines: Line[] = [mk("drawdown", "bad", (x) => x.drawdown, { area: true })];
  const start = session ? session.starting_equity : (pts[0]?.equity ?? "0");
  const ret = pctChange(p.equity ?? pts[pts.length - 1]?.equity ?? null, start);
  const fees = sumDecimals(p.strategies.map((s) => s.fees));
  const slip = sumDecimals(p.strategies.map((s) => s.estimated_slippage));
  const fills = p.strategies.reduce((n, s) => n + s.fills, 0);
  const maxAbs = Math.max(0.0001, ...p.strategies.map((s) => Math.abs(Number(s.net_pnl)))); // bar geometry only
  return (
    <>
      <h1>Performance &amp; Reports</h1>
      <p className="muted" data-testid="perf-note">{p.note}</p>

      <div className="grid kpis">
        <Tile label="Net simulated P&L" sub="after estimated fees and slippage">
          <strong data-testid="perf-net" className={direction(p.net_pnl) === "down" ? "neg" : direction(p.net_pnl) === "up" ? "pos" : ""}>{trimDecimal(p.net_pnl)}</strong> <span className="unit">USDT</span>
        </Tile>
        <Tile label="Return since start" sub={`on ${trimDecimal(start)} USDT starting equity`}>
          <span className={`delta big-delta ${direction(ret?.replace("%", ""))}`}>{ret ?? "—"}</span>
        </Tile>
        <Tile label="Estimated costs" sub={`fees ${fees} · slippage ${slip} USDT`}>{fills.toLocaleString("en-US")} <span className="unit">simulated fills</span></Tile>
        <Tile label="Strategies" sub="none is a deployment candidate">{p.strategies.length} <span className="unit">compared below</span></Tile>
      </div>

      <section className="card">
        <h2 className="card-title">Virtual equity vs benchmarks (USDT)</h2>
        <div data-testid="equity-chart">
          <AreaChart lines={equityLines} baseline={Number(start)} yFormat={(v) => v.toFixed(0)} label="Virtual equity compared with benchmarks" />
        </div>
        <div className="legend">
          {equityLines.map((l) => <span key={l.name} className="legend-item"><i className={`swatch ${l.tone}${l.dashed ? " dashed" : ""}`} />{l.name}</span>)}
        </div>
        <h3 className="sub-h">Drawdown from peak</h3>
        <div data-testid="dd-chart">
          <AreaChart lines={ddLines} height={170} floor={0} yFormat={(v) => `${(v * 100).toFixed(1)}%`} label="Drawdown from peak" />
        </div>
        <p className="muted footnote">The unconstrained passive benchmark ignores the risk limits; the constrained one obeys them. Neither is a promise of future results.</p>
      </section>

      <section className="card">
        <h2 className="card-title">Strategy comparison and fee analysis</h2>
        <div className="scroll">
          <table data-testid="strategy-table">
            <thead><tr><th>Strategy</th><th className="num">Starting</th><th className="num">Equity</th><th>Net P&amp;L</th><th className="num">Realised</th><th className="num">Fills</th><th className="num">Fees</th><th className="num">Est. slippage</th><th>Deployment candidate</th></tr></thead>
            <tbody>{p.strategies.map((s) => (
              <tr key={s.strategy}>
                <td className="acct">{s.strategy}</td><td className="num">{trimDecimal(s.starting_cash)}</td><td className="num">{trimDecimal(s.equity)}</td>
                <td className="pnlcell"><Signed value={s.net_pnl}>{trimDecimal(s.net_pnl)}</Signed><div className="pbar"><div className={Number(s.net_pnl) < 0 ? "neg" : "pos"} style={{ width: `${Math.max(3, Math.round((Math.abs(Number(s.net_pnl)) / maxAbs) * 100))}%` }} /></div></td>
                <td className="num"><Signed value={s.realized_pnl}>{trimDecimal(s.realized_pnl)}</Signed></td><td className="num">{s.fills}</td><td className="num">{trimDecimal(s.fees, 4)}</td><td className="num">{trimDecimal(s.estimated_slippage, 4)}</td><td>{s.deployment_candidate ? "yes" : "no"}</td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </section>

      <section className="card">
        <h2 className="card-title">Data coverage and quality</h2>
        <ul className="xbars">
          {Object.entries(p.data_coverage).map(([v, c]) => {
            const ratio = c.steps ? meterRatio(String(c.complete_and_fresh), String(c.steps)) : 0;
            return (
              <li key={v} className="xbar">
                <div className="xbar-top"><span className="xbar-name">{v}</span><span className="muted">{c.complete_and_fresh} of {c.steps} live steps had complete, fresh bars for every market{c.steps ? ` (${pctText(String(c.complete_and_fresh / c.steps))})` : ""}</span></div>
                <div className="xbar-track"><div className={`xbar-fill ${ratio >= 0.95 ? "ok" : ratio >= 0.7 ? "warn" : "bad"}`} style={{ width: `${Math.max(2, Math.round(ratio * 100))}%` }} /></div>
              </li>
            );
          })}
        </ul>
      </section>

      <section className="card">
        <h2 className="card-title">Daily CEO reports</h2>
        <div className="row">
          {canWriteResearch(me?.role) && (<>
            <label>Day (UTC, completed)<input type="date" value={day} onChange={(e) => setDay(e.target.value)} data-testid="report-day" /></label>
            <button data-testid="make-report" disabled={!day} onClick={async () => { setMsg(null); try { const r = await post<{ created: boolean }>("/v1/reports/daily", { session_id: sid, day }, me!.csrf_token); setMsg(r.created ? "Report created." : "Report already existed (unchanged)."); window.location.reload(); } catch (e) { setMsg(await explain(e)); } }}>Create report</button>
          </>)}
          {msg && <span role="status" data-testid="report-msg">{msg}</span>}
        </div>
        <div className="scroll"><table data-testid="reports-table">
          <thead><tr><th>Day (UTC)</th><th className="num">Net P&amp;L</th><th className="num">Return</th><th>Risk limits</th><th>Reproducible</th><th /></tr></thead>
          <tbody>{reports.map((r) => (
            <tr key={r.date} data-testid="report-row">
              <td className="acct">{r.date}</td><td className="num"><Signed value={r.body.financial.net_pnl}>{trimDecimal(r.body.financial.net_pnl)}</Signed></td><td className="num">{pctText(r.body.financial.return_pct)}</td>
              <td>{r.body.research_conclusion.risk_compliant_period ? <span className="pill ok">within</span> : <span className="pill bad">BREACHED</span>}</td>
              <td>{r.reproducible ? <span className="pill ok">yes</span> : <span className="pill bad">NO</span>}</td>
              <td className="actions"><button className="ghost" onClick={() => setOpen(r)}>Open report</button> <button className="ghost" onClick={() => download(`paper-report-${r.date}.json`, "application/json", JSON.stringify(r.body, null, 1))}>Export JSON</button></td>
            </tr>
          ))}
            {reports.length === 0 && <tr><td colSpan={6} className="muted">No reports yet. Reports are produced for completed UTC days.</td></tr>}</tbody>
        </table></div>
        {open && (
          <div className="card report-card" data-testid="report-detail">
            <h3>Report {open.date}</h3>
            <p className="muted">{open.body.research_conclusion.statement}</p>
            <p>Opening {trimDecimal(open.body.financial.opening_equity)} → closing {trimDecimal(open.body.financial.closing_equity)} · fees {trimDecimal(open.body.financial.fees, 4)} · est. slippage {trimDecimal(open.body.financial.estimated_slippage, 4)}</p>
            <p className="muted">Signals {open.body.trading.signals_generated} ({open.body.trading.signals_rejected} rejected) · fills {open.body.trading.fills} · uptime {pctText(open.body.operations.uptime_fraction)} · market data: {open.body.research_conclusion.market_data}</p>
            <p className="muted mono-line">content hash {open.content_hash.slice(0, 16)}…</p>
          </div>
        )}
      </section>
    </>
  );
}

export default function PaperPerformancePage() {
  return <PaperShell active="/paper/performance">{({ sid, session }) => <Body sid={sid} session={session} />}</PaperShell>;
}
