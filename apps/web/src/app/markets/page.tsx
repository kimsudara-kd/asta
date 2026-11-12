"use client";
import { type Market } from "../../lib/api";
import { DATA_DISCLAIMER, showDecimal, sourceLabel } from "../../lib/format";
import { usePoll } from "../../lib/usePoll";
import Shell from "../Shell";

function tone(source: string): string {
  if (source === "LIVE") return "ok";
  if (source === "FIXTURE") return "warn";
  return "bad";
}

export default function MarketsPage() {
  const { data, error } = usePoll<Market[]>("/v1/markets", 3000);
  return (
    <Shell>
      <h1>Markets</h1>
      <p className="muted">
        Allowlist: BTC/ETH/SOL vs USDT. Public data only. Unknown or old data is shown as stale.
      </p>
      <p className="muted" data-testid="data-disclaimer">
        {DATA_DISCLAIMER}
      </p>
      {error && <p className="err">{error}</p>}
      <div className="card scroll">
        <table>
          <thead>
            <tr>
              <th>Venue</th>
              <th>Symbol</th>
              <th>Bid</th>
              <th>Ask</th>
              <th>Spread (bps)</th>
              <th>Last update (UTC)</th>
              <th>Source</th>
              <th>Fresh</th>
            </tr>
          </thead>
          <tbody>
            {(data ?? []).map((m) => (
              <tr key={`${m.venue}-${m.symbol}`} data-testid="market-row">
                <td>{m.venue}</td>
                <td>{m.symbol}</td>
                <td>{showDecimal(m.bid)}</td>
                <td>{showDecimal(m.ask)}</td>
                <td>{showDecimal(m.spread_bps)}</td>
                <td>{m.last_ingested ? m.last_ingested.slice(11, 19) : "—"}</td>
                <td>
                  <span className={`pill ${tone(m.source)}`}>
                    {sourceLabel(m.source)}
                  </span>
                </td>
                <td>
                  {m.stale ? (
                    <span className="pill bad" data-testid="stale-pill">STALE</span>
                  ) : (
                    <span className="pill ok">fresh</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Shell>
  );
}
