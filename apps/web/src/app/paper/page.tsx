"use client";
import { useState } from "react";
import PaperShell from "../../components/PaperShell";
import { usePrincipal } from "../../components/ResearchShell";
import { trimDecimal, pctText } from "../../lib/format";
import { api } from "../../lib/api";
import type { FeedsStatus, PaperOrderRow, PaperRisk, PaperSession, PaperSignal } from "../../lib/paper";
import { canWriteResearch, explain, post } from "../../lib/research";
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
    <div className="card">
      <h3>Create a simulated session</h3>
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
      <div className="row">
        {STRATS.map((s) => (
          <label key={s.id}><span><input type="checkbox" checked={chosen.includes(s.id)} onChange={(e) => setChosen(e.target.checked ? [...chosen, s.id] : chosen.filter((x) => x !== s.id))} /> {s.label}</span></label>
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
    </div>
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
    <div className="row" data-testid="controls">
      <button disabled={s.state !== "READY"} onClick={() => act("start")} data-testid="ctl-start">Start session</button>
      <button disabled={s.state !== "RUNNING"} onClick={() => act("pause")} data-testid="ctl-pause">Pause session</button>
      <button disabled={s.state !== "PAUSED"} onClick={() => act("resume")} data-testid="ctl-resume">Resume session</button>
      <button disabled={s.state !== "RISK_HALTED"} onClick={() => act("rearm")} data-testid="ctl-rearm">Re-arm daily halt</button>
      <button disabled={["STOPPED", "FAILED"].includes(s.state)} onClick={() => act("stop", { reason: "operator stop" })} data-testid="ctl-stop">Stop session</button>
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
  const [rev, setRev] = useState(0);
  const refresh = () => { setRev((n) => n + 1); window.location.reload(); };
  void rev; void api;
  const r = sid ? (risk.data as PaperRisk | null) : null;
  const open = (orders.data?.orders ?? []).filter((o) => o.status === "OPEN" || o.status === "PARTIALLY_FILLED");
  return (
    <>
      <h1>Paper Trading Overview</h1>
      <div className="grid">
        <div className="card" data-testid="feed-card">
          <h3>Public market-data feeds</h3>
          {(feeds?.feeds ?? []).map((f) => (
            <p key={f.venue} data-testid={`feed-${f.venue}`}>
              <strong>{f.venue}</strong> <span className={f.state === "CONNECTED" ? "pill ok" : "pill bad"}>{f.state}</span>{" "}
              <span className="muted">{f.state === "DISABLED" ? f.reason : `${f.source} · reconnects ${f.reconnects}${f.last_event_age_s !== null ? ` · last event ${Math.round(f.last_event_age_s)} s ago` : ""}`}</span>
            </p>
          ))}
          {(feeds?.feeds ?? []).length === 0 && <p className="muted">No feed status yet — the paper service is not running.</p>}
        </div>
        {session && (
          <div className="card" data-testid="session-card">
            <h3>{session.name}</h3>
            <p><span className="pill" data-testid="session-state">{session.state}</span> <span className="muted">{session.state_reason}</span></p>
            <p>Virtual equity <strong data-testid="session-equity">{trimDecimal(session.equity)}</strong> USDT of {trimDecimal(session.starting_equity)} · timeframe {session.timeframe}</p>
            <p className="muted">Last step {session.last_step_ts ?? "—"} · reconciliation {session.last_reconcile_ok === null ? "not yet run" : session.last_reconcile_ok ? "OK" : "FAILED"}</p>
            <p className="muted">Strategies: {session.strategies.map((x) => `${x.strategy_id} v${x.version}`).join(", ")} — none is a deployment candidate (no validated edge).</p>
            {session.idle_cash_venues.length > 0 && <p className="warnbox">Idle (uninvested) virtual cash: {session.idle_cash_venues.join(", ")} — no verified feed.</p>}
            <Controls s={session} onDone={refresh} />
          </div>
        )}
        {r && (
          <div className="card" data-testid="risk-card">
            <h3>Risk status</h3>
            <p>Drawdown {pctText(r.current.drawdown_pct)} of 5% limit · daily {pctText(r.current.daily_pnl_pct)} (limit −2%)</p>
            <p>Invested exposure {pctText(r.current.invested_exposure_pct)} of 30% limit</p>
            <p>{r.halts.drawdown_halted || r.halts.daily_loss_tripped ? <span className="pill bad">HALTED: {r.halts.drawdown_halted ? "drawdown" : "daily loss"}</span> : <span className="pill ok">within limits</span>}</p>
          </div>
        )}
      </div>
      {count === 0 && <p className="muted" data-testid="no-sessions">No paper sessions yet.</p>}
      {session && (
        <div className="card">
          <h3>Open paper orders ({open.length})</h3>
          <div className="scroll"><table data-testid="open-orders">
            <thead><tr><th>Account</th><th>Symbol</th><th>Side</th><th>Type</th><th>Quantity</th><th>Limit</th><th>Status</th></tr></thead>
            <tbody>{open.slice(0, 10).map((o) => <tr key={`${o.account}-${o.order_id}`}><td>{o.account}</td><td>{o.symbol}</td><td>{o.side}</td><td>{o.type}</td><td>{o.quantity}</td><td>{o.limit_price ?? "—"}</td><td>{o.status}</td></tr>)}
              {open.length === 0 && <tr><td colSpan={7} className="muted">No resting simulated orders.</td></tr>}</tbody>
          </table></div>
          <h3>Recent simulated activity</h3>
          <div className="scroll"><table data-testid="recent-signals">
            <thead><tr><th>Step</th><th>Strategy</th><th>Symbol</th><th>Side</th><th>Outcome</th></tr></thead>
            <tbody>{(signals.data?.signals ?? []).map((x) => <tr key={`${x.account}-${x.signal_id}`}><td>{x.step_ts.slice(0, 16)}</td><td>{x.strategy}</td><td>{x.venue}:{x.symbol}</td><td>{x.side}</td><td>{x.accepted ? "accepted" : `rejected: ${x.reason}`}</td></tr>)}</tbody>
          </table></div>
        </div>
      )}
      <CreateForm onDone={refresh} />
    </>
  );
}
