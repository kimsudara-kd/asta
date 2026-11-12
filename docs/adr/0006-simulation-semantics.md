# ADR-0006: Simulation semantics — causality, fills, intrabar policy

Status: accepted (M1)

**Causality.** A bar is delivered to the strategy only after it has closed. The strategy sees history up to and including that bar and nothing else (the engine hands over only already-delivered bars; no dataset statistics). Orders submitted at the close of bar *t* become active no earlier than the **open of bar t+1+extra latency bars** (`latency_bars`, default 0 extra). Sub-bar latency is not modelled separately; it is covered by the slippage model and `latency_bars` sensitivity. Warm-up bars are delivered with `is_warmup=True`; any order intent during warm-up is rejected and counted.

**Candle-only fill policy (conservative; every result is labelled ESTIMATED FILLS).**
- *Market:* fills at the next bar's **open**, adverse by `slippage = base_bps + impact_bps_per_pct × participation%` plus half-spread assumption; capped by `max_participation × bar volume` (partial fill, remainder carries up to `expire_after_bars`).
- *Limit (resting):* fills only when price trades **strictly through** the limit by at least `through_ticks` ticks (buy: `low ≤ limit − k·tick`; sell: `high ≥ limit + k·tick`); a touch is not a fill. Fill price = the limit (maker fee). If the bar **opens** through the limit the order fills at the open (taker fee, price improvement kept). Filled quantity ≤ `maker_volume_share × max_participation × bar volume`. **No queue-priority claim is made.** Unfilled orders never produce P&L.
- *Stop-market (protective sells only):* triggers when `low ≤ stop`; fill = `min(open, stop)` minus slippage × `stop_slippage_multiplier` (gap-down fills at the open, not at the stop). Stops do not guarantee price.
- **No intrabar round trips:** inventory bought and cash received during a bar are not usable until the next bar; the sequence of intrabar events is never inferred. If a stop and a take-profit are both reachable in the same bar the **stop wins** (adverse-first). Same-side limits are processed in the adverse order (buys high→low, sells low→high).
- Missing bars: no synthetic bars; pending orders expire across a gap; no fill spans a gap.

**Order lifecycle:** CREATED → VALIDATED → SIMULATED_OPEN → PARTIALLY_FILLED → FILLED, or CANCELLED / EXPIRED / REJECTED (reason code recorded). Spot only: sells need settled inventory, buys need available cash (open buys reserve cash); no shorts, margin or negative balances (hard invariants).

**Fees:** maker/taker bps from a versioned `FeeSchedule`; charged in the received asset (buy → base, sell → quote), rounded **up** to 1e-8; fee currency recorded. All venue fee/rule profiles are marked `verified=false` unless source evidence is attached.
