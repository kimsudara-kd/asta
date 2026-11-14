"use client";
import { useEffect, useRef, useState } from "react";
import type { Market } from "../lib/api";
import { ladderLevels, midpoint, spreadShare } from "../lib/markets";
import { scaled } from "../lib/overview";

const tagOf = (m: Pick<Market, "stale" | "source">): { text: string; tone: "ok" | "warn" | "bad" } =>
  m.stale ? { text: "stale", tone: "bad" } : m.source === "LIVE" ? { text: "real-time", tone: "ok" } : m.source === "FIXTURE" ? { text: "fixture", tone: "warn" } : { text: "no data", tone: "bad" };

/** A price that flashes green/red when it moves (exact comparison of the decimal strings). */
export function Price({ value, className = "" }: { value: string | null; className?: string }) {
  const prev = useRef<string | null>(null);
  const [dir, setDir] = useState<"up" | "down" | "">("");
  useEffect(() => {
    const a = scaled(prev.current);
    const b = scaled(value);
    if (a !== null && b !== null && a !== b) setDir(b > a ? "up" : "down");
    prev.current = value;
  }, [value]);
  return <span key={`${value}-${dir}`} className={`px-flash ${dir} ${className}`}>{value ?? "—"}</span>;
}

/** Scrolling strip of the latest mid prices. Decorative duplicate of the data below, so hidden from assistive tech. */
export function Ticker({ rows }: { rows: Market[] }) {
  const items = rows.map((m) => {
    const t = tagOf(m);
    return { id: `${m.venue}-${m.symbol}`, sym: m.symbol.replace("-", "/"), venue: m.venue, px: !m.stale ? midpoint(m.bid, m.ask) : null, tone: t.tone };
  });
  if (items.length === 0) return null;
  const strip = (suffix: string) => items.map((it) => (
    <span key={`${it.id}-${suffix}`} className="tk-item">
      <i className={`tk-dot ${it.tone}`} />
      <b>{it.sym}</b>
      <span className="tk-px">{it.px ?? "—"}</span>
      <span className="tk-venue">{it.venue}</span>
    </span>
  ));
  return (
    <div className="ticker" aria-hidden="true">
      <div className="tk-track"><div className="tk-set">{strip("a")}</div><div className="tk-set">{strip("b")}</div></div>
    </div>
  );
}

/** Combined top of book across venues: asks (red) above, the spread, bids (green) below. */
export function Ladder({ quotes }: { quotes: Market[] }) {
  const l = ladderLevels(quotes);
  if (!l.bestBid || !l.bestAsk) return <p className="muted">No fresh two-sided quote for this symbol.</p>;
  const best = (side: "ask" | "bid", lv: { venue: string; price: string }) =>
    (side === "ask" ? l.bestAsk : l.bestBid)?.venue === lv.venue && (side === "ask" ? l.bestAsk : l.bestBid)?.price === lv.price;
  return (
    <ul className="ladder" aria-label="Top of book across venues">
      {l.asks.map((a) => (
        <li key={`a-${a.venue}`} className={`lad ask${best("ask", a) ? " best" : ""}`}>
          <span className="lad-side">ask</span><span className="lad-px">{a.price}</span><span className="lad-venue">{a.venue}</span>
        </li>
      ))}
      <li className={`lad spread${l.spread?.crossed ? " crossed" : ""}`}>
        <span>spread</span>
        <b>{l.spread ? `${l.spread.text} bps` : "—"}</b>
        <span>{l.spread?.crossed ? "venues disagree (crossed)" : "best bid to best ask"}</span>
      </li>
      {l.bids.map((b) => (
        <li key={`b-${b.venue}`} className={`lad bid${best("bid", b) ? " best" : ""}`}>
          <span className="lad-side">bid</span><span className="lad-px">{b.price}</span><span className="lad-venue">{b.venue}</span>
        </li>
      ))}
    </ul>
  );
}

/** Each venue's bid-ask band on one shared price axis: shows whether venues overlap or drift apart. */
export function VenueBands({ quotes }: { quotes: Market[] }) {
  const fresh = quotes.filter((m) => !m.stale && m.bid !== null && m.ask !== null);
  if (fresh.length === 0) return null;
  const nums = fresh.map((m) => ({ m, b: Number(m.bid), a: Number(m.ask) })); // geometry only
  const lo = Math.min(...nums.map((n) => n.b)), hi = Math.max(...nums.map((n) => n.a));
  const span = Math.max(hi - lo, Math.abs(hi) * 0.0002, 1e-9);
  const min = lo - span * 0.6, max = hi + span * 0.6;
  const pos = (v: number) => `${(((v - min) / (max - min)) * 100).toFixed(2)}%`;
  const loText = fresh.map((m) => m.bid as string).sort((x, y) => (scaled(x)! < scaled(y)! ? -1 : 1))[0];
  const hiText = fresh.map((m) => m.ask as string).sort((x, y) => (scaled(x)! > scaled(y)! ? -1 : 1))[0];
  return (
    <div className="bands" role="img" aria-label="Bid-ask band of each venue on a shared price axis">
      {nums.map(({ m, b, a }, i) => (
        <div key={m.venue} className="band-row">
          <span className="band-venue">{m.venue}</span>
          <div className="band-track">
            <div className={`band-fill v${i % 2}`} style={{ left: pos(b), width: `max(6px, calc(${pos(a)} - ${pos(b)}))` }} />
          </div>
        </div>
      ))}
      <div className="band-axis"><span>{loText}</span><span className="muted">shared price axis</span><span>{hiText}</span></div>
    </div>
  );
}

/** Bar chart of every venue's quoted spread (bps), tightest first. */
export function SpreadBars({ rows }: { rows: Market[] }) {
  const items = rows.filter((m) => m.spread_bps !== null && !m.stale)
    .sort((a, b) => (scaled(a.spread_bps)! < scaled(b.spread_bps)! ? -1 : 1));
  if (items.length === 0) return <p className="muted">No spreads to compare while quotes are stale or missing.</p>;
  return (
    <ul className="sbars">
      {items.map((m) => (
        <li key={`${m.venue}-${m.symbol}`} className="sbar">
          <span className="sbar-name">{m.symbol.replace("-", "/")}<span className="muted"> · {m.venue}</span></span>
          <div className="sbar-track"><div className="sbar-fill" style={{ width: `${Math.max(2, Math.round(spreadShare(m.spread_bps, rows) * 100))}%` }} /></div>
          <span className="sbar-val">{m.spread_bps} bps</span>
        </li>
      ))}
    </ul>
  );
}
