"use client";
import { Suspense, useEffect, useState } from "react";
import LineChart from "../../../components/LineChart";
import ResearchShell, { usePrincipal } from "../../../components/ResearchShell";
import { api } from "../../../lib/api";
import { metricText, pctText, trimDecimal } from "../../../lib/format";
import { canWriteResearch, download, explain, post, toCsv, type EquityPoint, type Performance, type RunRow, type ValidationRow } from "../../../lib/research";
import { usePoll } from "../../../lib/usePoll";
import { useQueryParam } from "../../../lib/useQuery";

const COLORS = ["#58a6ff", "#3fb950", "#d29922", "#f85149", "#a371f7", "#ff7b72"];

type Loaded = { run: RunRow; perf: Performance | null; eq: EquityPoint[] };

function flagsFor(l: Loaded, passive: Loaded | undefined): string[] {
  const f: string[] = [];
  if (l.run.risk_verdict && l.run.risk_verdict !== "PASSED") f.push("FAILED_RISK_BUDGET");
  const ret = l.run.summary?.net_return_pct;
  if (ret !== null && ret !== undefined && Number(ret) <= 0) f.push("NEGATIVE_OR_ZERO_NET_RETURN");
  if (l.run.research_grade === false) f.push("NOT_RESEARCH_GRADE_DATA");
  if (passive && l.run.id !== passive.run.id && l.run.summary?.net_return_pct && passive.run.summary?.net_return_pct && Number(l.run.summary.net_return_pct) < Number(passive.run.summary.net_return_pct)) f.push("UNDERPERFORMS_PASSIVE_HOLD");
  return f;
}

const SAMPLE_LABEL: Record<string, string> = {
  IN_SAMPLE: "IN-SAMPLE (not out-of-sample evidence)",
  IN_AND_OUT_OF_SAMPLE: "IN-SAMPLE (train) and OUT-OF-SAMPLE (test) shown separately",
  FINAL_HOLDOUT: "FINAL HOLDOUT (single evaluation; window consumed)",
  UNSPECIFIED: "SAMPLE TYPE NOT RECORDED",
};
const sampleLabel = (t?: string) => SAMPLE_LABEL[t ?? "UNSPECIFIED"] ?? t ?? "";

