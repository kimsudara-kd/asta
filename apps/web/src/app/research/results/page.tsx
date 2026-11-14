"use client";
import Link from "next/link";
import { Suspense, useEffect, useState } from "react";
import { AreaChart } from "../../../components/Charts";
import { Side, Skeleton, Signed, Tile } from "../../../components/paper/ui";
import ResearchShell, { usePrincipal } from "../../../components/ResearchShell";
import { RunStatus, Verdict } from "../../../components/research/ui";
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
    <section className="card">
      <h3 className="card-title">{title}</h3>
      <div className="kv">
        {Object.entries(data).map(([k, v]) => (
          <div key={k} style={{ display: "contents" }}>
            <div>{k}</div>
            <div data-testid={`m-${k}`} className={isNa(v) ? "muted" : undefined}>{render(k, v)}</div>
          </div>
        ))}
      </div>
    </section>
  );
}

function RunList() {
  const runs = usePoll<{ runs: RunRow[] }>("/v1/backtests", 4000);
  const [sel, setSel] = useState<string[]>([]);
  const all = runs.data?.runs ?? [];
  const done = all.filter((r) => r.status === "COMPLETED");
  const within = done.filter((r) => r.risk_verdict === "PASSED").length;
  const best = done.filter((r) => r.summary?.net_return_pct).sort((x, y) => Number(y.summary!.net_return_pct) - Number(x.summary!.net_return_pct))[0];
  return (
    <>
      <h1>Backtest results</h1>
      {runs.data ? (
        <div className="grid kpis">
          <Tile label="Runs" sub={`${done.length} completed`}>{all.length}</Tile>
          <Tile label="Within risk budget" sub="completed runs that kept every limit">{within} <span className="unit">of {done.length}</span></Tile>
          <Tile label="Highest simulated return" sub={best ? best.label : "—"}>{best?.summary?.net_return_pct ? <Signed value={best.summary.net_return_pct}>{pctText(best.summary.net_return_pct)}</Signed> : "—"}</Tile>
          <Tile label="Research-grade data" sub="runs on real, validated history">{done.filter((r) => r.research_grade).length} <span className="unit">of {done.length}</span></Tile>
        </div>
      ) : <div className="grid kpis"><Skeleton h={110} /><Skeleton h={110} /><Skeleton h={110} /><Skeleton h={110} /></div>}
      <section className="card">
        <div className="card-head">
          <h2 className="card-title">All runs</h2>
          <Link className="tab" href={`/research/compare?ids=${sel.join(",")}`} aria-disabled={sel.length < 1}>Compare selected ({sel.length})</Link>
        </div>
        <div className="scroll">
        <table data-testid="run-table">
          <thead><tr><th /><th>Run</th><th>Strategy</th><th>Status</th><th className="num">Simulated net P&amp;L</th><th className="num">Return</th><th className="num">Max drawdown</th><th>Risk budget</th><th>Data</th><th>Queued</th></tr></thead>
          <tbody>
            {all.map((r) => (
              <tr key={r.id} data-testid="run-row">
                <td><input type="checkbox" aria-label={`select ${r.label}`} checked={sel.includes(r.id)} onChange={(e) => setSel(e.target.checked ? [...sel, r.id] : sel.filter((x) => x !== r.id))} /></td>
                <td><Link href={`/research/results?id=${r.id}`}>{r.label}</Link></td>
                <td>{r.strategy_id}</td>
                <td><RunStatus status={r.status} progress={r.progress_pct} /></td>
                <td className="num">{r.summary?.net_pnl ? <Signed value={r.summary.net_pnl}>{trimDecimal(r.summary.net_pnl)}</Signed> : "—"}</td>
                <td className="num">{r.summary?.net_return_pct ? <Signed value={r.summary.net_return_pct}>{pctText(r.summary.net_return_pct)}</Signed> : "—"}</td>
                <td className="num">{r.summary?.max_drawdown_pct ? pctText(r.summary.max_drawdown_pct) : "—"}</td>
                <td><Verdict verdict={r.risk_verdict} /></td>
                <td>{r.research_grade === null ? "—" : r.research_grade ? "research-grade" : "NOT research-grade"}</td>
                <td className="nowrap">{r.queued_at.slice(0, 19).replace("T", " ")}</td>
              </tr>
            ))}
            {runs.data && all.length === 0 && <tr><td colSpan={10} className="muted">No backtests yet. Start one in the Strategy lab.</td></tr>}
          </tbody>
        </table>
        </div>
      </section>
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
      <div className="run-hero">
        <RunStatus status={r.status} testId="run-status" />
        {r.risk_verdict && <span className={`pill ${r.risk_verdict === "PASSED" ? "ok" : "bad"}`} data-testid="risk-verdict">{r.risk_verdict === "PASSED" ? "WITHIN RISK BUDGET" : "FAILED RISK BUDGET"}{r.result_status === "STOPPED_RISK_HALT" ? " · STOPPED (drawdown halt)" : ""}</span>}
        {r.research_grade !== null && <span className={`pill ${r.research_grade ? "ok" : "warn"}`} data-testid="grade-pill">{r.research_grade ? "research-grade data" : "NOT research-grade data"}</span>}
        <span className="muted">{r.strategy_id} v{r.strategy_version} · queued {r.queued_at.slice(0, 16).replace("T", " ")} UTC</span>
      </div>
      {active && (
        <section className="card">
          <div className="progress" role="progressbar" aria-valuenow={r.progress_pct}><div style={{ width: `${r.progress_pct}%` }} /></div>
          <p className="muted">{r.status} {r.progress_pct}%</p>
          {canWriteResearch(me?.role) && <button className="warn" onClick={cancel} data-testid="cancel-run">Cancel run</button>}
        </section>
      )}
      {r.error && <div className="badbox" data-testid="run-error">{r.error}</div>}
      {msg && <p role="status">{msg}</p>}
      {r.notes.map((n, i) => <div key={i} className="warnbox">{n}</div>)}

      {done && perf && (
        <>
          <div className="grid kpis" data-testid="metric-cards">
            <Tile label="Simulated net P&L (marked, not realised)" sub={<>{metricText(perf.returns.net_return_pct, "pct")} of starting equity · not realised cash profit</>}>
              <strong data-testid="card-net-pnl" className={String(perf.returns.total_net_pnl).startsWith("-") ? "neg" : "pos"}>{metricText(perf.returns.total_net_pnl, "money")} USDT</strong>
            </Tile>
            <Tile label="Ending equity (marked / liquidation-adjusted)" sub={`starting ${metricText(perf.returns.starting_equity, "money")}`}><span className="kpi-pair">{metricText(perf.returns.ending_equity_marked, "money")} <span className="muted">/ {metricText(perf.returns.ending_equity_liquidation_adjusted, "money")}</span></span></Tile>
            <Tile label="Max drawdown" sub={`abs ${metricText(perf.risk.max_drawdown_abs, "money")}`}>{metricText(perf.risk.max_drawdown_pct, "pct")}</Tile>
            <Tile label="Costs (fees / slippage)" sub={`gross ${metricText(perf.returns.gross_trading_pnl, "money")}`}>{metricText(perf.returns.total_fees_quote_equivalent, "money")} <span className="muted">/ {metricText(perf.returns.total_slippage_cost, "money")}</span></Tile>
            <Tile label="Sharpe (daily, rf 0)" sub="annualised from daily returns"><span data-testid="card-sharpe">{metricText(perf.risk.sharpe)}</span></Tile>
            <Tile label="Closed round trips" sub={`fills ${String(perf.trading.fills)}, rejected ${String(perf.trading.orders_rejected)}`}>{String(perf.trading.closed_round_trips)}</Tile>
          </div>

          <section className="card">
            <h2 className="card-title">Simulated equity (USDT, marked to market)</h2>
            <div data-testid="equity-chart">
              <AreaChart lines={[{ name: "equity", tone: "accent", area: true, points: eq.map((p) => ({ t: Date.parse(p.ts), y: Number(p.equity) })) }]} baseline={Number(perf.returns.starting_equity ?? 0) || undefined} label="Simulated equity" />
            </div>
            <h3 className="sub-h">Drawdown from peak</h3>
            <div data-testid="drawdown-chart">
              <AreaChart height={170} floor={0} yFormat={(v) => `${v.toFixed(1)}%`} label="Drawdown from peak" lines={[{ name: "drawdown", tone: "bad", area: true, points: eq.map((p) => ({ t: Date.parse(p.ts), y: Math.abs(Number(p.drawdown) * 100) })) }]} />
            </div>
          </section>

          {risk && (
            <section className="card" data-testid="risk-panel">
              <h3 className="card-title">Risk incidents</h3>
              <p className="muted">{risk.note}. Limits: {JSON.stringify(risk.limits)}</p>
              {risk.events.length === 0 ? <p className="pos">No risk incidents.</p> : (
                <div className="scroll tall"><table><thead><tr><th>Time (UTC)</th><th>Event</th><th>Detail</th></tr></thead>
                  <tbody>{risk.events.slice(0, 100).map((e) => <tr key={e.seq}><td className="nowrap">{e.ts.slice(0, 19)}</td><td><span className="reason-chip">{e.kind}</span></td><td className="wrap muted">{e.detail}</td></tr>)}</tbody></table></div>
              )}
            </section>
          )}

          <div className="metric-groups">
            <Section title="Returns" data={perf.returns} />
            <Section title="Risk" data={perf.risk} />
            <Section title="Activity" data={perf.trading} />
            <Section title="Portfolio quality" data={perf.portfolio} />
          </div>

          {fills && (
            <section className="card" data-testid="fills-panel">
              <h3 className="card-title">Simulated fills ({fills.total})</h3>
              <p className="muted">Estimated from candle data; maker/taker labels are model assumptions, not exchange fills.</p>
              <div className="scroll tall"><table><thead><tr><th>Time (UTC)</th><th>Market</th><th>Side</th><th className="num">Price</th><th className="num">Quantity</th><th>Liquidity</th><th className="num">Fee</th><th className="num">Slippage</th><th className="num">Realised P&amp;L</th><th>Tag</th></tr></thead>
                <tbody>{fills.fills.slice(0, 200).map((f) => (
                  <tr key={f.fill_id}><td className="nowrap">{f.ts.slice(0, 19).replace("T", " ")}</td><td>{f.symbol}</td><td><Side side={f.side} /></td><td className="num">{trimDecimal(f.price, 4)}</td><td className="num">{trimDecimal(f.quantity, 6)}</td><td>{f.liquidity}</td><td className="num">{trimDecimal(f.fee_amount, 6)} {f.fee_asset}</td><td className="num">{trimDecimal(f.slippage_cost, 4)}</td><td className="num"><Signed value={f.realized_pnl}>{trimDecimal(f.realized_pnl, 4)}</Signed></td><td>{f.tag}</td></tr>
                ))}</tbody></table></div>
            </section>
          )}

          <section className="card">
            <h3 className="card-title">Reproducibility</h3>
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
            <div className="btn-row">
              <button className="ghost" data-testid="export-json" onClick={() => download(`backtest-${id}.json`, "application/json", JSON.stringify({ labels: r.labels, run: r, performance: perf, risk }, null, 1))}>Export JSON</button>
              <button className="ghost" data-testid="export-csv" onClick={() => download(`backtest-${id}-fills.csv`, "text/csv", toCsv((fills?.fills ?? []) as unknown as Record<string, unknown>[]))}>Export fills CSV</button>
            </div>
          </section>
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
