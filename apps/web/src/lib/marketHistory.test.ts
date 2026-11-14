import assert from "node:assert/strict";
import test from "node:test";
import { MAX_SAMPLES, nearest, record, refSeries, resetHistory, series, sessionChange } from "./marketHistory.ts";

const q = (o: Record<string, unknown> = {}) =>
  ({ venue: "BINANCE", symbol: "BTC-USDT", bid: "100.00", ask: "100.10", spread_bps: "1", last_ingested: null, stale: false, source: "LIVE", ...o });

test("record keeps one exact mid per fresh quote and leaves gaps for stale or one-sided quotes", () => {
  resetHistory();
  const n = record([q(), q({ venue: "OKX", bid: "101.00", ask: "101.10" }), q({ symbol: "ETH-USDT", stale: true }), q({ symbol: "SOL-USDT", bid: null })] as never[], 1000);
  assert.equal(n, 2);
  assert.equal(series("BINANCE", "BTC-USDT")[0].text, "100.05");
  assert.equal(series("OKX", "BTC-USDT")[0].text, "101.05");
  assert.deepEqual(series("BINANCE", "ETH-USDT"), []);
  assert.deepEqual(series("BINANCE", "SOL-USDT"), []);
});

test("two samples are never recorded at the same instant", () => {
  resetHistory();
  record([q()] as never[], 5);
  assert.equal(record([q({ bid: "200.00", ask: "200.10" })] as never[], 5), 0);
  assert.equal(series("BINANCE", "BTC-USDT").length, 1);
});

test("history is capped", () => {
  resetHistory();
  for (let i = 0; i < MAX_SAMPLES + 25; i++) record([q()] as never[], i + 1);
  assert.equal(series("BINANCE", "BTC-USDT").length, MAX_SAMPLES);
  assert.equal(series("BINANCE", "BTC-USDT")[0].t, 26); // the oldest were dropped
});

test("time windows keep only recent samples", () => {
  resetHistory();
  for (const t of [1000, 30_000, 61_000, 120_000]) record([q()] as never[], t);
  assert.equal(series("BINANCE", "BTC-USDT", 60_000, 120_000).length, 2); // 61_000 and 120_000
  assert.equal(series("BINANCE", "BTC-USDT", null, 120_000).length, 4);
});

test("refSeries averages the venues sampled at the same instant, exactly", () => {
  resetHistory();
  record([q(), q({ venue: "OKX", bid: "102.00", ask: "102.10" })] as never[], 10);
  record([q({ bid: "110.00", ask: "110.10" })] as never[], 20); // OKX stale/missing at t=20: only Binance
  const r = refSeries("BTC-USDT", ["BINANCE", "OKX"]);
  assert.deepEqual(r.map((s) => [s.t, s.text]), [[10, "101.05"], [20, "110.05"]]);
});

test("sessionChange is exact and signed; flat stays flat; needs two samples", () => {
  const s = (texts: string[]) => texts.map((text, i) => ({ t: i, y: Number(text), text }));
  assert.equal(sessionChange(s(["100"])), null);
  assert.deepEqual(sessionChange(s(["100", "101"])), { text: "+1.00%", dir: "up" });
  assert.deepEqual(sessionChange(s(["100", "98.5"])), { text: "-1.50%", dir: "down" });
  assert.deepEqual(sessionChange(s(["67000.05", "67000.05"])), { text: "0.00%", dir: "flat" });
  assert.deepEqual(sessionChange(s(["100", "99.999"])), { text: "0.00%", dir: "flat" }); // tiny dip is not "-0.00%"
});

test("nearest picks the closest sample in time", () => {
  const s = [1, 10, 20].map((t) => ({ t, y: t, text: String(t) }));
  assert.equal(nearest(s, 12), 1);
  assert.equal(nearest(s, 100), 2);
  assert.equal(nearest([], 5), -1);
});
