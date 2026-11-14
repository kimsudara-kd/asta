"use client";
import PaperShell from "../../../components/PaperShell";
import { Signed, Skeleton, StackBar } from "../../../components/paper/ui";
import { trimDecimal } from "../../../lib/format";
import type { PaperPortfolio } from "../../../lib/paper";
import { usePoll } from "../../../lib/usePoll";

const TONES = ["accent", "violet", "ok", "warn", "muted"] as const;

/** share of `part` in `whole` for BAR GEOMETRY ONLY; every printed figure stays an exact decimal string. */
const share = (part: string, whole: number): number => (whole > 0 ? Math.max(0, Number(part)) / whole : 0);

function Body({ sid }: { sid: string | null }) {
  const pf = usePoll<PaperPortfolio>(sid ? `/v1/paper/sessions/${sid}/portfolio` : "/v1/paper/health", 5000);
  const p = sid ? (pf.data as PaperPortfolio | null) : null;
  if (!p) return sid ? <><h1>Paper Portfolio</h1><div className="grid kpis"><Skeleton h={160} /><Skeleton h={160} /></div></> : <p className="muted">No portfolio yet.</p>;
  const eq = Number(p.totals.equity);
  const venues = Object.entries(p.exposure_by_venue);
  const assets = Object.entries(p.exposure_by_asset);
  const invested = venues.reduce((n, [, v]) => n + Number(v), 0);
  const accounts = Object.entries(p.accounts);
  const maxEq = Math.max(1, ...accounts.map(([, a]) => Number(a.equity)));
  return (
    <>
      <h1>Paper Portfolio</h1>
      <div className="grid pf-top">
        <section className="card" data-testid="pf-totals">
          <h2 className="card-title">Total virtual equity</h2>
          <p className="pf-eq"><strong data-testid="pf-equity">{trimDecimal(p.totals.equity)}</strong> <span className="unit">USDT</span></p>
          <StackBar parts={[
            { label: "Cash", value: trimDecimal(p.totals.cash), share: share(p.totals.cash, eq), tone: "accent" },
            { label: "Inventory (marked)", value: trimDecimal(p.totals.inventory_value), share: share(p.totals.inventory_value, eq), tone: "violet" },
          ]} />
          <div className="pnl-chips">
            <span className="pnl"><span className="muted">Realised</span><Signed value={p.totals.realized_pnl}>{trimDecimal(p.totals.realized_pnl)}</Signed></span>
            <span className="pnl"><span className="muted">Unrealised</span><Signed value={p.totals.unrealized_pnl}>{trimDecimal(p.totals.unrealized_pnl)}</Signed></span>
          </div>
          <p className="muted pf-plain">Cash {trimDecimal(p.totals.cash)} · inventory {trimDecimal(p.totals.inventory_value)} · realised {trimDecimal(p.totals.realized_pnl)} · unrealised {trimDecimal(p.totals.unrealized_pnl)}</p>
          {Number(p.idle_wallet_cash) > 0 && <p className="warnbox">Idle virtual cash on venues without a verified feed: {trimDecimal(p.idle_wallet_cash)} USDT</p>}
        </section>
        <section className="card" data-testid="pf-exposure">
          <h2 className="card-title">Exposure (marked inventory)</h2>
          {venues.length === 0 && assets.length === 0 ? <p className="muted">No open exposure.</p> : (
            <>
              <h3 className="sub-h">By venue</h3>
              <StackBar parts={venues.map(([k, v], i) => ({ label: k, value: trimDecimal(v), share: share(v, invested), tone: TONES[i % TONES.length] }))} />
              <h3 className="sub-h">By asset</h3>
              <StackBar parts={assets.map(([k, v], i) => ({ label: k, value: trimDecimal(v), share: share(v, invested), tone: TONES[i % TONES.length] }))} />
            </>
          )}
        </section>
      </div>
      <section className="card">
        <h2 className="card-title">Accounts</h2>
        <div className="scroll">
          <table data-testid="pf-accounts">
            <thead><tr><th>Account (venue : strategy)</th><th className="num">Available cash</th><th className="num">Reserved cash</th><th>Assets (qty · cost · mark · unrealised)</th><th className="num">Realised</th><th>Equity</th></tr></thead>
            <tbody>{accounts.map(([k, a]) => (
              <tr key={k}>
                <td className="acct">{k}</td>
                <td className="num">{trimDecimal(a.cash_available)}</td>
                <td className="num">{trimDecimal(a.cash_reserved)}</td>
                <td>{a.assets.length === 0 ? <span className="muted">—</span> : <div className="asset-chips">{a.assets.map((x) => <span key={x.asset} className="asset-chip"><b>{x.asset}</b> {trimDecimal(x.quantity, 6)} · cost {trimDecimal(x.cost_basis)} · mark {x.mark === null ? "—" : trimDecimal(x.mark)} · <Signed value={x.unrealized_pnl}>{trimDecimal(x.unrealized_pnl)}</Signed></span>)}</div>}</td>
                <td className="num"><Signed value={a.realized_pnl}>{trimDecimal(a.realized_pnl)}</Signed></td>
                <td className="eqcell"><span>{trimDecimal(a.equity)}</span><div className="eqbar"><div style={{ width: `${Math.max(2, Math.round((Number(a.equity) / maxEq) * 100))}%` }} /></div></td>
              </tr>
            ))}</tbody>
          </table>
        </div>
      </section>
      <p className="muted">Each row is a simulated wallet slice. Shadow benchmark accounts are not part of the portfolio.</p>
    </>
  );
}

export default function PaperPortfolioPage() {
  return <PaperShell active="/paper/portfolio">{({ sid }) => <Body sid={sid} />}</PaperShell>;
}
