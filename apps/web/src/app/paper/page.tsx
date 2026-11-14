"use client";
import { useState } from "react";
import { Sparkline } from "../../components/Charts";
import PaperShell from "../../components/PaperShell";
import { OrderStatus, Side, Skeleton, StatePill, Tile } from "../../components/paper/ui";
import { usePrincipal } from "../../components/ResearchShell";
import { api } from "../../lib/api";
import { pctText, trimDecimal } from "../../lib/format";
import { direction, meterRatio, pctChange } from "../../lib/overview";
import type { FeedsStatus, PaperOrderRow, PaperPerf, PaperRisk, PaperSession, PaperSignal } from "../../lib/paper";
import { canWriteResearch, explain, post } from "../../lib/research";
import { gaugeTone, reasonText } from "../../lib/risk";
import { usePoll } from "../../lib/usePoll";

const STRATS = [
  { id: "regime_grid", label: "Regime-aware spot grid" },
  { id: "trend_pullback", label: "Trend pullback" },
  { id: "sma_cross", label: "SMA cross (research baseline)" },
];

function CreateForm({ onDone }: { onDone: () => void }) {
  const me = usePrincipal();
  const [name, setName] = useState("paper session");
  const [binance, setBinance] = useState("500");
  const [tf, setTf] = useState("1h");
  const [chosen, setChosen] = useState<string[]>(STRATS.map((s) => s.id));
  const [msg, setMsg] = useState<string | null>(null);
  const okx = String(1000 - Number(binance || 0));
  if (!canWriteResearch(me?.role)) return <p className="muted" data-testid="create-note">Your role can view sessions but not create or control them.</p>;
  return (
    <section className="card create-card">
      <h2 className="card-title">Create a simulated session</h2>
      <p className="muted">Virtual funds only: 1,000 USDT split between two virtual venue wallets. The split is fixed for the life of the session.</p>
      <div className="row">
        <label>Name<input value={name} onChange={(e) => setName(e.target.value)} data-testid="new-name" /></label>
        <label>Binance virtual wallet (USDT)<input value={binance} onChange={(e) => setBinance(e.target.value)} size={6} data-testid="new-binance" /></label>
        <label>OKX virtual wallet (USDT)<input value={okx} readOnly size={6} data-testid="new-okx" /></label>
        <label>Timeframe
          <select value={tf} onChange={(e) => setTf(e.target.value)} data-testid="new-tf">
            <option>1h</option><option>15m</option><option>5m</option>
          </select>
        </label>
      </div>
      <div className="chk-row">
        {STRATS.map((s) => (
          <label key={s.id} className="chk"><input type="checkbox" checked={chosen.includes(s.id)} onChange={(e) => setChosen(e.target.checked ? [...chosen, s.id] : chosen.filter((x) => x !== s.id))} /><span>{s.label}</span></label>
        ))}
      </div>
      <button data-testid="create-session" onClick={async () => {
        setMsg(null);
        try {
          await post("/v1/paper/sessions", { name, binance_wallet: binance, okx_wallet: okx, strategies: chosen, timeframe: tf }, me!.csrf_token);
          setMsg("Session created; warm-up history is being applied.");
          setTimeout(onDone, 2500); // let the confirmation be read before the list refreshes
        } catch (e) { setMsg(await explain(e)); }
      }}>Create session</button>
      {msg && <p role="status" data-testid="create-msg">{msg}</p>}
    </section>
  );
}

function Controls({ s, onDone }: { s: PaperSession; onDone: () => void }) {
  const me = usePrincipal();
  const [msg, setMsg] = useState<string | null>(null);
  if (!canWriteResearch(me?.role)) return null;
  const act = async (path: string, body: unknown = {}) => {
    setMsg(null);
    try { await post(`/v1/paper/sessions/${s.id}/${path}`, body, me!.csrf_token); onDone(); }
    catch (e) { setMsg(await explain(e)); }
  };
  return (
    <div className="ctl-bar" data-testid="controls">
      <button disabled={s.state !== "READY"} onClick={() => act("start")} data-testid="ctl-start">Start session</button>
      <button className="ghost" disabled={s.state !== "RUNNING"} onClick={() => act("pause")} data-testid="ctl-pause">Pause session</button>
      <button disabled={s.state !== "PAUSED"} onClick={() => act("resume")} data-testid="ctl-resume">Resume session</button>
      <button className="warn" disabled={s.state !== "RISK_HALTED"} onClick={() => act("rearm")} data-testid="ctl-rearm">Re-arm daily halt</button>
      <button className="danger" disabled={["STOPPED", "FAILED"].includes(s.state)} onClick={() => act("stop", { reason: "operator stop" })} data-testid="ctl-stop">Stop session</button>
      {msg && <span role="status" className="err" data-testid="ctl-msg">{msg}</span>}
    </div>
  );
}

