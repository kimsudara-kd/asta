"use client";
import PaperShell from "../../../components/PaperShell";
import { OrderStatus, Side, Skeleton, Tile } from "../../../components/paper/ui";
import { trimDecimal } from "../../../lib/format";
import type { PaperOrderRow, PaperPerf, PaperPortfolio, PaperRisk, PaperSession, PaperSignal } from "../../../lib/paper";
import { reasonText } from "../../../lib/risk";
import { usePoll } from "../../../lib/usePoll";

function Body({ sid, session }: { sid: string | null; session: PaperSession | null }) {
  const sig = usePoll<{ signals: PaperSignal[] }>(sid ? `/v1/paper/sessions/${sid}/signals?limit=12` : null, 8000);
  const ord = usePoll<{ orders: PaperOrderRow[] }>(sid ? `/v1/paper/sessions/${sid}/orders?limit=8` : null, 8000);
  const port = usePoll<PaperPortfolio>(sid ? `/v1/paper/sessions/${sid}/portfolio` : null, 15000);
  const perf = usePoll<PaperPerf>(sid ? `/v1/paper/sessions/${sid}/performance` : null, 15000);
  const risk = usePoll<PaperRisk>(sid ? `/v1/paper/sessions/${sid}/risk` : null, 8000);
  if (!sid || !session) return <p className="muted">No paper session yet, so the agent has nothing to show.</p>;
  const halted = !!risk.data && (risk.data.halts.drawdown_halted || risk.data.halts.daily_loss_tripped);
  const status = session.state !== "RUNNING" ? session.state : halted ? "RUNNING — new buys halted by risk" : "RUNNING";
  const accounts = Object.entries(port.data?.accounts ?? {});
  return (
    <>
      <h1>Agent</h1>
      <section className="card" data-testid="agent-identity">
        <h2 className="card-title">ASTA spot trading agent</h2>
        <p>
          A <strong>rule-based</strong> agent: the strategies below turn closed market bars into trade <em>signals</em>; the risk engine then accepts or refuses each
          one, and accepted signals become <strong>simulated</strong> orders against virtual funds. No language model places, approves or changes orders, and the agent
          has no access to exchange credentials: the execution gateway is <strong>DENY</strong> in this build.
        </p>
        <p className="muted">Spot only · virtual funds · paper trading. Nothing here can reach an exchange.</p>
      </section>

      <div className="grid kpis">
        <Tile label="Agent status" sub={session.state_reason || "paper session state"}>{status}</Tile>
        <Tile label="Strategies" sub={session.timeframe ? `timeframe ${session.timeframe}` : ""}>{session.strategies.length}</Tile>
        <Tile label="Venues" sub="market data only">{session.enabled_venues.join(" · ") || "—"}</Tile>
        <Tile label="Last decision step" sub={`step #${session.last_step_seq}`}>{session.last_step_ts ? session.last_step_ts.slice(0, 16).replace("T", " ") : "—"}</Tile>
      </div>

      <section className="card">
        <h2 className="card-title">Strategies the agent runs</h2>
        <div className="scroll"><table data-testid="agent-strategies">
          <thead><tr><th>Strategy</th><th>Version</th><th>Research status</th><th>Net P&amp;L (sim)</th><th>Fills</th></tr></thead>
          <tbody>{session.strategies.map((s) => {
            const p = perf.data?.strategies.find((x) => x.strategy === s.strategy_id);
            return (
              <tr key={s.strategy_id}>
                <td>{s.strategy_id}</td><td>{s.version}</td>
                <td><span className="pill na">{s.research_status}</span>{s.deployment_candidate ? " candidate" : ""}</td>
                <td>{p ? trimDecimal(p.net_pnl) : <Skeleton h={14} />}</td><td>{p ? p.fills : "—"}</td>
              </tr>
            );
          })}</tbody>
        </table></div>
        <p className="muted">Simulated results with estimated fills; not a prediction, not investment advice.</p>
      </section>

      <section className="card">
        <h2 className="card-title">Current holdings (virtual)</h2>
        <div className="scroll"><table data-testid="agent-holdings">
          <thead><tr><th>Account</th><th>Cash</th><th>Positions</th><th>Equity</th></tr></thead>
          <tbody>{accounts.map(([k, a]) => (
            <tr key={k}>
              <td>{k}</td><td>{trimDecimal(a.cash_total)}</td>
              <td>{a.assets.filter((x) => Number(x.quantity) > 0).map((x) => `${trimDecimal(x.quantity)} ${x.asset}`).join(", ") || "none"}</td>
              <td>{trimDecimal(a.equity)}</td>
            </tr>
          ))}</tbody>
        </table></div>
      </section>

      <section className="card">
        <h2 className="card-title">Latest decisions (signals)</h2>
        <div className="scroll"><table data-testid="agent-signals">
          <thead><tr><th>Step (UTC)</th><th>Strategy</th><th>Symbol</th><th>Side</th><th>Outcome</th><th>Why</th></tr></thead>
          <tbody>{(sig.data?.signals ?? []).map((x) => (
            <tr key={`${x.account}-${x.signal_id}`}>
              <td>{x.step_ts.slice(0, 16).replace("T", " ")}</td><td>{x.strategy}</td><td>{x.symbol}</td><td><Side side={x.side} /></td>
              <td><span className={`pill ${x.accepted ? "ok" : "bad"}`}>{x.accepted ? "accepted" : "refused"}</span></td>
              <td>{x.reason ? reasonText(x.reason) : "passed the risk checks"}</td>
            </tr>
          ))}</tbody>
        </table></div>
        {sig.data && sig.data.signals.length === 0 && <p className="muted">The agent has produced no signals in this session yet.</p>}
      </section>

      <section className="card">
        <h2 className="card-title">Latest simulated orders</h2>
        <div className="scroll"><table data-testid="agent-orders">
          <thead><tr><th>Time (UTC)</th><th>Symbol</th><th>Side</th><th>Qty</th><th>Status</th></tr></thead>
          <tbody>{(ord.data?.orders ?? []).map((o) => (
            <tr key={`${o.account}-${o.order_id}`}>
              <td>{o.created_ts.slice(0, 16).replace("T", " ")}</td><td>{o.symbol}</td><td><Side side={o.side} /></td>
              <td>{trimDecimal(o.quantity)}</td><td><OrderStatus status={o.status} /></td>
            </tr>
          ))}</tbody>
        </table></div>
        <p className="muted">Full history and filters: Signals &amp; Paper Orders.</p>
      </section>
    </>
  );
}

export default function Page() {
  return <PaperShell active="/paper/agent">{({ sid, session }) => <Body sid={sid} session={session} />}</PaperShell>;
}
