"use client";
import { useState } from "react";
import PaperShell from "../../../components/PaperShell";
import { OrderStatus, Side, Skeleton, Tile } from "../../../components/paper/ui";
import { trimDecimal } from "../../../lib/format";
import type { PaperOrderRow, PaperSignal } from "../../../lib/paper";
import { orderStats, signalStats } from "../../../lib/paperStats";
import { reasonText } from "../../../lib/risk";
import { usePoll } from "../../../lib/usePoll";

function Body({ sid }: { sid: string | null }) {
  const [acc, setAcc] = useState("all");
  const [status, setStatus] = useState("");
  const sig = usePoll<{ signals: PaperSignal[] }>(sid ? `/v1/paper/sessions/${sid}/signals?limit=300${acc === "rej" ? "&accepted=false" : acc === "acc" ? "&accepted=true" : ""}` : "/v1/paper/health", 8000);
  const ord = usePoll<{ orders: PaperOrderRow[] }>(sid ? `/v1/paper/sessions/${sid}/orders?limit=300${status ? `&status=${status}` : ""}` : "/v1/paper/health", 8000);
  // unfiltered copies feed the summary tiles, so choosing a filter never changes the totals
  const sigAll = usePoll<{ signals: PaperSignal[] }>(sid ? `/v1/paper/sessions/${sid}/signals?limit=300` : null, 8000);
  const ordAll = usePoll<{ orders: PaperOrderRow[] }>(sid ? `/v1/paper/sessions/${sid}/orders?limit=300` : null, 8000);
  if (!sid) return <p className="muted">No session selected.</p>;
  const signals = (sig.data as { signals: PaperSignal[] } | null)?.signals ?? [];
  const orders = (ord.data as { orders: PaperOrderRow[] } | null)?.orders ?? [];
  const ss = signalStats(sigAll.data?.signals ?? []);
  const os = orderStats(ordAll.data?.orders ?? []);
  const ready = !!sigAll.data && !!ordAll.data;
  return (
    <>
      <h1>Signals &amp; Paper Orders</h1>
      <p className="muted">Strategy signals are separate from simulated execution: a signal can be rejected (with a reason code) and then never becomes an order fill.</p>

      {ready ? (
        <div className="grid kpis">
          <Tile label="Signals generated" sub="in the latest 300">{ss.total.toLocaleString("en-US")}</Tile>
          <Tile label="Accepted" sub={ss.total ? `${Math.round((1 - ss.refusedShare) * 100)}% of signals` : "—"}><span className="pos">{ss.accepted.toLocaleString("en-US")}</span></Tile>
          <Tile label="Refused by safeguards" sub={ss.topReason ? `mostly: ${reasonText(ss.topReason.reason).toLowerCase()}` : "none refused"}>
            {ss.refused.toLocaleString("en-US")}
            <div className="meter" role="presentation"><div className="meter-fill warn" style={{ width: `${Math.max(2, Math.round(ss.refusedShare * 100))}%` }} /></div>
          </Tile>
          <Tile label="Simulated fills" sub={`estimated costs: fees ${os.fees} · slippage ${os.slippage} USDT`}>{os.filled.toLocaleString("en-US")} <span className="unit">of {os.total.toLocaleString("en-US")} orders</span></Tile>
        </div>
      ) : (
        <div className="grid kpis"><Skeleton h={120} /><Skeleton h={120} /><Skeleton h={120} /><Skeleton h={120} /></div>
      )}

      <section className="card">
        <div className="card-head">
          <h2 className="card-title">Strategy signals</h2>
          <label className="inline-filter">Show
            <select value={acc} onChange={(e) => setAcc(e.target.value)} data-testid="sig-filter">
              <option value="all">all</option><option value="acc">accepted</option><option value="rej">rejected</option>
            </select>
          </label>
        </div>
        <div className="scroll tall"><table data-testid="signals-table">
          <thead><tr><th>Step (UTC)</th><th>Account</th><th>Version</th><th>Symbol</th><th>Side</th><th>Type</th><th>Outcome</th><th>Reason</th></tr></thead>
          <tbody>{signals.map((x) => (
            <tr key={`${x.account}-${x.signal_id}`} data-testid="signal-row">
              <td>{x.step_ts.slice(0, 16).replace("T", " ")}</td><td>{x.account}</td><td>{x.strategy_version}</td><td>{x.symbol}</td><td><Side side={x.side} /></td><td>{x.type}</td>
              <td><span className={`pill ${x.accepted ? "ok" : "bad"}`}>{x.accepted ? "accepted" : "rejected"}</span></td>
              <td>{x.reason ? <span className="reason-chip" title={reasonText(x.reason)}>{x.reason}</span> : "—"}</td>
            </tr>
          ))}
            {signals.length === 0 && <tr><td colSpan={8} className="muted">No signals yet.</td></tr>}</tbody>
        </table></div>
        <p className="muted table-cap">{signals.length.toLocaleString("en-US")} signal{signals.length === 1 ? "" : "s"} shown (newest first, up to 300). Scroll inside the table for more.</p>
      </section>

      <section className="card">
        <div className="card-head">
          <h2 className="card-title">Simulated order history (estimated fills)</h2>
          <label className="inline-filter">Status
            <select value={status} onChange={(e) => setStatus(e.target.value)} data-testid="ord-filter">
              <option value="">all</option><option>OPEN</option><option>PARTIALLY_FILLED</option><option>FILLED</option><option>CANCELLED</option><option>EXPIRED</option><option>REJECTED</option>
            </select>
          </label>
        </div>
        <div className="scroll tall"><table data-testid="orders-table">
          <thead><tr><th>Created</th><th>Account</th><th>Symbol</th><th>Side</th><th>Type</th><th className="num">Qty</th><th className="num">Filled</th><th className="num">Limit</th><th>Status</th><th>Reason</th><th className="num">Est. fee</th><th className="num">Est. slippage</th></tr></thead>
          <tbody>{orders.map((o) => (
            <tr key={`${o.account}-${o.order_id}`} data-testid="order-row">
              <td>{o.created_ts.slice(0, 16).replace("T", " ")}</td><td>{o.account}</td><td>{o.symbol}</td><td><Side side={o.side} /></td><td>{o.type}</td>
              <td className="num">{trimDecimal(o.quantity, 6)}</td><td className="num">{trimDecimal(o.filled_qty, 6)}</td><td className="num">{o.limit_price === null ? "—" : trimDecimal(o.limit_price, 2)}</td>
              <td><OrderStatus status={o.status} /></td>
              <td>{o.reason ? <span className="reason-chip" title={reasonText(o.reason)}>{o.reason}</span> : "—"}</td>
              <td className="num">{trimDecimal(o.estimated_fee, 4)}</td><td className="num">{trimDecimal(o.estimated_slippage, 4)}</td>
            </tr>
          ))}
            {orders.length === 0 && <tr><td colSpan={12} className="muted">No simulated orders yet.</td></tr>}</tbody>
        </table></div>
        <p className="muted table-cap">{orders.length.toLocaleString("en-US")} order{orders.length === 1 ? "" : "s"} shown (newest first, up to 300).</p>
      </section>
    </>
  );
}

export default function PaperOrdersPage() {
  return <PaperShell active="/paper/orders">{({ sid }) => <Body sid={sid} />}</PaperShell>;
}
