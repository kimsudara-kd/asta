import assert from "node:assert/strict";
import test from "node:test";
import { orderStats, signalStats, sumDecimals } from "./paperStats.ts";

test("sumDecimals is exact (no float drift) and skips non-decimals", () => {
  assert.equal(sumDecimals(["0.1", "0.2"], 4), "0.3000"); // 0.1 + 0.2 is exactly 0.3 here
  assert.equal(sumDecimals(["0.0001", "0.0002", null, "x", undefined], 4), "0.0003");
  assert.equal(sumDecimals([], 2), "0.00");
  assert.equal(sumDecimals(["-1.5", "0.25"], 2), "-1.25");
});

test("signalStats counts accepted/refused and the most common refusal reason", () => {
  const s = signalStats([
    { accepted: false, reason: "RISK_ASSET_EXPOSURE_CAP" }, { accepted: false, reason: "RISK_ASSET_EXPOSURE_CAP" },
    { accepted: false, reason: "INSUFFICIENT_CASH" }, { accepted: true, reason: "" },
  ]);
  assert.deepEqual([s.total, s.accepted, s.refused], [4, 1, 3]);
  assert.equal(s.refusedShare, 0.75);
  assert.deepEqual(s.topReason, { reason: "RISK_ASSET_EXPOSURE_CAP", count: 2 });
  assert.deepEqual(signalStats([]), { total: 0, accepted: 0, refused: 0, refusedShare: 0, topReason: null });
});

test("orderStats totals fees and slippage exactly and counts statuses", () => {
  const o = orderStats([
    { status: "FILLED", estimated_fee: "0.0100", estimated_slippage: "0.0030" },
    { status: "FILLED", estimated_fee: "0.0200", estimated_slippage: "0.0010" },
    { status: "OPEN", estimated_fee: "0", estimated_slippage: "0" },
    { status: "REJECTED", estimated_fee: "0", estimated_slippage: "0" },
  ]);
  assert.deepEqual([o.total, o.filled, o.open, o.rejected], [4, 2, 1, 1]);
  assert.equal(o.fees, "0.0300");
  assert.equal(o.slippage, "0.0040");
});
