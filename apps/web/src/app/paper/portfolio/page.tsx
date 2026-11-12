"use client";
import PaperShell from "../../../components/PaperShell";
import { trimDecimal } from "../../../lib/format";
import type { PaperPortfolio } from "../../../lib/paper";
import { usePoll } from "../../../lib/usePoll";

function Body({ sid }: { sid: string | null }) {
  const pf = usePoll<PaperPortfolio>(sid ? `/v1/paper/sessions/${sid}/portfolio` : "/v1/paper/health", 5000);
  const p = sid ? (pf.data as PaperPortfolio | null) : null;
  if (!p) return <p className="muted">No portfolio yet.</p>;
  return (
    <>
      <h1>Paper Portfolio</h1>
      <div className="grid">
        <div className="card" data-testid="pf-totals">
          <h3>Total virtual equity</h3>
          <p><strong data-testid="pf-equity">{trimDecimal(p.totals.equity)}</strong> USDT</p>
          <p className="muted">Cash {trimDecimal(p.totals.cash)} · inventory {trimDecimal(p.totals.inventory_value)} · realised {trimDecimal(p.totals.realized_pnl)} · unrealised {trimDecimal(p.totals.unrealized_pnl)}</p>
          {Number(p.idle_wallet_cash) > 0 && <p className="warnbox">Idle virtual cash on venues without a verified feed: {trimDecimal(p.idle_wallet_cash)} USDT</p>}
        </div>
        <div className="card" data-testid="pf-exposure">
          <h3>Exposure (marked inventory)</h3>
          <p className="muted">By venue: {Object.entries(p.exposure_by_venue).map(([k, v]) => `${k} ${trimDecimal(v)}`).join(" · ") || "none"}</p>
          <p className="muted">By asset: {Object.entries(p.exposure_by_asset).map(([k, v]) => `${k} ${trimDecimal(v)}`).join(" · ") || "none"}</p>
        </div>
      </div>
      <div className="card scroll">
        <table data-testid="pf-accounts">
          <thead><tr><th>Account (venue : strategy)</th><th>Available cash</th><th>Reserved cash</th><th>Assets (qty · cost · mark · unrealised)</th><th>Realised</th><th>Equity</th></tr></thead>
          <tbody>{Object.entries(p.accounts).map(([k, a]) => (
            <tr key={k}>
              <td>{k}</td><td>{trimDecimal(a.cash_available)}</td><td>{trimDecimal(a.cash_reserved)}</td>
              <td>{a.assets.length === 0 ? "—" : a.assets.map((x) => `${x.asset} ${x.quantity} · cost ${trimDecimal(x.cost_basis)} · mark ${x.mark ?? "—"} · ${trimDecimal(x.unrealized_pnl)}`).join(" | ")}</td>
              <td>{trimDecimal(a.realized_pnl)}</td><td>{trimDecimal(a.equity)}</td>
            </tr>
          ))}</tbody>
        </table>
      </div>
      <p className="muted">Each row is a simulated wallet slice. Shadow benchmark accounts are not part of the portfolio.</p>
    </>
  );
}

export default function PaperPortfolioPage() {
  return <PaperShell active="/paper/portfolio">{({ sid }) => <Body sid={sid} />}</PaperShell>;
}
