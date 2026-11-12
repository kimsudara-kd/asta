import { test } from "node:test";
import assert from "node:assert/strict";
import { canSee, showDecimal, stateLabel, stateTone } from "./format.ts";

test("fixture is never labelled live", () => {
  assert.match(stateLabel("FIXTURE"), /FIXTURE/);
  assert.doesNotMatch(stateLabel("FIXTURE"), /^Live/);
  assert.equal(stateTone("FIXTURE"), "warn");
  assert.equal(stateTone("LIVE"), "ok");
  assert.equal(stateTone("UNREACHABLE"), "bad");
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
