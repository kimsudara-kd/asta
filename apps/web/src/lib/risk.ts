// Pure helpers for the Risk page. Ratios and limit amounts come from exact Decimal STRINGS (BigInt); Number() is
// used only for gauge/bar geometry.
import { fromScaled } from "./markets.ts";
import { meterRatio, scaled } from "./overview.ts";
import { pctText, trimDecimal } from "./format.ts";

export type Tone = "ok" | "warn" | "bad";

/** How close to a limit is "worrying"? <60 % fine, <85 % watch, otherwise at/near the limit. */
export function gaugeTone(ratio: number): Tone {
  return ratio < 0.6 ? "ok" : ratio < 0.85 ? "warn" : "bad";
}

const REASONS: Record<string, string> = {
  STALE_MARKET_DATA: "Market data is stale or missing",
  CLOCK_DRIFT: "Exchange clock differs too much from ours",
  RISK_DRAWDOWN_HALT: "Drawdown halt is active",
  RISK_DAILY_LOSS_TRIP: "Daily loss limit was tripped",
  RISK_ASSET_EXPOSURE_CAP: "Single-asset exposure cap reached",
  RISK_TOTAL_EXPOSURE_CAP: "Total exposure cap reached",
  INSUFFICIENT_CASH: "Not enough free cash for the entry",
  M0_EXECUTION_DISABLED: "Order execution is disabled in this build",
};

/** Plain-language text for a reason code; unknown codes are shown readably, never hidden. */
export function reasonText(code: string): string {
  return REASONS[code] ?? code.replace(/^RISK_/, "").replaceAll("_", " ").toLowerCase().replace(/^./, (c) => c.toUpperCase());
}

/** Only LOSSES count towards the daily-loss limit: a gain leaves the gauge empty. */
export function lossRatio(dailyPnlPct: string | null | undefined, limit: string | null | undefined): number {
  const v = scaled(dailyPnlPct);
  if (v === null || v >= 0n) return 0;
  return meterRatio(fromScaled(-v, 8), limit);
}

export type AssetUse = { asset: string; value: string; cap: string; ratio: number; pct: string };

/** Exposure per asset against the single-asset cap (cap = starting equity x max_asset_pct), most-used first. */
export function assetExposure(byAsset: Record<string, string>, startingEquity: string, maxAssetPct: string): AssetUse[] {
  const eq = scaled(startingEquity);
  const pct = scaled(maxAssetPct);
  if (eq === null || pct === null || eq <= 0n || pct <= 0n) return [];
  const capScaled = (eq * pct) / 1_000_000_000_000n;
  const cap = fromScaled(capScaled, 2);
  return Object.entries(byAsset)
    .map(([asset, value]) => ({
      asset,
      value: trimDecimal(value, 2),
      cap,
      ratio: meterRatio(value, fromScaled(capScaled, 8)),
      pct: ((): string => {
        const v = scaled(value);
        if (v === null || capScaled === 0n) return "—";
        const q = (v * 10000n) / capScaled; // percent x 100, exact integer
        return `${q / 100n}.${(q % 100n).toString().padStart(2, "0")}`;
      })(),
    }))
    .sort((a, b) => b.ratio - a.ratio);
}

export type RejectionRow = { code: string; label: string; count: number; share: number };

export function rejectionRows(rej: Record<string, number>): RejectionRow[] {
  const total = Object.values(rej).reduce((n, c) => n + c, 0);
  return Object.entries(rej)
    .map(([code, count]) => ({ code, label: reasonText(code), count, share: total ? count / total : 0 }))
    .sort((a, b) => b.count - a.count);
}

export type Verdict = { tone: Tone; title: string; detail: string };

/** One honest verdict for the hero. A halt always wins; "close to a limit" is a warning, never hidden. */
export function riskVerdict(a: { haltNewBuys: boolean; haltReasons: string[]; ratios: { label: string; ratio: number }[] }): Verdict {
  if (a.haltNewBuys) {
    return { tone: "bad", title: "New simulated buys are halted", detail: a.haltReasons.map(reasonText).join("; ") || "A policy limit was breached." };
  }
  const hot = a.ratios.filter((r) => r.ratio >= 0.85).sort((x, y) => y.ratio - x.ratio);
  if (hot.length > 0) {
    return { tone: "warn", title: "Close to a limit", detail: `${hot.map((h) => `${h.label} at ${Math.round(h.ratio * 100)}% of its limit`).join("; ")}. Entries that would cross it are refused.` };
  }
  return { tone: "ok", title: "Within all limits", detail: "No halt is active and every limit has headroom. Order execution stays disabled regardless." };
}

const POLICY_TEXT: Record<string, { label: string; pct: boolean }> = {
  starting_equity: { label: "Virtual capital every percentage is measured against (USDT)", pct: false },
  max_invested_pct: { label: "Maximum share of equity invested at any time", pct: true },
  max_per_asset_pct: { label: "Maximum share of equity in a single asset", pct: true },
  min_cash_reserve_pct: { label: "Minimum share of equity kept in cash", pct: true },
  daily_drawdown_trip_pct: { label: "Loss within one day that halts new buys", pct: true },
  peak_drawdown_trip_pct: { label: "Fall from peak equity that halts new buys", pct: true },
  max_positions: { label: "Maximum simultaneous positions", pct: false },
  min_net_edge_pct: { label: "Minimum expected edge after costs to enter", pct: true },
};

export type PolicyRow = { key: string; description: string; raw: string; shown: string };

export function policyRows(policy: Record<string, string>): PolicyRow[] {
  return Object.entries(policy).map(([key, raw]) => {
    const meta = POLICY_TEXT[key];
    return { key, description: meta?.label ?? "", raw, shown: meta?.pct ? pctText(raw) : raw };
  });
}
