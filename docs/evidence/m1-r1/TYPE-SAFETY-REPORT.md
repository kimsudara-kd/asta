# M1-R1 Workstream C — Type-checking exception report

**Result:** `mypy` (strict, plugin `pydantic.mypy`) → **Success: no issues found in 115 source files** (`src/` + `tests/`), run in the pinned python:3.12 container (Windows App Control blocks the local binary). `pyright src` → 0 errors, 0 warnings. Log: `docs/evidence/m1-r1/mypy.log`.

## What happened to the original 67 test-module errors

In M1-E the first strict run reported 69 errors in 12 files: **2 in production code** (fixed then: an unused `type: ignore` in `strategies/registry.py`, a missing annotation in `analytics/metrics.py`) and **67 in test modules**. To finish M1-E I had suppressed the 67 with `ignore_errors = true` for `tests.research.*` and `tests.integration.test_research_db`. **That exclusion has been removed in full.** Each error was fixed in the test code (none suppressed):

| Count | mypy code | Cause | Fix |
|---|---|---|---|
| 27 | arg-type (`test_properties.py`) | Hypothesis `settings(**COMMON)` where `COMMON` was inferred as `dict[str, list \| int \| None]` | `COMMON: dict[str, Any]` |
| 9 | arg-type (`test_strategies.py`) | `GridParams(**base)` / `TrendPullbackParams(**base)` with `base = dict(...)` inferred as `dict[str, object]` | `base: dict[str, Any]` |
| 6 | arg-type (`test_risk_sim.py` 5, `test_properties.py` 1) | `Scripted`/`run` required invariant `dict[int, list[Action]]` but tests pass `list[OrderIntent]`; `dict[str, list[Bar]]` vs `Sequence[Bar]` | helper signatures now take `Mapping[int, Sequence[Action]]` (covariant; strictly more permissive for callers; no behaviour change); one `series` annotated `dict[str, Sequence[Bar]]` |
| 6 | union-attr (`test_data_foundation.py` 4, `test_walkforward.py` 2) | `finding(...)`, `.train`, `.test` are `Optional` | explicit `assert x is not None` before use, so the tests now also fail loudly if a finding or window is missing |
| 6 | assignment / operator (`bar_builders.py`) | loop variable re-bound to a different type | separate variable |
| 6 | attr-defined / operator / misc (`test_metrics.py`) | `sum()` of Decimals typed `Decimal \| int`; `rep.value()` is `Decimal \| None` | `sum(..., D(0))` with typed locals; helper `_risk()` asserts non-None |
| 2 | comparison-overlap (`test_data_foundation.py`) | `assert ROUND_UP != ROUND_DOWN != ROUND_HALF_EVEN` is a chained comparison that never compares the first and last | replaced by `len({...}) == 3` (a **stronger** assertion) |
| 2 | type-arg (`test_data_foundation.py`, `test_lookahead.py`) | bare `dict`, `tuple` | parameterised |
| 1 | assignment (`test_data_foundation.py`) | default `frozenset()` for a `set[int]` parameter | `frozenset[int] \| set[int]` |
| 1 | var-annotated (`test_sensitivity_regimes_advisory.py`) | untyped heterogeneous tuple of dicts | annotated list |
| 1 | index (`test_research_db.py`) | `performance_json()` is Optional | `_perf()` helper asserts non-None |

(27 + 9 + 6 + 6 + 6 + 6 + 2 + 2 + 1 + 1 + 1 = 67.)

New M1-R1 test modules were written to the same standard (no exclusions).

## Remaining configuration that relaxes typing (complete list)

1. `[[tool.mypy.overrides]] module = "tests.*"`: `disallow_untyped_defs`, `disallow_incomplete_defs`, `disallow_untyped_calls` = false, `check_untyped_defs = true`. This is the **M0** setting (pytest fixtures/params are unannotated); test bodies are still fully type-checked. Unchanged by M1/M1-R1.
2. Six targeted `# type: ignore[...]` comments in tests, each deliberate: three assert that frozen dataclasses / immutable entries reject mutation (`test_engine_golden.py:261`, `test_risk_sim.py:30`, `test_sensitivity_regimes_advisory.py:238`) two pass deliberately wrong fakes in the M0 market-state supervisor tests (`tests/unit/test_market_state.py:116-117`); one is the untyped `pgserver` import in `tests/integration/conftest.py:37`.
3. `ruff` per-file `E501` (line length) ignores for research modules, scripts and `migrations/versions/*.py` (formatting only, not typing).

## Production code

`src/` has **zero** `type: ignore` comments, zero per-module mypy overrides and no global exclusions; `strict = true` applies in full.
