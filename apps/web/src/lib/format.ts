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
