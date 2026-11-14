"use client";
import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Sparkline } from "../../components/Charts";
import { Ladder, Price, SpreadBars, Ticker, VenueBands } from "../../components/MarketWidgets";
import PriceChart, { type PriceLine } from "../../components/PriceChart";
import { type Market } from "../../lib/api";
import { DATA_DISCLAIMER, showDecimal, sourceLabel } from "../../lib/format";
import { RANGE_MS, record, refSeries, series, sessionChange, type Range } from "../../lib/marketHistory";
import { ageText, extremeSpread, gapTone, groupBySymbol, sourceSummary, type SymbolGroup, type Tone } from "../../lib/markets";
import { usePoll } from "../../lib/usePoll";
import Shell from "../Shell";

function tone(source: string): string {
  if (source === "LIVE") return "ok";
  if (source === "FIXTURE") return "warn";
  return "bad";
}

function Skeleton({ h }: { h: number }) {
  return <div className="skeleton" style={{ height: h }} aria-busy="true" />;
}

function Tile({ label, value, sub, pill, children }: { label: string; value: string; sub: string; pill?: Tone; children?: ReactNode }) {
  return (
    <div className="card kpi">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">{pill ? <span className={`pill ${pill} big`}>{value}</span> : value}</div>
      {children}
      <div className="kpi-sub">{sub}</div>
    </div>
  );
}

const LINE_TONES = ["accent", "violet", "ok", "warn"] as const;
const RANGES: { id: Range; label: string }[] = [
  { id: "1m", label: "1 min" },
  { id: "5m", label: "5 min" },
  { id: "all", label: "Session" },
];

function Change({ s }: { s: ReturnType<typeof sessionChange> }) {
  if (!s) return <span className="chg flat">—</span>;
  return <span className={`chg ${s.dir}`}>{s.text}</span>;
}

