# M2 paper-trading rejection analysis (Workstream F, read-only)

Scope: read-only analysis of the live M2 observation sessions. **Nothing was changed** - no strategy, exposure limit, capital allocation, fee or historical result - and the existing holdout data was not used to tune anything. Data: `observe-5m` (`489d0990-6cf9-4120-9ad7-c6d370374dc2`, 2026-11-12 16:24 -> 2026-11-13 ~08:10 UTC) and `observe-1h` (`afec3e2b-...`, which made no signal that reached the order stage). Queries are reproducible from the tables `paper_orders`, `strategy_signals`, `paper_fills`, `paper_risk_events`.

## 1. What was observed
| Measure | Value |
|---|---|
| Orders (observe-5m, snapshot 08:10 UTC) | 253: 9 FILLED, 5 CANCELLED (unfilled grid orders), 239 REJECTED (the M2 acceptance record, taken at 07:20 UTC, shows 230 rejected = 189 cap + 41 cash; the session keeps running, so counts grow) |
| Rejection reasons | `RISK_ASSET_EXPOSURE_CAP` 189 (OKX 122, Binance 67), `INSUFFICIENT_CASH` 50 (OKX 22, Binance 28). **No** minimum-notional, lot-size, price-filter or fee-related rejection. |
| Rejected by strategy | `sma_cross` 183, `regime_grid` 56 orders (every rejected signal was a BUY); `trend_pullback` produced none |
| Repeats | the 239 rejections collapse into **11 distinct episodes** (same account + symbol, consecutive 5-minute bars): about **95 % (228 of 239) of rejections are repeats of a signal that was already rejected** |
| Longest repeat run | OKX `sma_cross` ETH-USDT: 74 consecutive rejected BUYs (16:55 -> 23:00 UTC) |
| Fills | 9 fills, notional 809.40, fees 0.8094, estimated slippage 0.4731 (all from `sma_cross`) |

## 2. Root causes
1. **A single shared per-asset cap versus per-account strategy sizing.** The accepted M1 reference policy caps exposure per asset at 10 % of starting equity (`max_per_asset_pct = 0.10` -> 100 USDT) across **all** accounts of the session, and total invested at 30 %. `sma_cross` sizes each entry at 90 USDT and `regime_grid` at 18 USDT per level. One filled 90 USDT entry in an asset (e.g. SOL on Binance) leaves about 10 USDT of headroom, so every later BUY in that asset from any other account - 6 accounts x 3 symbols compete for 3 asset caps - is refused by `check_buy` (`risk.py`: `asset_exposure_after > max_asset`). This is the **intended safeguard** working; the refusals are correct.
2. **Strategies are not rejection-aware.** `SmaCross.on_bar` emits a BUY whenever `fast > slow`, the position is zero, no order is open and `ctx.risk.halted_new_buys` is false. A cap refusal is not a "halt", the position stays zero and nothing remembers the refusal, so the same BUY is re-emitted on **every** bar while the trend persists. Grid levels re-arm the same way. The 95 % repeat rate and the 74-bar run are this mechanism. This part is **avoidable signal noise**, not a safeguard.
3. **Cash split.** The wallet is split into six 166.67 USDT sub-accounts. After one 90 USDT entry, a second 90 USDT entry in the same account fails `INSUFFICIENT_CASH` (76.67 left): 41 refusals at closure, 50 now. Also a sizing/allocation interaction, expected by construction.
4. **Not a cause:** exchange minimum-order rules (min notional 5 USDT is far below every order), fees (0.1 % assumed per fill, no rejection), strategy state corruption (none found), execution-model faults (none; the 9 fills reconcile exactly).

## 3. Classification
| Item | Classification |
|---|---|
| 189 asset-cap + 50 cash rejections | **Expected safeguards** (risk engine and cash check behaved correctly; zero risk-increasing order slipped through) |
| Repetition of identical rejected signals (228 of 239) | **Avoidable signal noise** - strategy-layer defect/limitation (no back-off, no headroom check), no financial impact in simulation |
| 6 accounts competing for one per-asset cap | Design interaction of M2 capital allocation and the M1 policy; **a finding for the strategy/portfolio owner, not an M3-A change** |
| Order volume / audit growth | Rejected orders and signals are persisted (append-only): about 240 extra rows per 16 h in `paper_orders`, `strategy_signals`, `paper_risk_events` - storage noise, no correctness impact |

## 4. Recommendations (NOT implemented; outside M3-A authority)
1. Strategy layer: after a risk or cash rejection, suppress re-emission of the same entry until exposure/cash headroom changes or a cool-down expires; expose per-asset headroom in `StrategyContext` so entries can be sized or skipped before submission.
2. Portfolio layer: allocate asset headroom explicitly (per account share of the 100 USDT) or size entries to the shared cap; decide whether six accounts on one 1,000 USDT wallet is the intended experiment.
3. Reporting: report "distinct rejected signals" next to raw rejection counts so refusals are not mistaken for activity.
4. Any change to strategy, exposure limits or sizing needs its own authorized work package, new paper sessions and a fresh (not holdout-tuned) evaluation; it would also invalidate comparison with the M2 sessions.

