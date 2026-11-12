"use client";
import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import LineChart from "../../../components/LineChart";
import ResearchShell, { usePrincipal } from "../../../components/ResearchShell";
import { api } from "../../../lib/api";
import { isNa, metricText, pctText, trimDecimal } from "../../../lib/format";
import {
  canWriteResearch,
  download,
  explain,
  post,
  toCsv,
  type EquityPoint,
  type FillRow,
  type Performance,
  type RiskReport,
  type RunRow,
} from "../../../lib/research";
import { usePoll } from "../../../lib/usePoll";
import { useQueryParam } from "../../../lib/useQuery";

const MONEY = new Set([
  "starting_equity", "ending_equity_marked", "ending_equity_liquidation_adjusted", "total_net_pnl",
  "gross_trading_pnl", "total_fees_quote_equivalent", "total_slippage_cost", "realized_pnl", "unrealized_pnl",
  "max_drawdown_abs", "avg_win", "avg_loss", "avg_net_pnl_per_trade", "gross_traded_notional",
  "unrealized_pnl_total", "final_value",
]);
const PCT = new Set([
  "net_return_pct", "annualized_return", "max_drawdown_pct", "avg_drawdown_pct", "avg_underwater_depth_pct",
  "volatility_annualized", "worst_day_return", "daily_return_p05", "win_rate", "maker_fill_share",
  "taker_fill_share", "fee_to_gross_profit_ratio", "avg_exposure_over_equity", "max_exposure_over_equity",
  "max_asset_share", "avg_inventory_value_over_equity",
]);

function render(key: string, v: unknown): string {
  if (isNa(v)) return metricText(v);
  if (v !== null && typeof v === "object") {
    return Object.entries(v as Record<string, unknown>)
      .map(([k, x]) => `${k}: ${typeof x === "object" && x !== null ? JSON.stringify(x) : MONEY.has(k) ? trimDecimal(String(x)) : PCT.has(k) || key === "time_at_exposure" || key === "monthly_returns" ? pctText(String(x)) : String(x)}`)
      .join(" · ");
  }
  return metricText(v, MONEY.has(key) ? "money" : PCT.has(key) ? "pct" : "plain");
}

