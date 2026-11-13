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