## 5. Relevance to M3
Order intent volume matters for demo trading: an exchange demo would receive (or must locally suppress) the duplicate-signal stream. M3-B must define idempotent client order IDs and duplicate suppression **before** any authenticated connector exists; the paper evidence shows the existing strategies would otherwise generate repeated identical intents (this is also a duplicate-order threat in `THREAT-MODEL.md`, T-14).


---

# R1 addendum: repeated-signal diagnostics (M3A-R1 Workstream F, read-only)

Data snapshot 2026-11-13 ~09:10 UTC, `observe-5m` (`489d0990-...`), tables `strategy_signals`, `paper_orders`. Nothing was modified; the sessions, strategy parameters, risk limits and configurations are untouched.

| Measure | Value |
|---|---|
| Rejected BUY signals | 264 (189 `RISK_ASSET_EXPOSURE_CAP`, 75 `INSUFFICIENT_CASH`); the cap count stopped growing at 189 (OKX disabled since 03:00 UTC), the cash count keeps growing (Binance `sma_cross` retrying) |
| Distinct signal numbers (`signal_id`) | 118 - **`signal_id` is a per-account sequence, not a global identifier** (the same number appears in several accounts), so it cannot be used for de-duplication |
| Distinct intents (account, symbol, side, order type, tag) | **12** |
| Repeats on the very next 5-minute bar | 253 of 264 (96 %); first-in-episode 11 |
| Repeats with the identical rejection reason as the previous bar | 252; reason flips: 1 (cap -> cash) |
| Strategy / tag mix | `sma_cross` entry 213 + exit 4; `regime_grid` b:0/b:1/b:2 levels 20/20/21 |

## Identical strategy-state evaluations
`SmaCross.on_bar` has no state besides its two moving averages and a counter that only increments on emission: as long as `fast > slow`, position = 0, no open order and no global halt, **every bar produces the same intent**. The rejection feeds nothing back (no position, no open order, `halted_new_buys` false), so consecutive evaluations are functionally identical, differing only in the client id counter (`x:<SYMBOL>:<n>`) - the identifier changes while the decision does not. `regime_grid` re-arms the same level on each bar while its level price is touched and exposure headroom is missing.

## Rejection patterns
* Asset cap: one shared per-asset cap (100 USDT, 10 % of 1,000) versus 90 USDT entries per account and 6 accounts per asset; after a single 90 USDT fill in an asset almost no headroom remains for any other account.
* Cash: each account owns 166.67 USDT; after one 90 USDT entry the second 90 USDT entry fails (`INSUFFICIENT_CASH`).
* Time pattern: bursts last until the trend flips or exposure frees (OKX `sma_cross` ETH-USDT 74 consecutive bars; Binance `sma_cross` SOL-USDT 40).

## Potential de-duplication boundaries (analysis only)
| Boundary | Effect on this session's 264 rejected signals (estimate from the data) | Risk |
|---|---|---|
| D1. Same intent key (account, symbol, side, type, tag) rejected on the previous bar, with unchanged reason and unchanged exposure/cash snapshot -> suppress | 252 of 264 suppressed (about 95 %); the 12 first emissions and the 1 reason flip pass | needs a reliable "state changed" signal (fill, cancel, exposure/cash delta) to re-arm |
| D2. Per-intent cool-down of N bars (1 h = 12 bars) | about 24 emissions remain (240 of 264 suppressed) | may delay a legitimate entry after headroom frees |
| D3. Cool-down 4 h | about 10 emissions remain | larger delay |
| D4. Pre-submission headroom check (strategy sees per-asset headroom and cash, skips or resizes) | removes the rejection class itself (cap and cash) | changes the strategy interface; separate approval |
| D5. Idempotent order identity: deterministic client order id = hash(account, symbol, side, tag, decision bar), unique per decision | makes any accidental duplicate harmless at an exchange; does not reduce repeated decisions | prerequisite for M3-B regardless |

## Proposals for a later, separately approved milestone (not implemented)
1. Strategy layer: remember a rejected intent and re-emit only when its blocking condition changes (D1) or a cool-down expires (D2).
2. Expose per-asset headroom and available cash in `StrategyContext` (D4) and size entries to the cap.
3. Replace the per-account `signal_id` by a global, deterministic decision id and a deterministic client order id (D5).
4. Reporting: show distinct rejected intents next to raw counts.
5. Portfolio layer: decide whether six accounts should share one per-asset cap; allocate headroom explicitly.
All require their own work package, new paper sessions and a fresh evaluation (the M2 sessions and holdout data must not be tuned).
