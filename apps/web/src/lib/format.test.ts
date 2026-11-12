import assert from "node:assert/strict";
import { test } from "node:test";
import {
  canSee,
  isNa,
  metricText,
  pctText,
  trimDecimal,
  DATA_DISCLAIMER,
  showDecimal,
  sourceLabel,
  stateLabel,
  stateTone,
} from "./format.ts";

test("fixture is never labelled as real data", () => {
  assert.match(stateLabel("FIXTURE"), /FIXTURE/);
  assert.match(stateLabel("FIXTURE"), /not real/);
  assert.equal(stateTone("FIXTURE"), "warn");
  assert.equal(stateTone("LIVE"), "ok");
  assert.equal(stateTone("UNREACHABLE"), "bad");
});

test("real-time data is never described as live trading", () => {
  for (const s of ["LIVE", "FIXTURE", "NONE", "weird", "STALE", "CONNECTING"]) {
    assert.doesNotMatch(sourceLabel(s), /\blive\b/i);
    assert.doesNotMatch(stateLabel(s), /\blive\b/i);
  }
  assert.equal(sourceLabel("LIVE"), "REAL-TIME PUBLIC DATA");
  assert.match(sourceLabel("FIXTURE"), /not real/);
  assert.match(DATA_DISCLAIMER, /do not enable, authorise or imply live trading/);
});

test("decimals stay exact strings", () => {
  assert.equal(showDecimal("67000.10"), "67000.10");
  assert.equal(showDecimal("0.00001000"), "0.00001000");
  assert.equal(showDecimal(null), "—");
});

test("role visibility", () => {
  assert.equal(canSee("viewer", "audit"), false);
  assert.equal(canSee("reviewer", "audit"), true);
  assert.equal(canSee("operator", "audit"), false);
  assert.equal(canSee("nobody", "status"), false);
});

test("N/A metrics keep their reason and are never shown as zero", () => {
  const na = { value: null, na_reason: "only 12 daily observations (< 30)" };
  assert.equal(isNa(na), true);
  assert.equal(isNa("0"), false);
  assert.match(metricText(na), /^N\/A — only 12 daily/);
  assert.notEqual(metricText(na), "0");
  assert.equal(metricText(null), "—");
});

test("decimals are trimmed by text, not through floating point", () => {
  assert.equal(trimDecimal("47.85164945"), "47.85");
  assert.equal(trimDecimal("0.00000000"), "0.00");
  assert.equal(trimDecimal("-0.00400000"), "0.00"); // no negative zero
  assert.equal(trimDecimal("1000"), "1000.00");
  assert.equal(trimDecimal("123456789012345678.123456", 2), "123456789012345678.12"); // beyond float precision
  assert.equal(trimDecimal("1e-8"), "1e-8");
});

test("fractions render as percentages without float error", () => {
  assert.equal(pctText("0.04785165"), "4.78%");
  assert.equal(pctText("-0.0190666"), "-1.90%");
  assert.equal(pctText("0.30"), "30.00%");
  assert.equal(pctText("0"), "0.00%");
  assert.equal(pctText("1.5"), "150.00%");
  assert.equal(metricText("0.05", "pct"), "5.00%");
  assert.equal(metricText("1234.5678", "money"), "1234.56");
  assert.equal(metricText(true), "yes");
});