function Section({ title, data }: { title: string; data: Record<string, unknown> }) {
  return (
    <div className="card">
      <h3>{title}</h3>
      <div className="kv">
        {Object.entries(data).map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}>
            <div>{k}</div>
            <div data-testid={`m-${k}`} className={isNa(v) ? "muted" : undefined}>{render(k, v)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

function RunList() {
  const runs = usePoll<{ runs: RunRow[] }>("/v1/backtests", 4000);
  const [sel, setSel] = useState<string[]>([]);
  return (
    <>
      <h1>Backtest results</h1>
      <div className="row">
        <Link className="tab" href={`/research/compare?ids=${sel.join(",")}`} aria-disabled={sel.length < 1}>Compare selected ({sel.length})</Link>
      </div>
      <div className="card scroll">
        <table data-testid="run-table">
          <thead><tr><th /><th>Run</th><th>Strategy</th><th>Status</th><th>Simulated net P&amp;L</th><th>Return</th><th>Max drawdown</th><th>Risk budget</th><th>Data</th><th>Queued</th></tr></thead>
          <tbody>
            {(runs.data?.runs ?? []).map((r) => (
              <tr key={r.id} data-testid="run-row">
                <td><input type="checkbox" aria-label={`select ${r.label}`} checked={sel.includes(r.id)} onChange={(e) => setSel(e.target.checked ? [...sel, r.id] : sel.filter((x) => x !== r.id))} /></td>
                <td><Link href={`/research/results?id=${r.id}`}>{r.label}</Link></td>
                <td>{r.strategy_id}</td>
                <td><span className={`pill ${r.status === "COMPLETED" ? "ok" : r.status === "FAILED" || r.status === "CANCELLED" ? "bad" : "warn"}`}>{r.status}{r.status === "RUNNING" ? ` ${r.progress_pct}%` : ""}</span></td>
                <td>{r.summary?.net_pnl ? trimDecimal(r.summary.net_pnl) : "—"}</td>
                <td>{r.summary?.net_return_pct ? pctText(r.summary.net_return_pct) : "—"}</td>
                <td>{r.summary?.max_drawdown_pct ? pctText(r.summary.max_drawdown_pct) : "—"}</td>
                <td>{r.risk_verdict ? <span className={`pill ${r.risk_verdict === "PASSED" ? "ok" : "bad"}`}>{r.risk_verdict === "PASSED" ? "within budget" : "FAILED budget"}</span> : "—"}</td>
                <td>{r.research_grade === null ? "—" : r.research_grade ? "research-grade" : "NOT research-grade"}</td>
                <td>{r.queued_at.slice(0, 19)}</td>
              </tr>
            ))}
            {runs.data && runs.data.runs.length === 0 && <tr><td colSpan={10} className="muted">No backtests yet. Start one in the Strategy lab.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}

function RunDetail({ id }: { id: string }) {
  const me = usePrincipal();
  const run = usePoll<RunRow>(`/v1/backtests/${id}`, 1500);
  const done = run.data?.status === "COMPLETED";
  const [perf, setPerf] = useState<Performance | null>(null);
  const [eq, setEq] = useState<EquityPoint[]>([]);
  const [fills, setFills] = useState<{ total: number; fills: FillRow[] } | null>(null);
  const [risk, setRisk] = useState<RiskReport | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => {
    if (!done) return;
    void (async () => {
      setPerf((await api<{ performance: Performance }>(`/v1/backtests/${id}/results`)).performance);
      setEq((await api<{ points: EquityPoint[] }>(`/v1/backtests/${id}/equity?max_points=1500`)).points);
      setFills(await api(`/v1/backtests/${id}/trades?limit=300`));
      setRisk(await api(`/v1/backtests/${id}/risk`));
    })();
  }, [done, id]);

  async function cancel() {
    if (!me) return;
    try { await post(`/v1/backtests/${id}/cancel`, {}, me.csrf_token); setMsg("Cancel requested."); } catch (e) { setMsg(await explain(e)); }
  }

  const r = run.data;
  if (!r) return <p className="muted">Loading…</p>;
  const active = r.status === "QUEUED" || r.status === "RUNNING";
  return (
    <>
      <h1>{r.label}</h1>
      <p>
        <span className={`pill ${r.status === "COMPLETED" ? "ok" : active ? "warn" : "bad"}`} data-testid="run-status">{r.status}</span>{" "}
        {r.risk_verdict && <span className={`pill ${r.risk_verdict === "PASSED" ? "ok" : "bad"}`} data-testid="risk-verdict">{r.risk_verdict === "PASSED" ? "WITHIN RISK BUDGET" : "FAILED RISK BUDGET"}{r.result_status === "STOPPED_RISK_HALT" ? " · STOPPED (drawdown halt)" : ""}</span>}{" "}
        {r.research_grade !== null && <span className={`pill ${r.research_grade ? "ok" : "warn"}`} data-testid="grade-pill">{r.research_grade ? "research-grade data" : "NOT research-grade data"}</span>}
      </p>
      {active && (
        <div className="card">
          <div className="progress" role="progressbar" aria-valuenow={r.progress_pct}><div style={{ width: `${r.progress_pct}%` }} /></div>
          <p className="muted">{r.status} {r.progress_pct}%</p>
          {canWriteResearch(me?.role) && <button onClick={cancel} data-testid="cancel-run">Cancel run</button>}
        </div>
      )}
      {r.error && <div className="badbox" data-testid="run-error">{r.error}</div>}
      {msg && <p role="status">{msg}</p>}
      {r.notes.map((n, i) => <div key={i} className="warnbox">{n}</div>)}

      {done && perf && (
        <>
          <div className="grid" data-testid="metric-cards">
            <div className="card"><div className="muted">Simulated net P&amp;L (marked, not realised)</div><strong data-testid="card-net-pnl">{metricText(perf.returns.total_net_pnl, "money")} USDT</strong><div className="muted">{metricText(perf.returns.net_return_pct, "pct")} of starting equity</div></div>
            <div className="card"><div className="muted">Ending equity (marked / liquidation-adjusted)</div><strong>{metricText(perf.returns.ending_equity_marked, "money")} / {metricText(perf.returns.ending_equity_liquidation_adjusted, "money")}</strong></div>
            <div className="card"><div className="muted">Max drawdown</div><strong>{metricText(perf.risk.max_drawdown_pct, "pct")}</strong><div className="muted">abs {metricText(perf.risk.max_drawdown_abs, "money")}</div></div>
            <div className="card"><div className="muted">Costs (fees / slippage)</div><strong>{metricText(perf.returns.total_fees_quote_equivalent, "money")} / {metricText(perf.returns.total_slippage_cost, "money")}</strong><div className="muted">gross {metricText(perf.returns.gross_trading_pnl, "money")}</div></div>
            <div className="card"><div className="muted">Sharpe (daily, rf 0)</div><strong data-testid="card-sharpe">{metricText(perf.risk.sharpe)}</strong></div>
            <div className="card"><div className="muted">Closed round trips</div><strong>{String(perf.trading.closed_round_trips)}</strong><div className="muted">fills {String(perf.trading.fills)}, rejected {String(perf.trading.orders_rejected)}</div></div>
          </div>

          <div className="card">
            <LineChart testId="equity-chart" title="Simulated equity (USDT, marked to market)" series={[{ name: "equity", color: "#58a6ff", points: eq.map((p) => ({ t: Date.parse(p.ts), y: Number(p.equity) })) }]} />
            <LineChart testId="drawdown-chart" title="Drawdown from peak (%)" height={160} yFormat={(v) => `${v.toFixed(1)}%`} series={[{ name: "drawdown", color: "#f85149", points: eq.map((p) => ({ t: Date.parse(p.ts), y: Number(p.drawdown) * 100 })) }]} />
          </div>

          {risk && (
            <div className="card" data-testid="risk-panel">
              <h3>Risk incidents</h3>
              <p className="muted">{risk.note}. Limits: {JSON.stringify(risk.limits)}</p>
              {risk.events.length === 0 ? <p>No risk incidents.</p> : (
                <table><thead><tr><th>Time (UTC)</th><th>Event</th><th>Detail</th></tr></thead>
                  <tbody>{risk.events.slice(0, 100).map((e) => <tr key={e.seq}><td>{e.ts.slice(0, 19)}</td><td>{e.kind}</td><td className="muted">{e.detail}</td></tr>)}</tbody></table>
              )}
            </div>
          )}

          <Section title="Returns" data={perf.returns} />
          <Section title="Risk" data={perf.risk} />
          <Section title="Activity" data={perf.trading} />
          <Section title="Portfolio quality" data={perf.portfolio} />

          {fills && (
            <div className="card scroll" data-testid="fills-panel">
              <h3>Simulated fills ({fills.total})</h3>
              <p className="muted">Estimated from candle data; maker/taker labels are model assumptions, not exchange fills.</p>
              <table><thead><tr><th>Time (UTC)</th><th>Market</th><th>Side</th><th>Price</th><th>Quantity</th><th>Liquidity</th><th>Fee</th><th>Slippage</th><th>Realised P&amp;L</th><th>Tag</th></tr></thead>
                <tbody>{fills.fills.slice(0, 200).map((f) => (
                  <tr key={f.fill_id}><td>{f.ts.slice(0, 19)}</td><td>{f.symbol}</td><td>{f.side}</td><td>{f.price}</td><td>{f.quantity}</td><td>{f.liquidity}</td><td>{f.fee_amount} {f.fee_asset}</td><td>{trimDecimal(f.slippage_cost, 4)}</td><td>{trimDecimal(f.realized_pnl, 4)}</td><td>{f.tag}</td></tr>
                ))}</tbody></table>
            </div>
          )}

          <div className="card">
            <h3>Reproducibility</h3>
            <div className="kv">
              <div>Config hash</div><div className="mono" data-testid="config-hash">{r.config_hash}</div>
              <div>Result checksum</div><div className="mono" data-testid="result-checksum">{r.result_checksum}</div>
              <div>Git commit</div><div className="mono">{r.git_commit}</div>
              <div>Strategy</div><div>{r.strategy_id} v{r.strategy_version}</div>
              <div>Execution model</div><div>{r.execution_model_version}</div>
              <div>Fee schedule</div><div>{r.fee_schedule_version}</div>
              <div>Risk policy</div><div>{r.risk_policy_version}</div>
              <div>Seed</div><div>{r.seed}</div>
              <div>Dataset versions</div><div className="mono">{JSON.stringify(r.dataset_version_ids)}</div>
            </div>
            <div className="row">
              <button data-testid="export-json" onClick={() => download(`backtest-${id}.json`, "application/json", JSON.stringify({ labels: r.labels, run: r, performance: perf, risk }, null, 1))}>Export JSON</button>
              <button data-testid="export-csv" onClick={() => download(`backtest-${id}-fills.csv`, "text/csv", toCsv((fills?.fills ?? []) as unknown as Record<string, unknown>[]))}>Export fills CSV</button>
            </div>
          </div>
        </>
      )}
      <p><Link href="/research/results">← all runs</Link></p>
    </>
  );
}

function ResultsInner() {
  const id = useQueryParam("id");
  return (
    <ResearchShell active="/research/results">
      {id ? <RunDetail key={id} id={id} /> : <RunList />}
    </ResearchShell>
  );
}

export default function ResultsPage() {
  return (
    <Suspense fallback={<p className="muted">Loading…</p>}>
      <ResultsInner />
    </Suspense>
  );
}
