export type FeedState = "LIVE" | "FIXTURE" | "STALE" | "CONNECTING" | "UNREACHABLE";

/** Every data label the UI shows must be honest: fixtures are never presented as live. */
export function stateLabel(s: string): string {
  switch (s) {
    case "LIVE": return "Live public data";
    case "FIXTURE": return "FIXTURE data (not live)";
    case "STALE": return "Stale";
    case "CONNECTING": return "Connecting";
    case "UNREACHABLE": return "Unreachable";
    default: return "Unknown";
  }
}

export function stateTone(s: string): "ok" | "warn" | "bad" {
  if (s === "LIVE") return "ok";
  if (s === "FIXTURE" || s === "CONNECTING") return "warn";
  return "bad";
}

/** Keep Decimal strings exact: never route money through Number(). */
export function showDecimal(v: string | null | undefined): string {
  return v === null || v === undefined ? "—" : v;
}

export function canSee(role: string, page: "audit" | "status"): boolean {
  if (page === "status") return ["viewer", "operator", "reviewer", "admin"].includes(role);
  return role === "reviewer" || role === "admin";
}