export default function MarketsPage() {
  const { data, error } = usePoll<Market[]>("/v1/markets", 3000);
  const rows = data ?? null;
  const groups = useMemo(() => groupBySymbol(rows ?? []), [rows]);
  const [now, setNow] = useState(() => Date.now());
  const [picked, setPicked] = useState<string | null>(null);
  const [range, setRange] = useState<Range>("all");

  useEffect(() => {
    if (!rows) return;
    const t = Date.now();
    record(rows, t); // browser-side session history (module store); stale quotes leave gaps
    setNow(t);
  }, [rows]);

  const symbol = picked && groups.some((g) => g.symbol === picked) ? picked : groups[0]?.symbol ?? null;
  const g: SymbolGroup | null = groups.find((x) => x.symbol === symbol) ?? null;

  const total = rows?.length ?? 0;
  const fresh = (rows ?? []).filter((m) => !m.stale && m.bid !== null).length;
  const src = sourceSummary(rows ?? []);
  const tight = extremeSpread(rows ?? [], "min");
  const widest = groups.filter((x) => x.gap).sort((a, b) => (b.gap?.value ?? 0) - (a.gap?.value ?? 0))[0] ?? null;
  const latest = (rows ?? []).map((m) => m.last_ingested).filter((x): x is string => !!x).sort().at(-1) ?? null;

  const lines: PriceLine[] = g
    ? g.quotes.map((m, i) => ({ name: m.venue, tone: LINE_TONES[i % LINE_TONES.length], samples: series(m.venue, g.symbol, RANGE_MS[range], now) }))
    : [];
  const gChange = g ? sessionChange(refSeries(g.symbol, g.quotes.map((m) => m.venue))) : null;
  const gNewest = g ? g.quotes.map((m) => m.last_ingested).filter((x): x is string => !!x).sort().at(-1) ?? null : null;

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
          <Skeleton h={44} />
          <div className="grid kpis"><Skeleton h={120} /><Skeleton h={120} /><Skeleton h={120} /><Skeleton h={120} /></div>
          <Skeleton h={420} />
        </>
      ) : (
        <>
          <Ticker rows={rows} />

          <div className="grid kpis">
            <Tile label="Fresh quotes" value={`${fresh} / ${total}`} sub="stale quotes suspend new simulated buys">
              <div className="meter" role="presentation"><div className={`meter-fill ${fresh === total ? "ok" : fresh === 0 ? "bad" : "warn"}`} style={{ width: `${total ? Math.max(2, Math.round((fresh / total) * 100)) : 2}%` }} /></div>
            </Tile>
            <Tile label="Data source" value={src.title} sub={src.detail} pill={src.tone} />
            <Tile label="Tightest spread" value={tight ? `${showDecimal(tight.spread_bps)} bps` : "—"} sub={tight ? `${tight.symbol} on ${tight.venue}` : "no spread available"} />
            <Tile label="Largest venue gap" value={widest?.gap ? `${widest.gap.text} bps` : "—"} sub={widest ? `${widest.symbol}: venue mid prices differ` : "needs two fresh venues"} />
          </div>

          <div className="mk-terminal">
            <aside className="card wl" aria-label="Watchlist">
              <h2 className="card-title">Watchlist</h2>
              <div className="wl-list" role="radiogroup" aria-label="Symbol">
                {groups.map((x) => {
                  const ref = refSeries(x.symbol, x.quotes.map((m) => m.venue));
                  return (
                    <label key={x.symbol} className="wl-item">
                      <input type="radio" name="symbol" value={x.symbol} checked={x.symbol === symbol} onChange={() => setPicked(x.symbol)} />
                      <span className="coin" aria-hidden="true">{x.base.slice(0, 3)}</span>
                      <span className="wl-main">
                        <span className="wl-name">{x.symbol.replace("-", "/")}</span>
                        <span className="muted wl-sub">{x.fresh.length}/{x.quotes.length} venues fresh</span>
                      </span>
                      <span className="wl-right">
                        <Price value={x.refMid} className="wl-px" />
                        <Change s={sessionChange(ref)} />
                      </span>
                      <span className="wl-spark">{ref.length >= 2 ? <Sparkline points={ref.map((s) => ({ t: s.t, y: s.y }))} label={`${x.symbol} mid price this session`} height={30} /> : null}</span>
                    </label>
                  );
                })}
              </div>
            </aside>

            <section className="card mk-chart" aria-label="Price chart">
              {g ? (
                <>
                  <div className="mk-head">
                    <div className="mk-id">
                      <span className="coin lg" aria-hidden="true">{g.base.slice(0, 3)}</span>
                      <div>
                        <h2 className="card-title mk-sym">{g.symbol.replace("-", "/")}</h2>
                        <div className="muted mk-sub">{g.refMid ? `mid, average of ${g.fresh.length} fresh venue${g.fresh.length === 1 ? "" : "s"}` : "no fresh quote: no price shown"} · updated {ageText(gNewest, now)}</div>
                      </div>
                    </div>
                    <div className="mk-price-box">
                      <Price value={g.refMid} className="mk-price" />
                      <div className="mk-chips">
                        <Change s={gChange} />
                        {g.gap && <span className={`pill ${gapTone(g.gap.value)}`} title="Difference between the venues' mid prices">gap {g.gap.text} bps</span>}
                      </div>
                    </div>
                  </div>
                  <div className="mk-toolbar">
                    <div className="seg" role="radiogroup" aria-label="Time range">
                      {RANGES.map((r) => (
                        <label key={r.id}><input type="radio" name="range" value={r.id} checked={range === r.id} onChange={() => setRange(r.id)} /><span>{r.label}</span></label>
                      ))}
                    </div>
                    <span className="muted mk-note">session history, recorded in this browser tab</span>
                  </div>
                  <PriceChart lines={lines} label={`${g.symbol} mid price per venue`} />
                  <div className="legend">
                    {lines.map((l) => <span key={l.name} className="legend-item"><i className={`swatch ${l.tone}`} />{l.name}</span>)}
                  </div>
                </>
              ) : (
                <p className="muted">No symbols yet.</p>
              )}
            </section>

            <div className="mk-side">
              <section className="card">
                <h2 className="card-title">Top of book</h2>
                {g ? <Ladder quotes={g.quotes} /> : null}
              </section>
              <section className="card">
                <h2 className="card-title">Venue alignment</h2>
                {g ? <VenueBands quotes={g.quotes} /> : null}
                <p className="muted footnote">Each band spans a venue&rsquo;s bid to ask. Overlapping bands mean the venues agree.</p>
              </section>
            </div>
          </div>

          <section className="card">
            <h2 className="card-title">Spread comparison</h2>
            <SpreadBars rows={rows} />
          </section>

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
