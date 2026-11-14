"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { Sparkline, type Pt } from "../../components/Charts";
import { type Market } from "../../lib/api";
import { DATA_DISCLAIMER, showDecimal, sourceLabel } from "../../lib/format";
import {
  ageText, extremeSpread, gapTone, groupBySymbol, sourceSummary, spreadShare, type SymbolGroup, type Tone,
} from "../../lib/markets";
import { usePoll } from "../../lib/usePoll";
import Shell from "../Shell";

function tone(source: string): string {
  if (source === "LIVE") return "ok";
  if (source === "FIXTURE") return "warn";
  return "bad";
}

function shortTag(m: Market): { text: string; tone: string } {
  if (m.stale) return { text: "stale", tone: "bad" };
  if (m.source === "LIVE") return { text: "real-time", tone: "ok" };
  if (m.source === "FIXTURE") return { text: "fixture", tone: "warn" };
  return { text: "no data", tone: "bad" };
}

function Skeleton({ h }: { h: number }) {
  return <div className="skeleton" style={{ height: h }} aria-busy="true" />;
}

function Tile({ label, value, sub, pill, children }: { label: string; value: string; sub: string; pill?: Tone; children?: React.ReactNode }) {
  return (
    <div className="card kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{pill ? <span className={`pill ${pill} big`}>{value}</span> : value}</div>
      {children}
      <div className="kpi-sub">{sub}</div>
    </div>
  );
}

function SymbolCard({ g, history, now, all }: { g: SymbolGroup; history: Pt[]; now: number; all: Market[] }) {
  return (
    <article className="card sym-card" data-testid="symbol-card">
      <div className="sym-head">
        <span className="coin" aria-hidden="true">{g.base.slice(0, 3)}</span>
        <div className="sym-title">
          <span className="sym-name">{g.symbol.replace("-", "/")}</span>
          <span className="muted sym-sub">{g.fresh.length} of {g.quotes.length} venues fresh</span>
        </div>
        {g.gap && (
          <span className={`pill ${gapTone(g.gap.value)}`} title="Difference between the venues' mid prices">
            gap {g.gap.text} bps
          </span>
        )}
      </div>
      <div className="ref-px">
        <span className="ref-val">{g.refMid ?? "—"}</span>
        <span className="muted ref-lbl">{g.refMid ? "mid price, average of fresh venues" : "no fresh quote: no price shown"}</span>
      </div>
      {history.length >= 2 ? (
        <>
          <Sparkline points={history} label={`${g.symbol} mid price since this page was opened`} height={40} />
          <div className="muted spark-cap">since you opened this page</div>
        </>
      ) : (
        <div className="muted spark-cap">collecting price history…</div>
      )}
      <ul className="vrows">
        {g.quotes.map((m) => {
          const t = shortTag(m);
          return (
            <li key={m.venue} className="vrow">
              <div className="vrow-top">
                <span className="vrow-venue">{m.venue}</span>
                <span className={`pill ${t.tone}`}>{t.text}</span>
              </div>
              <div className="vrow-px">
                <span><span className="muted k">bid</span> {showDecimal(m.bid)}</span>
                <span><span className="muted k">ask</span> {showDecimal(m.ask)}</span>
              </div>
              <div className="vrow-spread" title="Spread in basis points, relative to the widest spread on this page">
                <div className="spread-bar">
                  {m.spread_bps !== null && <div style={{ width: `${Math.max(3, Math.round(spreadShare(m.spread_bps, all) * 100))}%` }} />}
                </div>
                <span className="muted">
                  {m.spread_bps !== null ? `${m.spread_bps} bps · ${ageText(m.last_ingested, now)}` : "no quote"}
                </span>
              </div>
            </li>
          );
        })}
      </ul>
    </article>
  );
}

