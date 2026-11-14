import assert from "node:assert/strict";
import test from "node:test";
import { assetExposure, gaugeTone, lossRatio, policyRows, reasonText, rejectionRows, riskVerdict } from "./risk.ts";

test("gaugeTone thresholds", () => {
  assert.equal(gaugeTone(0), "ok");
  assert.equal(gaugeTone(0.59), "ok");
  assert.equal(gaugeTone(0.6), "warn");
  assert.equal(gaugeTone(0.849), "warn");
  assert.equal(gaugeTone(0.85), "bad");
  assert.equal(gaugeTone(1), "bad");
});

test("reasonText translates known codes and shows unknown codes readably", () => {
  assert.equal(reasonText("STALE_MARKET_DATA"), "Market data is stale or missing");
  assert.equal(reasonText("RISK_ASSET_EXPOSURE_CAP"), "Single-asset exposure cap reached");
  assert.equal(reasonText("RISK_SOMETHING_NEW"), "Something new");
  assert.equal(reasonText("ODD_CODE"), "Odd code");
});

test("lossRatio counts only losses, exactly", () => {
  assert.equal(lossRatio("-0.01", "0.02"), 0.5);
  assert.equal(lossRatio("0.03", "0.02"), 0); // a gain never fills the loss gauge
  assert.equal(lossRatio("0", "0.02"), 0);
  assert.equal(lossRatio("-0.5", "0.02"), 1); // clamped
  assert.equal(lossRatio(null, "0.02"), 0);
});

test("assetExposure: cap = starting equity x per-asset %, ratio exact, most-used first", () => {
  const r = assetExposure({ BTC: "80.69200565", ETH: "97.41430742", SOL: "0.17550594" }, "1000", "0.10");
  assert.deepEqual(r.map((x) => x.asset), ["ETH", "BTC", "SOL"]);
  assert.equal(r[0].cap, "100.00");
  assert.equal(r[0].value, "97.41");
  assert.ok(Math.abs(r[0].ratio - 0.9741) < 0.0005);
  assert.equal(r[0].pct, "97.41");
  assert.deepEqual(assetExposure({ BTC: "1" }, "0", "0.10"), []);
  assert.deepEqual(assetExposure({ BTC: "1" }, "1000", "x"), []);
});

test("rejectionRows sorts by count and shares add up", () => {
  const r = rejectionRows({ INSUFFICIENT_CASH: 40, RISK_ASSET_EXPOSURE_CAP: 578 });
  assert.equal(r[0].code, "RISK_ASSET_EXPOSURE_CAP");
  assert.equal(r[0].label, "Single-asset exposure cap reached");
  assert.ok(Math.abs(r[0].share + r[1].share - 1) < 1e-9);
  assert.deepEqual(rejectionRows({}), []);
});

test("riskVerdict: a halt wins; near-limit warns; otherwise ok; always says execution stays disabled when ok", () => {
  const halted = riskVerdict({ haltNewBuys: true, haltReasons: ["CLOCK_DRIFT"], ratios: [{ label: "x", ratio: 1 }] });
  assert.equal(halted.tone, "bad");
  assert.match(halted.detail, /clock differs/i);
  const warn = riskVerdict({ haltNewBuys: false, haltReasons: [], ratios: [{ label: "ETH exposure", ratio: 0.974 }, { label: "Drawdown", ratio: 0.3 }] });
  assert.equal(warn.tone, "warn");
  assert.match(warn.detail, /ETH exposure at 97% of its limit/);
  assert.doesNotMatch(warn.detail, /Drawdown/);
  const ok = riskVerdict({ haltNewBuys: false, haltReasons: [], ratios: [{ label: "x", ratio: 0.2 }] });
  assert.equal(ok.tone, "ok");
  assert.match(ok.detail, /execution stays disabled/i);
});

test("policyRows keeps keys verbatim, adds descriptions and formats percentages exactly", () => {
  const r = policyRows({ starting_equity: "1000", max_invested_pct: "0.30", max_positions: "3", mystery: "7" });
  assert.deepEqual(r.map((x) => x.key), ["starting_equity", "max_invested_pct", "max_positions", "mystery"]);
  assert.equal(r[1].shown, "30.00%");
  assert.equal(r[0].shown, "1000");
  assert.equal(r[3].description, "");
  assert.ok(!r[0].description.includes("starting_equity")); // the key text appears in ONE cell only
});
