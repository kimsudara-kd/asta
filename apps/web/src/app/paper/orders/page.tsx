"use client";
import { useState } from "react";
import PaperShell from "../../../components/PaperShell";
import { trimDecimal } from "../../../lib/format";
import type { PaperOrderRow, PaperSignal } from "../../../lib/paper";
import { usePoll } from "../../../lib/usePoll";

function Body({ sid }: { sid: string | null }) {
  const [acc, setAcc] = useState("all");
  const [status, setStatus] = useState("");
  const sig = usePoll<{ signals: PaperSignal[] }>(sid ? `/v1/paper/sessions/${sid}/signals?limit=300${acc === "rej" ? "&accepted=false" : acc === "acc" ? "&accepted=true" : ""}` : "/v1/paper/health", 8000);
  const ord = usePoll<{ orders: PaperOrderRow[] }>(sid ? `/v1/paper/sessions/${sid}/orders?limit=300${status ? `&status=${status}` : ""}` : "/v1/paper/health", 8000);
  if (!sid) return <p className="muted">No session selected.</p>;
  const signals = (sig.data as { signals: PaperSignal[] } | null)?.signals ?? [];
  const orders = (ord.data as { orders: PaperOrderRow[] } | null)?.orders ?? [];
  return (
    <>
      <h1>Signals &amp; Paper Orders</h1>
      <p className="muted">Strategy signals are separate from simulated execution: a signal can be rejected (with a reason code) and then never becomes an order fill.</p>
      <div className="card">
        <h3>Strategy signals</h3>
        <div className="row">
          <label>Show
            <select value={acc} onChange={(e) => setAcc(e.target.value)} data-testid="sig-filter">
              <option value="all">all</option><option value="acc">accepted</option><option value="rej">rejected</option>
            </select>
          </label>
        </div>
        <div className="scroll"><table data-testid="signals-table">
          <thead><tr><th>Step (UTC)</th><th>Account</th><th>Version</th><th>Symbol</th><th>Side</th><th>Type</th><th>Outcome</th><th>Reason</th></tr></thead>
          <tbody>{signals.map((x) => <tr key={`${x.account}-${x.signal_id}`} data-testid="signal-row"><td>{x.step_ts.slice(0, 16).replace("T", " ")}</td><td>{x.account}</td><td>{x.strategy_version}</td><td>{x.symbol}</td><td>{x.side}</td><td>{x.type}</td><td>{x.accepted ? "accepted" : "rejected"}</td><td>{x.reason || "—"}</td></tr>)}
            {signals.length === 0 && <tr><td colSpan={8} className="muted">No signals yet.</td></tr>}</tbody>
        </table></div>
      </div>
      <div className="card">
        <h3>Simulated order history (estimated fills)</h3>
        <div className="row">
          <label>Status
            <select value={status} onChange={(e) => setStatus(e.target.value)} data-testid="ord-filter">
              <option value="">all</option><option>OPEN</option><option>PARTIALLY_FILLED</option><option>FILLED</option><option>CANCELLED</option><option>EXPIRED</option><option>REJECTED</option>
            </select>
          </label>
        </div>
        <div className="scroll"><table data-testid="orders-table">
          <thead><tr><th>Created</th><th>Account</th><th>Symbol</th><th>Side</th><th>Type</th><th>Qty</th><th>Filled</th><th>Limit</th><th>Status</th><th>Reason</th><th>Est. fee</th><th>Est. slippage</th></tr></thead>
          <tbody>{orders.map((o) => <tr key={`${o.account}-${o.order_id}`} data-testid="order-row"><td>{o.created_ts.slice(0, 16).replace("T", " ")}</td><td>{o.account}</td><td>{o.symbol}</td><td>{o.side}</td><td>{o.type}</td><td>{o.quantity}</td><td>{o.filled_qty}</td><td>{o.limit_price ?? "—"}</td><td>{o.status}</td><td>{o.reason || "—"}</td><td>{trimDecimal(o.estimated_fee, 4)}</td><td>{trimDecimal(o.estimated_slippage, 4)}</td></tr>)}
            {orders.length === 0 && <tr><td colSpan={12} className="muted">No simulated orders yet.</td></tr>}</tbody>
        </table></div>
      </div>
    </>
  );
}

export default function PaperOrdersPage() {
  return <PaperShell active="/paper/orders">{({ sid }) => <Body sid={sid} />}</PaperShell>;
}
