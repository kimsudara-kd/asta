import assert from "node:assert/strict";
import test from "node:test";
import { direction, meterRatio, pctChange, scaled, systemStatus, uptimeText } from "./overview.ts";

const venue = (o: Record<string, unknown> = {}) =>
  ({ venue: "BINANCE", state: "LIVE", error: null, clock_offset_ms: 5, clock_ok: true, reconnects: 0, gap_open: false, ...o });
const risk = (o: Record<string, unknown> = {}) =>
  ({ halt_new_buys: false, halt_reasons: [], gateway_probe: { decision: "DENY", reasons: [] }, execution_enabled: false, live_trading_enabled: false, policy: {}, ...o });

test("scaled parses exact decimals and refuses everything else", () => {
  assert.equal(scaled("986.359895010000000000"), 986359895010000n);
  assert.equal(scaled("-0.5"), -500000000000n);
  assert.equal(scaled("1e3"), null);
  assert.equal(scaled(null), null);
  assert.equal(scaled("abc"), null);
});

test("pctChange is exact, truncates toward zero and never produces -0.00%", () => {
  assert.equal(pctChange("986.359895", "1000"), "-1.36%");
  assert.equal(pctChange("1000", "1000"), "0.00%");
  assert.equal(pctChange("1000.001", "1000"), "0.00%"); // tiny positive: truncated, not rounded up
  assert.equal(pctChange("999.999", "1000"), "0.00%"); // tiny negative must not print "-0.00%"
  assert.equal(pctChange("1250", "1000"), "25.00%");
  assert.equal(pctChange("5", "0"), null);
  assert.equal(pctChange(null, "1000"), null);
});

test("meterRatio clamps to 0..1 and handles missing limits", () => {
  assert.equal(meterRatio("0.025", "0.05"), 0.5);
  assert.equal(meterRatio("0.2", "0.05"), 1);
  assert.equal(meterRatio("-1", "0.05"), 0);
  assert.equal(meterRatio("0.01", null), 0);
  assert.equal(meterRatio("0.01", "0"), 0);
});

test("direction is by sign only", () => {
  assert.equal(direction("-13.64"), "down");
  assert.equal(direction("0.0001"), "up");
  assert.equal(direction("0"), "flat");
  assert.equal(direction(null), "flat");
});

test("systemStatus: a risk halt always wins and lists the reasons", () => {
  const s = systemStatus({ venues: [venue()], risk: risk({ halt_new_buys: true, halt_reasons: ["CLOCK_DRIFT"] }) } as never);
  assert.equal(s.tone, "bad");
  assert.match(s.detail, /CLOCK_DRIFT/);
});

test("systemStatus: stale/connecting/unreachable/gap/clock problems need attention", () => {
  for (const v of [venue({ state: "STALE" }), venue({ state: "CONNECTING" }), venue({ state: "UNREACHABLE" }), venue({ gap_open: true }), venue({ clock_ok: false })]) {
    assert.equal(systemStatus({ venues: [v], risk: risk() } as never).tone, "warn");
  }
});

test("systemStatus: fixture data is never reported as 'all nominal'", () => {
  const s = systemStatus({ venues: [venue({ state: "FIXTURE" }), venue({ venue: "OKX", state: "FIXTURE" })], risk: risk() } as never);
  assert.equal(s.tone, "warn");
  assert.match(s.title, /fixture/i);
  assert.equal(systemStatus({ venues: [venue(), venue({ venue: "OKX", state: "FIXTURE" })], risk: risk() } as never).tone, "ok");
});

test("uptimeText", () => {
  assert.equal(uptimeText(45), "45s");
  assert.equal(uptimeText(125), "2m 5s");
  assert.equal(uptimeText(8100), "2h 15m");
  assert.equal(uptimeText(200000), "2d 7h");
});
