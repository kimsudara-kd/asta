// Pure helpers for the Markets page. Prices are exact Decimal STRINGS handled as scaled BigInt; Number() is used
// only for chart geometry and bar widths, never for a figure that is printed.
import type { Market } from "./api";
import { scaled } from "./overview.ts";

const SCALE = 12n;
const ONE = 10n ** SCALE;

function decimals(s: string): number {
  const i = s.indexOf(".");
  return i < 0 ? 0 : s.length - i - 1;
}

/** Scaled BigInt (SCALE fractional digits) -> decimal text truncated toward zero to `dp` places. */
export function fromScaled(v: bigint, dp: number): string {
  const neg = v < 0n;
  const abs = neg ? -v : v;
  const cut = 10n ** (SCALE - BigInt(dp));
  const t = abs / cut;
  const f = 10n ** BigInt(dp);
  const whole = t / f;
  const frac = (t % f).toString().padStart(dp, "0");
  return `${neg && t !== 0n ? "-" : ""}${whole}${dp > 0 ? "." + frac : ""}`;
}

/** Exact mid price of a quote, with as many decimals as the inputs carry. null if either side is missing. */
export function midpoint(bid: string | null, ask: string | null): string | null {
  const b = scaled(bid);
  const a = scaled(ask);
  if (b === null || a === null || bid === null || ask === null) return null;
  return fromScaled((a + b) / 2n, Math.min(8, Math.max(decimals(bid), decimals(ask))));
}

/** Average of several exact prices (display text), or null when there are none. */
export function averagePrice(prices: string[]): string | null {
  const vals = prices.map((p) => ({ p, v: scaled(p) })).filter((x): x is { p: string; v: bigint } => x.v !== null);
  if (vals.length === 0) return null;
  const sum = vals.reduce((n, x) => n + x.v, 0n);
  return fromScaled(sum / BigInt(vals.length), Math.min(8, Math.max(...vals.map((x) => decimals(x.p)))));
}

/** |a - b| relative to their mean, in basis points, exact to 2 places. text e.g. "1.25", value for tone logic. */
export function gapBps(a: string | null, b: string | null): { text: string; value: number } | null {
  const x = scaled(a);
  const y = scaled(b);
  if (x === null || y === null || x + y === 0n) return null;
  const diff = x > y ? x - y : y - x;
  const q = (diff * 2n * 10000n * 100n) / (x + y); // |a-b| / ((a+b)/2) * 10000, scaled by 100
  const whole = q / 100n;
  const frac = (q % 100n).toString().padStart(2, "0");
  return { text: `${whole}.${frac}`, value: Number(q) / 100 };
}

export type SymbolGroup = {
  symbol: string;
  base: string;
  quotes: Market[];
  fresh: Market[]; // real or fixture quotes that are not stale
  refMid: string | null; // average mid of the fresh venues
  gap: { text: string; value: number } | null; // between the first two fresh venues
};

/** Group raw quotes by symbol (first-seen order), with a reference mid and the cross-venue gap. */
export function groupBySymbol(rows: Market[]): SymbolGroup[] {
  const order: string[] = [];
  const by = new Map<string, Market[]>();
  for (const m of rows) {
    if (!by.has(m.symbol)) { by.set(m.symbol, []); order.push(m.symbol); }
    by.get(m.symbol)!.push(m);
  }
  return order.map((symbol) => {
    const quotes = by.get(symbol)!;
    const fresh = quotes.filter((q) => !q.stale && q.bid !== null && q.ask !== null);
    const mids = fresh.map((q) => midpoint(q.bid, q.ask)).filter((x): x is string => x !== null);
    return {
      symbol,
      base: symbol.split("-")[0],
      quotes,
      fresh,
      refMid: averagePrice(mids),
      gap: mids.length >= 2 ? gapBps(mids[0], mids[1]) : null,
    };
  });
}

/** The quote with the smallest / largest spread (exact comparison), ignoring quotes without one. */
export function extremeSpread(rows: Market[], which: "min" | "max"): Market | null {
  let best: { m: Market; v: bigint } | null = null;
  for (const m of rows) {
    const v = scaled(m.spread_bps);
    if (v === null) continue;
    if (best === null || (which === "min" ? v < best.v : v > best.v)) best = { m, v };
  }
  return best?.m ?? null;
}

