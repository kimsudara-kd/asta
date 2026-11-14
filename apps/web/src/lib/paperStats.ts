// Pure helpers for the paper Signals & Orders page. Sums use exact Decimal STRINGS (BigInt), never Number().
import { fromScaled } from "./markets.ts";
import { scaled } from "./overview.ts";
import type { PaperOrderRow, PaperSignal } from "./paper";

/** Exact sum of decimal strings, truncated to `dp` places. Values that are not plain decimals are skipped. */
export function sumDecimals(values: (string | null | undefined)[], dp = 4): string {
  const total = values.reduce<bigint>((n, v) => n + (scaled(v) ?? 0n), 0n);
  return fromScaled(total, dp);
}

export type SignalStats = { total: number; accepted: number; refused: number; refusedShare: number; topReason: { reason: string; count: number } | null };

export function signalStats(signals: Pick<PaperSignal, "accepted" | "reason">[]): SignalStats {
  const accepted = signals.filter((s) => s.accepted).length;
  const refusedRows = signals.filter((s) => !s.accepted);
  const counts = new Map<string, number>();
  for (const s of refusedRows) counts.set(s.reason || "—", (counts.get(s.reason || "—") ?? 0) + 1);
  const top = [...counts.entries()].sort((a, b) => b[1] - a[1])[0];
  return {
    total: signals.length,
    accepted,
    refused: refusedRows.length,
    refusedShare: signals.length ? refusedRows.length / signals.length : 0,
    topReason: top ? { reason: top[0], count: top[1] } : null,
  };
}

export type OrderStats = { total: number; filled: number; open: number; rejected: number; fees: string; slippage: string };

export function orderStats(orders: Pick<PaperOrderRow, "status" | "estimated_fee" | "estimated_slippage">[]): OrderStats {
  return {
    total: orders.length,
    filled: orders.filter((o) => o.status === "FILLED").length,
    open: orders.filter((o) => o.status === "OPEN" || o.status === "PARTIALLY_FILLED").length,
    rejected: orders.filter((o) => o.status === "REJECTED").length,
    fees: sumDecimals(orders.map((o) => o.estimated_fee)),
    slippage: sumDecimals(orders.map((o) => o.estimated_slippage)),
  };
}
