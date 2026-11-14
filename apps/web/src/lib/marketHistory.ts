// Session price history recorded in the BROWSER from the quotes the API already serves (no new data source).
// It lives at module level, so it survives navigating between pages while the tab stays open, and it is
// explicitly labelled "since this tab opened" in the UI. Pure TypeScript (no React) so it is unit-tested.
import type { Market } from "./api";
import { averagePrice, midpoint } from "./markets.ts";
import { pctChange } from "./overview.ts";

export type Sample = { t: number; y: number; text: string }; // y: chart geometry only; text: the exact mid
export const MAX_SAMPLES = 600; // ~30 min at one sample per 3 s

const store = new Map<string, Sample[]>();
const key = (venue: string, symbol: string) => `${venue}|${symbol}`;

export function resetHistory(): void {
  store.clear();
}

/** Record one mid-price sample per FRESH quote. Stale or one-sided quotes leave a gap: nothing is invented. */
export function record(rows: Market[], now: number): number {
  let added = 0;
  for (const m of rows) {
    if (m.stale || m.bid === null || m.ask === null) continue;
    const text = midpoint(m.bid, m.ask);
    if (text === null) continue;
    const k = key(m.venue, m.symbol);
    const arr = store.get(k) ?? [];
    if (arr.length > 0 && arr[arr.length - 1].t === now) continue; // never two samples at the same instant
    arr.push({ t: now, y: Number(text), text });
    if (arr.length > MAX_SAMPLES) arr.shift();
    store.set(k, arr);
    added++;
  }
  return added;
}

export type Range = "1m" | "5m" | "all";
export const RANGE_MS: Record<Range, number | null> = { "1m": 60_000, "5m": 300_000, all: null };

/** One venue's samples for a symbol inside the time window (null = everything recorded). */
export function series(venue: string, symbol: string, windowMs: number | null = null, now = Date.now()): Sample[] {
  const arr = store.get(key(venue, symbol)) ?? [];
  return windowMs === null ? [...arr] : arr.filter((s) => s.t >= now - windowMs);
}

/** Reference series of a symbol: at each instant, the exact average of the venues sampled at that instant. */
export function refSeries(symbol: string, venues: string[], windowMs: number | null = null, now = Date.now()): Sample[] {
  const byT = new Map<number, string[]>();
  for (const v of venues) for (const s of series(v, symbol, windowMs, now)) byT.set(s.t, [...(byT.get(s.t) ?? []), s.text]);
  return [...byT.entries()]
    .sort((a, b) => a[0] - b[0])
    .map(([t, texts]) => ({ t, text: averagePrice(texts) ?? texts[0], y: Number(averagePrice(texts) ?? texts[0]) }));
}

/** Exact % change between the first and last sample ("+0.00%" style, signed). null if fewer than two samples. */
export function sessionChange(samples: Sample[]): { text: string; dir: "up" | "down" | "flat" } | null {
  if (samples.length < 2) return null;
  const raw = pctChange(samples[samples.length - 1].text, samples[0].text);
  if (raw === null) return null;
  const neg = raw.startsWith("-");
  const zero = /^-?0\.0+%$/.test(raw);
  return { text: neg || zero ? raw : `+${raw}`, dir: zero ? "flat" : neg ? "down" : "up" };
}

/** Index of the sample nearest to time t (samples sorted by t), or -1 when empty. */
export function nearest(samples: Sample[], t: number): number {
  if (samples.length === 0) return -1;
  let best = 0;
  for (let i = 1; i < samples.length; i++) if (Math.abs(samples[i].t - t) < Math.abs(samples[best].t - t)) best = i;
  return best;
}