function Validation({ v }: { v: ValidationRow }) {
  const r = (v.result ?? {}) as Record<string, unknown>;
  const windows = (r.windows as Record<string, unknown>[] | undefined) ?? [];
  const scenarios = (r.scenarios as { scenario: string; value: string; summary: { net_return_pct: string; max_drawdown_pct: string; risk_verdict: string; net_pnl: string } }[] | undefined) ?? [];
  const warnings = (v.warnings ?? (r.warnings as string[] | undefined) ?? []) as string[];
  return (
    <div className="card" data-testid="validation-detail">
      <h3>{v.title} <span className="muted">({v.kind}, cycle {v.cycle})</span></h3>
      <div className={v.sample_type === "IN_SAMPLE" ? "warnbox" : "card"} data-testid="sample-type">{sampleLabel(v.sample_type)}</div>
      <p className="muted" data-testid="study-provenance">
        Strategy {v.strategy_id}{v.strategy_version ? ` v${v.strategy_version}` : ""} · generated {v.generated_at ? v.generated_at.slice(0, 19).replace("T", " ") + " UTC" : "n/a"} · artifact {v.source_artifact ?? "n/a"}
      </p>
      {v.source_datasets && (
        <div className="scroll"><table data-testid="study-datasets">
          <thead><tr><th>Dataset</th><th>Timeframe</th><th>Rows</th><th>Source</th><th>Quality</th><th>Checksum</th></tr></thead>
          <tbody>{Object.entries(v.source_datasets).map(([sym, d]) => (
            <tr key={sym}><td>{sym}</td><td>{d.timeframe}</td><td>{d.rows}</td><td>{d.root_source_type}</td><td>{d.validation}</td><td className="mono">{(d.checksum ?? "").slice(0, 12)}</td></tr>
          ))}</tbody></table></div>
      )}
      {v.oos_reused && <div className="badbox">OUT-OF-SAMPLE REUSED: this result is no longer out-of-sample.</div>}
      {!v.research_grade && <div className="warnbox">Not research-grade data.</div>}
      {warnings.length > 0 && <div data-testid="overfit-warnings">{warnings.map((w, i) => <div key={i} className="warnbox">Overfitting/robustness warning: {w}</div>)}</div>}
      {windows.length > 0 && (
        <div className="scroll"><table data-testid="wf-table">
          <thead><tr><th>Window</th><th>Train</th><th>Test (out-of-sample)</th><th>In-sample return</th><th>Out-of-sample return</th><th>OOS risk budget</th><th>Chosen parameters</th></tr></thead>
          <tbody>{windows.map((w) => {
            const tr = w.train_summary as { net_return_pct: string } | null;
            const te = w.test_summary as { net_return_pct: string; risk_verdict: string } | null;
            return (
              <tr key={String(w.index)}>
                <td>{String(w.index)}</td>
                <td>{String((w.train as string[])[0]).slice(0, 10)} → {String((w.train as string[])[1]).slice(0, 10)}</td>
                <td>{String((w.test as string[])[0]).slice(0, 10)} → {String((w.test as string[])[1]).slice(0, 10)}</td>
                <td>{tr ? pctText(tr.net_return_pct) : "—"}</td>
                <td>{te ? pctText(te.net_return_pct) : String(w.status)}</td>
                <td>{te ? (te.risk_verdict === "PASSED" ? "within" : "FAILED") : "—"}</td>
                <td className="mono">{JSON.stringify(w.chosen_params)}</td>
              </tr>
            );
          })}</tbody></table></div>
      )}
      {scenarios.length > 0 && (
        <div className="scroll"><table data-testid="sens-table">
          <thead><tr><th>Scenario</th><th>Value</th><th>Net P&amp;L</th><th>Return</th><th>Max drawdown</th><th>Risk budget</th></tr></thead>
          <tbody>{scenarios.map((s, i) => <tr key={i}><td>{s.scenario}</td><td>{s.value}</td><td>{trimDecimal(s.summary.net_pnl)}</td><td>{pctText(s.summary.net_return_pct)}</td><td>{pctText(s.summary.max_drawdown_pct)}</td><td>{s.summary.risk_verdict === "PASSED" ? "within" : "FAILED"}</td></tr>)}</tbody></table></div>
      )}
      {v.kind === "HOLDOUT" && (() => {
        const hs = r.holdout_summary as { net_pnl: string; net_return_pct: string; max_drawdown_pct: string; risk_verdict: string; fees: string; fills: number; result_checksum: string } | undefined;
        return hs ? (
          <div className="scroll"><table data-testid="holdout-table">
            <thead><tr><th>Window</th><th>Net P&amp;L (simulated)</th><th>Return</th><th>Max drawdown</th><th>Fees</th><th>Fills</th><th>Risk budget</th><th>Result checksum</th></tr></thead>
            <tbody><tr>
              <td>{String((r.holdout_window as string[])[0]).slice(0, 10)} → {String((r.holdout_window as string[])[1]).slice(0, 10)}</td>
              <td>{trimDecimal(hs.net_pnl)}</td><td>{pctText(hs.net_return_pct)}</td><td>{pctText(hs.max_drawdown_pct)}</td><td>{trimDecimal(hs.fees)}</td><td>{hs.fills}</td>
              <td>{hs.risk_verdict === "PASSED" ? "within" : "FAILED"}</td><td className="mono">{hs.result_checksum.slice(0, 12)}</td>
            </tr></tbody></table></div>
        ) : null;
      })()}
      {v.kind === "BASELINE_COMPARISON" && (() => {
        const rows = ((r.comparison as { rows?: Record<string, string>[] } | undefined)?.rows ?? []);
        return (
          <div className="scroll"><table data-testid="baseline-table">
            <thead><tr><th>Run</th><th>Net P&amp;L (simulated)</th><th>Return</th><th>Max drawdown</th><th>Fees</th><th>Risk budget</th><th>Flags</th></tr></thead>
            <tbody>{rows.map((b, i) => (
              <tr key={i}><td>{b.name}</td><td>{trimDecimal(b.net_pnl)}</td><td>{pctText(b.net_return_pct)}</td><td>{pctText(b.max_drawdown_pct)}</td><td>{trimDecimal(b.fees)}</td><td>{b.risk_verdict === "PASSED" ? "within" : "FAILED"}</td><td>{Array.isArray(b.flags) ? (b.flags as string[]).join(", ") : ""}</td></tr>
            ))}</tbody></table></div>
        );
      })()}
      {v.pooled && <p className="muted">Pooled: {JSON.stringify(v.pooled)}</p>}
      {v.advisory && <p className="muted">Advisory (rule-based, not a claim of profitability): {JSON.stringify(v.advisory)}</p>}
    </div>
  );
}