export default function PaperOverview() {
  const feeds = usePoll<FeedsStatus>("/v1/market-feeds/status", 5000);
  return (
    <PaperShell active="/paper">
      {({ sid, session, sessions }) => <Body sid={sid} session={session} count={sessions.length} feeds={feeds.data} />}
    </PaperShell>
  );
}

function Body({ sid, session, count, feeds }: { sid: string | null; session: PaperSession | null; count: number; feeds: FeedsStatus | null }) {
  const risk = usePoll<PaperRisk>(sid ? `/v1/paper/sessions/${sid}/risk` : "/v1/paper/health", 5000);
  const orders = usePoll<{ orders: PaperOrderRow[] }>(sid ? `/v1/paper/sessions/${sid}/orders?limit=100` : "/v1/paper/health", 5000);
  const signals = usePoll<{ signals: PaperSignal[] }>(sid ? `/v1/paper/sessions/${sid}/signals?limit=8` : "/v1/paper/health", 5000);
  const perf = usePoll<PaperPerf>(sid ? `/v1/paper/sessions/${sid}/performance?max_points=240` : null, 15000);
  const [rev, setRev] = useState(0);
  const refresh = () => { setRev((n) => n + 1); window.location.reload(); };
  void rev; void api;
  const r = sid ? (risk.data as PaperRisk | null) : null;
  const open = (orders.data?.orders ?? []).filter((o) => o.status === "OPEN" || o.status === "PARTIALLY_FILLED");
  const pol = r?.policy;
  const ddRatio = meterRatio(r?.current.drawdown_pct, pol?.max_drawdown_pct);
  const exRatio = meterRatio(r?.current.invested_exposure_pct, pol?.max_total_pct);
  const chg = session ? pctChange(session.equity, session.starting_equity) : null;
  const equitySeries = (perf.data?.points ?? []).filter((p) => p.equity).map((p) => ({ t: Date.parse(p.ts), y: Number(p.equity) })); // geometry only
  const fills = (perf.data?.strategies ?? []).reduce((n, s) => n + s.fills, 0);
  const halted = !!r && (r.halts.drawdown_halted || r.halts.daily_loss_tripped);

  return (
    <>
      <h1>Paper Trading Overview</h1>
      {session && r && (
        <div className="grid kpis">
          <Tile label="Virtual equity" sub={<><span className={`delta ${direction(chg?.replace("%", ""))}`}>{chg ?? "—"}</span> vs {trimDecimal(session.starting_equity)} start</>}>
            {trimDecimal(session.equity)} <span className="unit">USDT</span>
            <Sparkline points={equitySeries} label="Virtual equity trend" />
          </Tile>
          <Tile label="Net simulated P&L" sub={`after estimated fees & slippage · ${fills} simulated fills`}>
            <span className={perf.data ? direction(perf.data.net_pnl) === "down" ? "neg" : direction(perf.data.net_pnl) === "up" ? "pos" : "" : ""}>{perf.data ? trimDecimal(perf.data.net_pnl) : "—"}</span> <span className="unit">USDT</span>
          </Tile>
          <Tile label="Drawdown from peak" sub={`limit ${pol?.max_drawdown_pct ? pctText(pol.max_drawdown_pct) : "—"}; beyond it new buys halt`}>
            {pctText(r.current.drawdown_pct)}
            <div className="meter" role="presentation"><div className={`meter-fill ${gaugeTone(ddRatio)}`} style={{ width: `${Math.max(2, Math.round(ddRatio * 100))}%` }} /></div>
          </Tile>
          <Tile label="Invested exposure" sub={`${trimDecimal(r.current.invested_exposure)} USDT of a ${pol?.max_total_pct ? pctText(pol.max_total_pct) : "—"} limit`}>
            {pctText(r.current.invested_exposure_pct)}
            <div className="meter" role="presentation"><div className={`meter-fill ${gaugeTone(exRatio)}`} style={{ width: `${Math.max(2, Math.round(exRatio * 100))}%` }} /></div>
          </Tile>
        </div>
      )}
      {sid && !r && <div className="grid kpis"><Skeleton h={130} /><Skeleton h={130} /><Skeleton h={130} /><Skeleton h={130} /></div>}

      <div className="grid p-ov">
        <section className="card" data-testid="feed-card">
          <h2 className="card-title">Public market-data feeds</h2>
          <div className="feeds">
            {(feeds?.feeds ?? []).map((f) => (
              <div key={f.venue} className="feed" data-testid={`feed-${f.venue}`}>
                <div className="feed-top"><strong>{f.venue}</strong> <span className={f.state === "CONNECTED" ? "pill ok" : "pill bad"}>{f.state}</span></div>
                <div className="muted feed-sub">{f.state === "DISABLED" ? f.reason : `${f.source} · reconnects ${f.reconnects}${f.last_event_age_s !== null ? ` · last event ${Math.round(f.last_event_age_s)} s ago` : ""}`}</div>
              </div>
            ))}
          </div>
          {(feeds?.feeds ?? []).length === 0 && <p className="muted">No feed status yet — the paper service is not running.</p>}
        </section>

        {session && (
          <section className="card session-card" data-testid="session-card">
            <div className="card-head">
              <h2 className="card-title">{session.name}</h2>
              <StatePill state={session.state} testId="session-state" />
            </div>
            {session.state_reason && <p className="muted sess-reason">{session.state_reason}</p>}
            <p className="sess-eq">Virtual equity <strong data-testid="session-equity">{trimDecimal(session.equity)}</strong> USDT of {trimDecimal(session.starting_equity)} · timeframe {session.timeframe}</p>
            <p className="muted">Last step {session.last_step_ts ?? "—"} · reconciliation {session.last_reconcile_ok === null ? "not yet run" : session.last_reconcile_ok ? "OK" : "FAILED"}</p>
            <p className="muted">Strategies: {session.strategies.map((x) => `${x.strategy_id} v${x.version}`).join(", ")} — none is a deployment candidate (no validated edge).</p>
            {session.idle_cash_venues.length > 0 && <p className="warnbox">Idle (uninvested) virtual cash: {session.idle_cash_venues.join(", ")} — no verified feed.</p>}
            <Controls s={session} onDone={refresh} />
          </section>
        )}

        {r && (
          <section className="card" data-testid="risk-card">
            <h2 className="card-title">Risk status</h2>
            <p>Drawdown {pctText(r.current.drawdown_pct)} of {pol?.max_drawdown_pct ? pctText(pol.max_drawdown_pct) : "—"} limit · daily {pctText(r.current.daily_pnl_pct)} (limit −{pol?.daily_loss_pct ? pctText(pol.daily_loss_pct) : "—"})</p>
            <p>Invested exposure {pctText(r.current.invested_exposure_pct)} of {pol?.max_total_pct ? pctText(pol.max_total_pct) : "—"} limit</p>
            <p>{halted ? <span className="pill bad big">HALTED: {r.halts.drawdown_halted ? "drawdown" : "daily loss"}</span> : <span className="pill ok big">within limits</span>}</p>
            {Object.keys(r.rejections_by_reason).length > 0 && (
              <p className="muted risk-ref">Most refusals: {Object.entries(r.rejections_by_reason).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([k, v]) => `${reasonText(k).toLowerCase()} (${v})`).join(", ")}.</p>
            )}
          </section>
        )}
      </div>

      {count === 0 && <p className="muted" data-testid="no-sessions">No paper sessions yet.</p>}
      {session && (
        <>
          <section className="card">
            <h2 className="card-title">Open paper orders ({open.length})</h2>
            <div className="scroll tall"><table data-testid="open-orders">
              <thead><tr><th>Account</th><th>Symbol</th><th>Side</th><th>Type</th><th className="num">Quantity</th><th className="num">Limit</th><th>Status</th></tr></thead>
              <tbody>{open.slice(0, 10).map((o) => <tr key={`${o.account}-${o.order_id}`}><td>{o.account}</td><td>{o.symbol}</td><td><Side side={o.side} /></td><td>{o.type}</td><td className="num">{o.quantity}</td><td className="num">{o.limit_price ?? "—"}</td><td><OrderStatus status={o.status} /></td></tr>)}
                {open.length === 0 && <tr><td colSpan={7} className="muted">No resting simulated orders.</td></tr>}</tbody>
            </table></div>
          </section>
          <section className="card">
            <h2 className="card-title">Recent simulated activity</h2>
            <div className="scroll tall"><table data-testid="recent-signals">
              <thead><tr><th>Step</th><th>Strategy</th><th>Symbol</th><th>Side</th><th>Outcome</th></tr></thead>
              <tbody>{(signals.data?.signals ?? []).map((x) => <tr key={`${x.account}-${x.signal_id}`}><td>{x.step_ts.slice(0, 16)}</td><td>{x.strategy}</td><td>{x.venue}:{x.symbol}</td><td><Side side={x.side} /></td><td>{x.accepted ? <span className="pill ok">accepted</span> : <span className="pill bad">{`rejected: ${x.reason}`}</span>}</td></tr>)}</tbody>
            </table></div>
          </section>
        </>
      )}
      <CreateForm onDone={refresh} />
    </>
  );
}
