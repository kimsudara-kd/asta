import assert from "node:assert/strict";
import { test } from "node:test";
import {
  canSee,
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