function ImportStudies({ onDone }: { onDone?: () => void }) {
  const me = usePrincipal();
  const [dir, setDir] = useState("m1-run");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  if (!canWriteResearch(me?.role)) return null;
  return (
    <div className="card">
      <h3>Import completed studies</h3>
      <p className="muted">Reads study files from the server&apos;s local studies folder. Repeating an import never creates duplicates.</p>
      <div className="row">
        <label>Study folder<input value={dir} onChange={(e) => setDir(e.target.value)} data-testid="studies-dir" /></label>
        <button disabled={busy} data-testid="import-studies" onClick={async () => {
          setBusy(true); setMsg(null);
          try {
            const r = await post<{ inserted: string[]; skipped_duplicate: string[] }>("/v1/validations/import", { directory: dir }, me!.csrf_token);
            setMsg(`Imported ${r.inserted.length} new, ${r.skipped_duplicate.length} already stored.`);
            onDone?.();
          } catch (e) { setMsg(await explain(e)); } finally { setBusy(false); }
        }}>Import studies</button>
      </div>
      {msg && <p role="status" data-testid="studies-msg">{msg}</p>}
    </div>
  );
}

function CompareInner() {
  const idsParam = useQueryParam("ids");
  const ids = idsParam ? idsParam.split(",").filter(Boolean) : [];
  const runs = usePoll<{ runs: RunRow[] }>("/v1/backtests", 5000);
  const [rev, setRev] = useState(0);
  const validations = usePoll<{ validations: ValidationRow[] }>(`/v1/validations?rev=${rev}`, 20000);
  const [loaded, setLoaded] = useState<Loaded[]>([]);
  const [pick, setPick] = useState<string[]>([]);
  const [openV, setOpenV] = useState<ValidationRow | null>(null);
  const idKey = ids.join(",");

  useEffect(() => {
    if (!idKey) return;
    let alive = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const load = async () => {
      const out: Loaded[] = [];
      for (const id of idKey.split(",")) {
        const run = await api<RunRow>(`/v1/backtests/${id}`);
        let perf: Performance | null = null;
        let eq: EquityPoint[] = [];
        if (run.status === "COMPLETED") {
          perf = (await api<{ performance: Performance }>(`/v1/backtests/${id}/results`)).performance;
          eq = (await api<{ points: EquityPoint[] }>(`/v1/backtests/${id}/equity?max_points=1000`)).points;
        }
        out.push({ run, perf, eq });
      }
      if (!alive) return;
      setLoaded(out);
      // keep refreshing while any run is still queued/running (reads the FRESH result, not a stale closure)
      if (out.some((l) => l.run.status === "QUEUED" || l.run.status === "RUNNING")) timer = setTimeout(load, 2000);
    };
    void load();
    return () => {
      alive = false;
      if (timer) clearTimeout(timer);
    };
  }, [idKey]);

  const passive = loaded.find((l) => l.run.strategy_id === "passive_hold");
  const rows = loaded.map((l) => ({
    run: l.run.label, strategy: l.run.strategy_id, status: l.run.status,
    net_pnl: l.run.summary?.net_pnl ?? "", return: l.run.summary?.net_return_pct ?? "",
    max_drawdown: l.run.summary?.max_drawdown_pct ?? "", risk_budget: l.run.risk_verdict ?? "",
    flags: flagsFor(l, passive).join("|"), result_checksum: l.run.result_checksum ?? "",
  }));

  return (
    <ResearchShell active="/research/compare">
      <h1>Research comparison</h1>
      {ids.length === 0 && (
        <div className="card">
          <h3>Choose runs to compare</h3>
          {(runs.data?.runs ?? []).filter((r) => r.status === "COMPLETED").map((r) => (
            <label key={r.id} style={{ display: "block" }}>
              <input type="checkbox" checked={pick.includes(r.id)} onChange={(e) => setPick(e.target.checked ? [...pick, r.id] : pick.filter((x) => x !== r.id))} /> {r.label} — {r.summary?.net_return_pct ? pctText(r.summary.net_return_pct) : ""}
            </label>
          ))}
          <button disabled={pick.length < 1} onClick={() => { window.location.href = `/research/compare?ids=${pick.join(",")}`; }}>Compare selected</button>
        </div>
      )}

      {loaded.length > 0 && (
        <>
          <div className="card scroll">
            <table data-testid="compare-table">
              <thead><tr><th>Run</th><th>Strategy</th><th>Status</th><th>Simulated net P&amp;L</th><th>Return</th><th>Max drawdown</th><th>Sharpe</th><th>Round trips</th><th>Risk budget</th><th>Flags</th></tr></thead>
              <tbody>{loaded.map((l) => {
                const fl = flagsFor(l, passive);
                return (
                  <tr key={l.run.id} data-testid="compare-row">
                    <td>{l.run.label}</td><td>{l.run.strategy_id}</td><td>{l.run.status}</td>
                    <td>{l.run.summary?.net_pnl ? trimDecimal(l.run.summary.net_pnl) : "—"}</td>
                    <td>{l.run.summary?.net_return_pct ? pctText(l.run.summary.net_return_pct) : "—"}</td>
                    <td>{l.run.summary?.max_drawdown_pct ? pctText(l.run.summary.max_drawdown_pct) : "—"}</td>
                    <td>{l.perf ? metricText(l.perf.risk.sharpe) : "—"}</td>
                    <td>{l.run.summary?.round_trips ?? "—"}</td>
                    <td>{l.run.risk_verdict ? (l.run.risk_verdict === "PASSED" ? "within" : "FAILED") : "—"}</td>
                    <td>{fl.map((f) => <span key={f} className="pill bad" style={{ marginRight: 4 }}>{f}</span>)}</td>
                  </tr>
                );
              })}</tbody>
            </table>
            <p className="muted">All runs use the identical engine, data and cost model; a flat all-cash result is not a success, and a risk-budget failure is reported whatever the P&amp;L.</p>
          </div>
          <div className="card">
            <LineChart testId="compare-chart" title="Simulated equity — overlay (USDT)"
              series={loaded.filter((l) => l.eq.length > 1).map((l, i) => ({ name: l.run.label, color: COLORS[i % COLORS.length], points: l.eq.map((p) => ({ t: Date.parse(p.ts), y: Number(p.equity) })) }))} />
          </div>
          <div className="row">
            <button data-testid="export-compare-json" onClick={() => download("comparison.json", "application/json", JSON.stringify({ labels: ["SIMULATED", "HISTORICAL DATA", "ESTIMATED FILLS", "NOT LIVE TRADING"], rows }, null, 1))}>Export comparison JSON</button>
            <button data-testid="export-compare-csv" onClick={() => download("comparison.csv", "text/csv", toCsv(rows))}>Export comparison CSV</button>
          </div>
        </>
      )}

      <h2>Stored research validations</h2>
      <p className="muted">Walk-forward, sensitivity and out-of-sample studies. In-sample and out-of-sample results are shown side by side; overfitting warnings are mechanical checks.</p>
      <ImportStudies onDone={() => setRev((n) => n + 1)} />
      <div className="card scroll">
        <table data-testid="validation-table">
          <thead><tr><th>Study</th><th>Kind</th><th>Sample</th><th>Strategy</th><th>Cycle</th><th>Warnings</th><th /></tr></thead>
          <tbody>
            {(validations.data?.validations ?? []).map((v) => (
              <tr key={v.id} data-testid="validation-row">
                <td>{v.title}</td><td>{v.kind}</td><td data-testid="sample-cell">{v.sample_type ?? "UNSPECIFIED"}</td><td>{v.strategy_id}</td><td>{v.cycle}{v.oos_reused ? " (OOS reused)" : ""}</td>
                <td>{(v.warnings ?? []).length}</td>
                <td><button onClick={async () => setOpenV(await api<ValidationRow>(`/v1/validations/${v.id}`))}>Open study</button></td>
              </tr>
            ))}
            {validations.data && validations.data.validations.length === 0 && <tr><td colSpan={7} className="muted">No stored studies yet. An operator can import completed study files below.</td></tr>}
          </tbody>
        </table>
      </div>
      {openV && <Validation v={openV} />}
    </ResearchShell>
  );
}

export default function ComparePage() {
  return (
    <Suspense fallback={<p className="muted">Loading…</p>}>
      <CompareInner />
    </Suspense>
  );
}