/** 0..1 width of a spread bar relative to the widest spread in the set (geometry only). */
export function spreadShare(spread: string | null, rows: Market[]): number {
  const v = scaled(spread);
  const max = rows.map((m) => scaled(m.spread_bps)).filter((x): x is bigint => x !== null).reduce((a, b) => (b > a ? b : a), 0n);
  if (v === null || max === 0n) return 0;
  return Number((v * 1000n) / max) / 1000;
}

export type Tone = "ok" | "warn" | "bad";
export function gapTone(valueBps: number): Tone {
  return valueBps < 5 ? "ok" : valueBps < 25 ? "warn" : "bad";
}

/** "3 s ago" / "2 min ago" / "—" for a missing time. Display only. */
export function ageText(iso: string | null, nowMs: number): string {
  if (!iso) return "—";
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const s = Math.max(0, Math.round((nowMs - t) / 1000));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  return `${Math.floor(s / 3600)} h ago`;
}

/** One summary of where the quotes come from, never calling fixtures "real". */
export function sourceSummary(rows: Market[]): { title: string; detail: string; tone: Tone } {
  const live = rows.filter((m) => m.source === "LIVE" && !m.stale).length;
  const fixture = rows.filter((m) => m.source === "FIXTURE").length;
  const none = rows.filter((m) => m.source !== "LIVE" && m.source !== "FIXTURE").length;
  if (rows.length === 0) return { title: "Waiting for data", detail: "No quotes received yet.", tone: "warn" };
  if (none === rows.length) return { title: "No data", detail: "No venue is delivering quotes.", tone: "bad" };
  if (fixture === rows.length) return { title: "Fixture only", detail: "Synthetic test prices, not real markets.", tone: "warn" };
  if (live === rows.length) return { title: "Real-time public data", detail: "Read-only observation; trading is disabled.", tone: "ok" };
  return { title: "Mixed sources", detail: `${live} real-time, ${fixture} fixture, ${none} without data.`, tone: "warn" };
}

/** Spread between the best bid and the best ask in bps of their mean, exact to 2 places; negative = crossed. */
export function crossSpread(bid: string | null, ask: string | null): { text: string; crossed: boolean } | null {
  const b = scaled(bid);
  const a = scaled(ask);
  if (b === null || a === null || a + b === 0n) return null;
  const q = ((a - b) * 2n * 10000n * 100n) / (a + b);
  const neg = q < 0n;
  const abs = neg ? -q : q;
  return { text: `${neg && abs !== 0n ? "-" : ""}${abs / 100n}.${(abs % 100n).toString().padStart(2, "0")}`, crossed: neg };
}

export type Level = { venue: string; price: string; source: string };
export type Ladder = { asks: Level[]; bids: Level[]; bestBid: Level | null; bestAsk: Level | null; spread: { text: string; crossed: boolean } | null };

/** Combined top of book across venues (fresh quotes only): asks high->low, bids high->low, best bid/ask. */
export function ladderLevels(quotes: Market[]): Ladder {
  const fresh = quotes.filter((m) => !m.stale && m.bid !== null && m.ask !== null);
  const byPriceDesc = (a: Level, b: Level) => {
    const x = scaled(a.price)!;
    const y = scaled(b.price)!;
    return x === y ? 0 : x > y ? -1 : 1;
  };
  const asks = fresh.map((m) => ({ venue: m.venue, price: m.ask as string, source: m.source })).filter((l) => scaled(l.price) !== null).sort(byPriceDesc);
  const bids = fresh.map((m) => ({ venue: m.venue, price: m.bid as string, source: m.source })).filter((l) => scaled(l.price) !== null).sort(byPriceDesc);
  const bestAsk = asks.length ? asks[asks.length - 1] : null; // lowest ask
  const bestBid = bids.length ? bids[0] : null; // highest bid
  return { asks, bids, bestBid, bestAsk, spread: crossSpread(bestBid?.price ?? null, bestAsk?.price ?? null) };
}
