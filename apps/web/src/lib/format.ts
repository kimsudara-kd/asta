export type FeedState = "LIVE" | "FIXTURE" | "STALE" | "CONNECTING" | "UNREACHABLE";

/** Venue connection-state label. "LIVE" is the API's data-state; the UI never shows the bare word
 *  "live", because it must not be read as "live trading". */
export function stateLabel(s: string): string {
  switch (s) {
    case "LIVE":
      return "Real-time public data (read-only)";
    case "FIXTURE":
      return "FIXTURE data (not real)";
    case "STALE":
      return "Stale";
    case "CONNECTING":
      return "Connecting";
    case "UNREACHABLE":
      return "Unreachable";
    default:
      return "Unknown";
  }
}

export function stateTone(s: string): "ok" | "warn" | "bad" {
  if (s === "LIVE") return "ok";
  if (s === "FIXTURE" || s === "CONNECTING") return "warn";
  return "bad";
}

/** Data-source label for a quote. "LIVE" in the API means real-time *market data*; it never
 *  means trading is live. */
export function sourceLabel(source: string): string {
  switch (source) {
    case "LIVE":
      return "REAL-TIME PUBLIC DATA";
    case "FIXTURE":
      return "FIXTURE (not real data)";
    case "NONE":
      return "NO DATA";
    default:
      return "UNKNOWN";
  }
}

export const DATA_DISCLAIMER =
  "Real-time public prices are shown for observation only. They do not enable, authorise or imply live trading. Trading is disabled in this build.";

/** Keep Decimal strings exact: never route money through Number(). */
export function showDecimal(v: string | null | undefined): string {
  return v === null || v === undefined ? "—" : v;
}

export function canSee(role: string, page: "audit" | "status"): boolean {
  if (page === "status") return ["viewer", "operator", "reviewer", "admin"].includes(role);
  return role === "reviewer" || role === "admin";
}

/** A metric is either a plain value or an N/A object carrying the reason. N/A is never shown as 0. */
export function isNa(v: unknown): v is { value: null; na_reason: string } {
  return typeof v === "object" && v !== null && "na_reason" in (v as Record<string, unknown>);
}

/** Trim a Decimal STRING to `dp` places by text (never through Number()), keeping the sign. */
export function trimDecimal(s: string, dp = 2): string {
  const m = /^(-?)(\d+)(?:\.(\d+))?(?:[eE]([+-]?\d+))?$/.exec(s.trim());
  if (!m) return s;
  if (m[4] !== undefined) return s; // exponent form: leave untouched rather than guess
  const frac = (m[3] ?? "").padEnd(dp, "0").slice(0, dp);
  const neg = m[1] === "-" && /[1-9]/.test(m[2] + frac) ? "-" : "";
  return dp > 0 ? `${neg}${m[2]}.${frac}` : `${neg}${m[2]}`;
}

/** Fraction string (0.0478) -> "4.78%" using string shifting, not floating point. */
export function pctText(s: string, dp = 2): string {
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(s.trim());
  if (!m) return s;
  const frac = (m[3] ?? "").padEnd(dp + 2, "0");
  const intPart = (m[2] + frac.slice(0, 2)).replace(/^0+(?=\d)/, "");
  const rest = frac.slice(2, 2 + dp);
  const neg = m[1] === "-" && /[1-9]/.test(intPart + rest) ? "-" : "";
  return `${neg}${intPart}${dp > 0 ? "." + rest : ""}%`;
}

export function metricText(v: unknown, kind: "money" | "pct" | "plain" = "plain"): string {
  if (isNa(v)) return `N/A — ${v.na_reason}`;
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  const s = String(v);
  if (kind === "money") return trimDecimal(s, 2);
  if (kind === "pct") return pctText(s);
  return s;
}

export const RESEARCH_BANNER = "SIMULATED · HISTORICAL DATA · ESTIMATED FILLS · NOT LIVE TRADING";
