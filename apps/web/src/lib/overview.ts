// Pure helpers for the Overview page. Money is handled as exact Decimal STRINGS / BigInt, never Number().
import type { Overview, Risk } from "./api";

const SCALE = 12;

/** Parse a plain decimal string into a scaled BigInt (SCALE fractional digits). null if not a plain decimal. */
export function scaled(s: string | null | undefined): bigint | null {
  if (s === null || s === undefined) return null;
  const m = /^(-?)(\d+)(?:\.(\d+))?$/.exec(s.trim());
  if (!m) return null;
  const frac = (m[3] ?? "").padEnd(SCALE, "0").slice(0, SCALE);
  const v = BigInt(m[2] + frac);
  return m[1] === "-" ? -v : v;
}

/** (a - b) / b as a signed percentage text with `dp` places (truncated), e.g. "-1.36%". null if undefined. */
export function pctChange(a: string | null | undefined, b: string | null | undefined, dp = 2): string | null {
  const x = scaled(a);
  const y = scaled(b);
  if (x === null || y === null || y === 0n) return null;
  const f = BigInt(10 ** dp);
  const q = ((x - y) * 100n * f) / y; // truncates toward zero
  const neg = q < 0n;
  const abs = neg ? -q : q;
  const whole = abs / f;
  const frac = (abs % f).toString().padStart(dp, "0");
  return `${neg && abs !== 0n ? "-" : ""}${whole}${dp > 0 ? "." + frac : ""}%`;
}

/** value / limit as a 0..1 ratio for a meter bar (geometry only; clamped). */
export function meterRatio(value: string | null | undefined, limit: string | null | undefined): number {
  const v = scaled(value);
  const l = scaled(limit);
  if (v === null || l === null || l <= 0n) return 0;
  const r = Number((v * 10000n) / l) / 10000;
  return Math.max(0, Math.min(1, r));
}

/** "up" | "down" | "flat" for a decimal string (sign only). */
export function direction(s: string | null | undefined): "up" | "down" | "flat" {
  const v = scaled(s);
  if (v === null || v === 0n) return "flat";
  return v > 0n ? "up" : "down";
}

export type Tone = "ok" | "warn" | "bad";
export type SystemStatus = { tone: Tone; title: string; detail: string };

/** One honest verdict for the hero card. Fixture data is called out, never presented as "all good". */
export function systemStatus(o: Pick<Overview, "venues" | "risk">): SystemStatus {
  const risk: Risk = o.risk;
  if (risk.halt_new_buys) {
    return {
      tone: "bad",
      title: "Risk halt active",
      detail: `New simulated buys are suspended (${risk.halt_reasons.join(", ") || "policy breach"}).`,
    };
  }
  const trouble = o.venues.filter(
    (v) => ["STALE", "CONNECTING", "UNREACHABLE"].includes(v.state) || v.gap_open || v.clock_ok === false,
  );
  if (trouble.length > 0) {
    return {
      tone: "warn",
      title: "Attention needed",
      detail: `${trouble.map((v) => v.venue).join(", ")}: data is stale, reconnecting or has a gap.`,
    };
  }
  if (o.venues.length > 0 && o.venues.every((v) => v.state === "FIXTURE")) {
    return {
      tone: "warn",
      title: "Running on fixture data",
      detail: "Prices are synthetic test data, not real markets. Nothing is wrong, nothing is real.",
    };
  }
  return { tone: "ok", title: "All systems nominal", detail: "Feeds are fresh and every safeguard is in place." };
}

/** Human uptime like "2h 14m" (display only). */
export function uptimeText(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m ${s % 60}s`;
  return `${s}s`;
}
