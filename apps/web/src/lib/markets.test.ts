import assert from "node:assert/strict";
import test from "node:test";
import { ageText, averagePrice, extremeSpread, fromScaled, gapBps, gapTone, groupBySymbol, midpoint, sourceSummary, spreadShare } from "./markets.ts";

const q = (o: Record<string, unknown> = {}) =>
  ({ venue: "BINANCE", symbol: "BTC-USDT", bid: "67000.00", ask: "67000.10", spread_bps: "0.01", last_ingested: "2026-11-14T08:00:00+00:00", stale: false, source: "FIXTURE", ...o });

test("fromScaled truncates toward zero and never prints -0", () => {
  assert.equal(fromScaled(67000050000000000n, 2), "67000.05");
  assert.equal(fromScaled(-1n, 2), "0.00");
  assert.equal(fromScaled(-1500000000000n, 1), "-1.5");
});

test("midpoint is exact and keeps the inputs' precision", () => {
  assert.equal(midpoint("67000.00", "67000.10"), "67000.05");
  assert.equal(midpoint("150.00", "150.02"), "150.01");
  assert.equal(midpoint("0.00001000", "0.00001100"), "0.00001050");
  assert.equal(midpoint(null, "1"), null);
  assert.equal(midpoint("x", "1"), null);
});

test("averagePrice", () => {
  assert.equal(averagePrice(["100.00", "101.00"]), "100.50");
  assert.equal(averagePrice([]), null);
});

test("gapBps is exact, symmetric and handles equal prices", () => {
  assert.equal(gapBps("100", "100")?.text, "0.00");
  assert.equal(gapBps("101", "100")?.text, "99.50");
  assert.equal(gapBps("100", "101")?.text, "99.50");
  assert.equal(gapBps("100.10", "100.00")?.text, "9.99");
  assert.equal(gapBps(null, "100"), null);
  assert.equal(gapBps("0", "0"), null);
});

test("gapTone thresholds", () => {
  assert.equal(gapTone(0), "ok");
  assert.equal(gapTone(4.99), "ok");
  assert.equal(gapTone(5), "warn");
  assert.equal(gapTone(25), "bad");
});

test("groupBySymbol: reference mid averages FRESH venues only; stale quotes are excluded", () => {
  const rows = [q(), q({ venue: "OKX", bid: "67010.00", ask: "67010.10" }), q({ symbol: "ETH-USDT", bid: "3200.00", ask: "3200.10", stale: true }), q({ symbol: "ETH-USDT", venue: "OKX", bid: null, ask: null, source: "NONE", stale: true })] as never[];
  const g = groupBySymbol(rows);
  assert.deepEqual(g.map((x) => x.symbol), ["BTC-USDT", "ETH-USDT"]);
  assert.equal(g[0].refMid, "67005.05");
  assert.equal(g[0].gap?.text, "1.49");
  assert.equal(g[1].refMid, null); // nothing fresh: never invent a price
  assert.equal(g[1].gap, null);
});

test("extremeSpread and spreadShare compare exact decimals", () => {
  const rows = [q({ spread_bps: "0.31" }), q({ spread_bps: "1.33" }), q({ spread_bps: null })] as never[];
  assert.equal(extremeSpread(rows, "min")?.spread_bps, "0.31");
  assert.equal(extremeSpread(rows, "max")?.spread_bps, "1.33");
  assert.equal(extremeSpread([q({ spread_bps: null })] as never[], "min"), null);
  assert.equal(spreadShare("1.33", rows), 1);
  assert.equal(spreadShare("0.665", rows), 0.5);
  assert.equal(spreadShare(null, rows), 0);
});

test("sourceSummary never calls fixture data real", () => {
  assert.equal(sourceSummary([q(), q()] as never[]).title, "Fixture only");
  assert.equal(sourceSummary([q({ source: "LIVE" }), q({ source: "LIVE" })] as never[]).tone, "ok");
  assert.equal(sourceSummary([q({ source: "LIVE" }), q()] as never[]).title, "Mixed sources");
  assert.equal(sourceSummary([q({ source: "NONE", stale: true })] as never[]).tone, "bad");
  assert.equal(sourceSummary([]).title, "Waiting for data");
});

test("ageText", () => {
  const now = Date.parse("2026-11-14T08:00:30+00:00");
  assert.equal(ageText("2026-11-14T08:00:00+00:00", now), "30 s ago");
  assert.equal(ageText("2026-11-14T07:57:00+00:00", now), "3 min ago");
  assert.equal(ageText(null, now), "—");
  assert.equal(ageText("garbage", now), "—");
});
