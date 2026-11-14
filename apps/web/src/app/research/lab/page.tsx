"use client";
import { useEffect, useMemo, useState } from "react";
import ResearchShell, { usePrincipal } from "../../../components/ResearchShell";
import { Step } from "../../../components/research/ui";
import { canWriteResearch, explain, post, type DatasetRow, type Profiles, type StrategyInfo } from "../../../lib/research";
import { usePoll } from "../../../lib/usePoll";

type Issue = { level: "error" | "warn"; text: string };

export default function LabPage() {
  const me = usePrincipal();
  const strategies = usePoll<{ strategies: StrategyInfo[] }>("/v1/strategies", 60000);
  const datasets = usePoll<{ datasets: DatasetRow[] }>("/v1/datasets", 10000);
  const profiles = usePoll<Profiles>("/v1/profiles", 60000);

  const [sid, setSid] = useState("sma_cross");
  const [params, setParams] = useState<Record<string, string>>({});
  const [tf, setTf] = useState("1h");
  const [picked, setPicked] = useState<Record<string, string>>({});
  const [ruleSet, setRuleSet] = useState("");
  const [fee, setFee] = useState("");
  const [feeMult, setFeeMult] = useState("1");
  const [slipMult, setSlipMult] = useState("1");
  const [latency, setLatency] = useState("0");
  const [cash, setCash] = useState("1000");
  const [riskProfile, setRiskProfile] = useState("default");
  const [custom, setCustom] = useState({ max_asset_pct: "0.10", max_total_pct: "0.30", daily_loss_pct: "0.02", max_drawdown_pct: "0.05" });
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [warmup, setWarmup] = useState("0");
  const [label, setLabel] = useState("");
  const [baselines, setBaselines] = useState(true);
  const [issues, setIssues] = useState<Issue[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const strat = strategies.data?.strategies.find((s) => s.strategy_id === sid);
  const hasSymbolsParam = !!strat && "symbols" in strat.default_params;

  // reset the parameter editor when the strategy changes
  useEffect(() => {
    if (!strat) return;
    const p: Record<string, string> = {};
    for (const [k, v] of Object.entries(strat.default_params)) {
      if (k === "symbols") continue;
      p[k] = typeof v === "object" ? JSON.stringify(v) : String(v);
    }
    setParams(p);
  }, [strat?.strategy_id]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (!ruleSet && profiles.data) setRuleSet(profiles.data.rule_sets[0]?.id ?? "");
    if (!fee && profiles.data) setFee(profiles.data.fee_profiles[0]?.schedule_id ?? "");
  }, [profiles.data]); // eslint-disable-line react-hooks/exhaustive-deps

  const tfs = useMemo(() => [...new Set((datasets.data?.datasets ?? []).map((d) => d.timeframe))], [datasets.data]);
  const bySymbol = useMemo(() => {
    const m: Record<string, DatasetRow[]> = {};
    for (const d of datasets.data?.datasets ?? []) if (d.timeframe === tf && d.validation_status !== "REJECTED") (m[d.symbol] ??= []).push(d);
    return m;
  }, [datasets.data, tf]);

  useEffect(() => {
    // default selection: first version of every symbol at this timeframe
    const next: Record<string, string> = {};
    for (const [s, rows] of Object.entries(bySymbol)) next[s] = picked[s] && rows.some((r) => r.version_id === picked[s]) ? picked[s] : rows[0].version_id;
    setPicked(next);
  }, [bySymbol]); // eslint-disable-line react-hooks/exhaustive-deps

  function coerce(key: string, raw: string): unknown {
    const def = strat?.default_params[key];
    if (typeof def === "number") return Number(raw);
    if (typeof def === "boolean") return raw === "true";
    if (typeof def === "object") return JSON.parse(raw);
    return raw;
  }

  function buildBody(strategyId: string, strategyParams: Record<string, unknown>, lbl: string) {
    const ids = Object.fromEntries(Object.entries(picked).filter(([, v]) => v));
    return {
      strategy_id: strategyId,
      strategy_params: strategyParams,
      dataset_version_ids: ids,
      rule_set: ruleSet,
      fee_profile: fee,
      fee_multiplier: feeMult,
      slippage_multiplier: slipMult,
      latency_bars: Number(latency),
      starting_cash: cash,
      risk: riskProfile === "custom" ? { profile: "custom", ...custom } : { profile: riskProfile },
      period_start: start ? `${start}T00:00:00Z` : null,
      period_end: end ? `${end}T00:00:00Z` : null,
      warmup_bars: Number(warmup),
      label: lbl,
    };
  }

  function userParams(): Record<string, unknown> {
    const out: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(params)) out[k] = coerce(k, v);
    if (hasSymbolsParam) out.symbols = Object.keys(picked);
    return out;
  }

  function check(): Issue[] {
    const out: Issue[] = [];
    if (!Object.keys(picked).length) out.push({ level: "error", text: "Select at least one dataset." });
    try { userParams(); } catch { out.push({ level: "error", text: "A parameter is not valid JSON/number." }); }
    const rows = Object.values(picked).map((v) => datasets.data?.datasets.find((d) => d.version_id === v)).filter(Boolean) as DatasetRow[];
    if (rows.some((r) => !r.accepted_for_research)) out.push({ level: "warn", text: "Some selected datasets are synthetic/fixture or not research-grade: results will be labelled NOT research-grade." });
    const rs = profiles.data?.rule_sets.find((r) => r.id === ruleSet);
    if (rs && !rs.verified) out.push({ level: "warn", text: `Rule set "${rs.id}" is an unverified assumption.` });
    const fp = profiles.data?.fee_profiles.find((f) => f.schedule_id === fee);
    if (fp && !fp.verified) out.push({ level: "warn", text: `Fee profile "${fp.schedule_id}" is an unverified assumption.` });
    if (riskProfile === "unconstrained_reference") out.push({ level: "warn", text: "Unconstrained reference runs ignore the risk budget and are labelled RISK_UNCONSTRAINED_REFERENCE." });
    if (start && end && start >= end) out.push({ level: "error", text: "Period start must be before the end." });
    if (!ruleSet || !fee) out.push({ level: "error", text: "Choose a rule set and a fee profile." });
    return out;
  }

  async function run() {
    if (!me) return;
    const found = check();
    setIssues(found);
    if (found.some((i) => i.level === "error")) return;
    setBusy(true);
    setMsg(null);
    try {
      const ids: string[] = [];
      const main = await post<{ id: string }>("/v1/backtests", buildBody(sid, userParams(), label || `${sid} (lab)`), me.csrf_token);
      ids.push(main.id);
      if (baselines) {
        const syms = Object.keys(picked);
        const hold = await post<{ id: string }>("/v1/backtests", buildBody("passive_hold", { symbols: syms, notional_per_symbol: "100" }, "Passive hold (baseline)"), me.csrf_token);
        const cashRun = await post<{ id: string }>("/v1/backtests", buildBody("all_cash", {}, "All cash (baseline)"), me.csrf_token);
        ids.push(hold.id, cashRun.id);
      }
      window.location.href = ids.length > 1 ? `/research/compare?ids=${ids.join(",")}` : `/research/results?id=${ids[0]}`;
    } catch (e) {
      setMsg(await explain(e));
    } finally {
      setBusy(false);
    }
  }

  const canRun = canWriteResearch(me?.role);
  const nSyms = Object.keys(picked).length;
  const riskText = riskProfile === "custom" ? "Custom limits" : riskProfile === "unconstrained_reference" ? "Unconstrained reference" : "Default budget";
  return (
    <ResearchShell active="/research/lab">
      <h1>Strategy lab</h1>
      <p className="muted">Configure a simulation. Runs execute inside the offline simulator only; they cannot reach an exchange.</p>

      <div className="lab-grid">
        <div>
          <Step n={1} title="Strategy" hint="Pick a rule set and tune its parameters.">
            <div className="row">
              <label>Strategy
                <select value={sid} onChange={(e) => setSid(e.target.value)} data-testid="strategy-select">
                  {(strategies.data?.strategies ?? []).map((s) => <option key={s.strategy_id} value={s.strategy_id}>{s.display_name} v{s.version}</option>)}
                </select>
              </label>
              <label>Run label<input value={label} onChange={(e) => setLabel(e.target.value)} placeholder="optional name" /></label>
            </div>
            {strat && <p className="muted">{strat.description}</p>}
            <div className="row">
              {Object.entries(params).map(([k, v]) => (
                <label key={k}>{k}
                  {v.startsWith("[") || v.startsWith("{")
                    ? <textarea value={v} onChange={(e) => setParams({ ...params, [k]: e.target.value })} style={{ minWidth: 220 }} />
                    : <input value={v} onChange={(e) => setParams({ ...params, [k]: e.target.value })} data-testid={`param-${k}`} size={10} />}
                </label>
              ))}
            </div>
          </Step>

          <Step n={2} title="Data" hint="Historical candles the simulation replays.">
            <div className="row">
              <label>Timeframe
                <select value={tf} onChange={(e) => setTf(e.target.value)} data-testid="tf-select">
                  {tfs.map((x) => <option key={x}>{x}</option>)}
                  {tfs.length === 0 && <option>1h</option>}
                </select>
              </label>
            </div>
            <div className="sym-picks">
              {Object.entries(bySymbol).map(([s, rows]) => (
                <label key={s}>{s}
                  <select value={picked[s] ?? ""} onChange={(e) => setPicked({ ...picked, [s]: e.target.value })} data-testid={`dataset-${s}`}>
                    <option value="">(not used)</option>
                    {rows.map((r) => <option key={r.version_id} value={r.version_id}>{r.label} · {r.checksum.slice(0, 8)} · {r.row_count} rows</option>)}
                  </select>
                </label>
              ))}
            </div>
            {Object.keys(bySymbol).length === 0 && <p className="muted">No datasets at this timeframe. Import one first.</p>}
            <div className="row">
              <label>Period start (UTC)<input type="date" value={start} onChange={(e) => setStart(e.target.value)} /></label>
              <label>Period end (UTC)<input type="date" value={end} onChange={(e) => setEnd(e.target.value)} /></label>
              <label>Warm-up bars<input value={warmup} onChange={(e) => setWarmup(e.target.value)} size={6} /></label>
            </div>
          </Step>

          <Step n={3} title="Assumptions" hint="Costs, venue rules and the risk budget are recorded with every run.">
            <div className="row">
              <label>Exchange rule profile
                <select value={ruleSet} onChange={(e) => setRuleSet(e.target.value)} data-testid="rules-select">
                  {(profiles.data?.rule_sets ?? []).map((r) => <option key={r.id} value={r.id}>{r.id}{r.verified ? "" : " (unverified)"}</option>)}
                </select>
              </label>
              <label>Fee profile
                <select value={fee} onChange={(e) => setFee(e.target.value)}>
                  {(profiles.data?.fee_profiles ?? []).map((f) => <option key={f.schedule_id} value={f.schedule_id}>{f.schedule_id} ({f.maker_bps}/{f.taker_bps} bps){f.verified ? "" : " (unverified)"}</option>)}
                </select>
              </label>
              <label>Fee multiplier<input value={feeMult} onChange={(e) => setFeeMult(e.target.value)} size={5} /></label>
              <label>Slippage multiplier<input value={slipMult} onChange={(e) => setSlipMult(e.target.value)} size={5} /></label>
              <label>Extra latency (bars)<input value={latency} onChange={(e) => setLatency(e.target.value)} size={4} /></label>
              <label>Starting cash (USDT)<input value={cash} onChange={(e) => setCash(e.target.value)} size={8} data-testid="cash-input" /></label>
            </div>
            <div className="row">
              <label>Risk budget
                <select value={riskProfile} onChange={(e) => setRiskProfile(e.target.value)}>
                  <option value="default">Default (10% per asset, 30% total, 2% daily, 5% drawdown)</option>
                  <option value="custom">Custom limits</option>
                  <option value="unconstrained_reference">Unconstrained reference (not risk-compliant)</option>
                </select>
              </label>
              {riskProfile === "custom" && Object.entries(custom).map(([k, v]) => (
                <label key={k}>{k}<input value={v} onChange={(e) => setCustom({ ...custom, [k]: e.target.value })} size={6} /></label>
              ))}
            </div>
            <p className="muted footnote">Limits are fixed for the whole run and recorded with it. A breached budget is reported as failed regardless of P&amp;L.</p>
          </Step>
        </div>

        <aside className="lab-side">
          <Step n={4} title="Run" hint="Review, validate, then start.">
            <ul className="plan">
              <li><span>Strategy</span><span>{strat ? strat.display_name : sid}</span></li>
              <li><span>Markets</span><span>{nSyms ? Object.keys(picked).join(", ") : "none selected"}</span></li>
              <li><span>Timeframe</span><span>{tf}</span></li>
              <li><span>Starting cash</span><span>{cash} USDT</span></li>
              <li><span>Costs</span><span>fees ×{feeMult} · slippage ×{slipMult}</span></li>
              <li><span>Risk budget</span><span>{riskText}</span></li>
            </ul>
            <label className="chk"><input type="checkbox" checked={baselines} onChange={(e) => setBaselines(e.target.checked)} /> Also run baselines (passive hold, all cash) with identical data and costs</label>
            <div className="btn-row">
              <button className="ghost" onClick={() => setIssues(check())} data-testid="validate-settings">Validate settings</button>
              <button onClick={run} disabled={busy || !canRun} data-testid="run-backtest">Run backtest</button>
            </div>
            {!canRun && <p className="muted footnote">Your role can view the lab but not start runs.</p>}
            {issues && (
              <div data-testid="settings-issues">
                {issues.length === 0 ? <p className="pos">No issues found.</p> : issues.map((i, n) => (
                  <div key={n} className={i.level === "error" ? "badbox" : "warnbox"}>{i.level === "error" ? "Error: " : "Note: "}{i.text}</div>
                ))}
              </div>
            )}
            {msg && <p role="status" className="err" data-testid="lab-msg">{msg}</p>}
          </Step>
        </aside>
      </div>
    </ResearchShell>
  );
}