export default function MarketsPage() {
  const { data, error } = usePoll<Market[]>("/v1/markets", 3000);
  const rows = data ?? null;
  const groups = useMemo(() => groupBySymbol(rows ?? []), [rows]);
  const [now, setNow] = useState(() => Date.now());
  const hist = useRef<Record<string, Pt[]>>({});
  const [, bump] = useState(0);

  useEffect(() => {
    if (!rows) return;
    const t = Date.now();
    setNow(t);
    for (const g of groupBySymbol(rows)) {
      if (g.refMid === null) continue;
      const arr = (hist.current[g.symbol] ??= []);
      arr.push({ t, y: Number(g.refMid) }); // chart geometry only; the printed price stays an exact string
      if (arr.length > 90) arr.shift();
    }
    bump((n) => n + 1);
  }, [rows]);

  const total = rows?.length ?? 0;
  const fresh = (rows ?? []).filter((m) => !m.stale && m.bid !== null).length;
  const src = sourceSummary(rows ?? []);
  const tight = extremeSpread(rows ?? [], "min");
  const widest = groups.filter((g) => g.gap).sort((a, b) => (b.gap?.value ?? 0) - (a.gap?.value ?? 0))[0] ?? null;
  const latest = (rows ?? []).map((m) => m.last_ingested).filter((x): x is string => !!x).sort().at(-1) ?? null;

  return (
    <Shell>
      <div className="page-head">
        <div>
          <h1>Markets</h1>
          <p className="muted">Allowlist: BTC/ETH/SOL vs USDT. Public data only. Unknown or old data is shown as stale.</p>
        </div>
        {latest && (
          <span className="updated" title="Time of the newest quote received">
            <span className="dot" aria-hidden="true" />
            Newest quote {latest.slice(11, 19)} UTC
          </span>
        )}
      </div>
      {error && <p className="err">{error}</p>}
      {!rows ? (
        <>
          <div className="grid kpis"><Skeleton h={120} /><Skeleton h={120} /><Skeleton h={120} /><Skeleton h={120} /></div>
          <Skeleton h={320} />
        </>
      ) : (
        <>
          <div className="grid kpis">
            <Tile label="Fresh quotes" value={`${fresh} / ${total}`} sub="stale quotes suspend new simulated buys">
              <div className="meter" role="presentation"><div className={`meter-fill ${fresh === total ? "ok" : fresh === 0 ? "bad" : "warn"}`} style={{ width: `${total ? Math.max(2, Math.round((fresh / total) * 100)) : 2}%` }} /></div>
            </Tile>
            <Tile label="Data source" value={src.title} sub={src.detail} pill={src.tone} />
            <Tile label="Tightest spread" value={tight ? `${showDecimal(tight.spread_bps)} bps` : "—"} sub={tight ? `${tight.symbol} on ${tight.venue}` : "no spread available"} />
            <Tile label="Largest venue gap" value={widest?.gap ? `${widest.gap.text} bps` : "—"} sub={widest ? `${widest.symbol}: venue mid prices differ` : "needs two fresh venues"} />
          </div>

          <h2>Quotes by symbol</h2>
          <div className="grid sym-grid">
            {groups.map((g) => (
              <SymbolCard key={g.symbol} g={g} history={hist.current[g.symbol] ?? []} now={now} all={rows} />
            ))}
          </div>

          <h2>All quotes</h2>
          <div className="card scroll">
            <table className="qtable">
              <thead>
                <tr>
                  <th>Venue</th>
                  <th>Symbol</th>
                  <th className="num">Bid</th>
                  <th className="num">Ask</th>
                  <th className="num">Spread (bps)</th>
                  <th>Last update (UTC)</th>
                  <th>Source</th>
                  <th>Fresh</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((m) => (
                  <tr key={`${m.venue}-${m.symbol}`} data-testid="market-row">
                    <td data-label="Venue">{m.venue}</td>
                    <td data-label="Symbol">{m.symbol}</td>
                    <td data-label="Bid" className="num">{showDecimal(m.bid)}</td>
                    <td data-label="Ask" className="num">{showDecimal(m.ask)}</td>
                    <td data-label="Spread (bps)" className="num">{showDecimal(m.spread_bps)}</td>
                    <td data-label="Last update (UTC)">{m.last_ingested ? m.last_ingested.slice(11, 19) : "—"}</td>
                    <td data-label="Source">
                      <span className={`pill ${tone(m.source)}`}>{sourceLabel(m.source)}</span>
                    </td>
                    <td data-label="Fresh">
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
          <p className="muted footnote" data-testid="data-disclaimer">{DATA_DISCLAIMER}</p>
        </>
      )}
    </Shell>
  );
}
