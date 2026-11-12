export class ApiError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api${path}`, { credentials: "same-origin", ...init });
  if (!res.ok) throw new ApiError(res.status, `${res.status}`);
  return (await res.json()) as T;
}

export type Me = { username: string; role: string; csrf_token: string };
export type Venue = {
  venue: string; state: string; error: string | null; clock_offset_ms: number | null;
  clock_ok: boolean; reconnects: number; gap_open: boolean;
};
export type Risk = {
  halt_new_buys: boolean; halt_reasons: string[];
  gateway_probe: { decision: string; reasons: string[] };
  execution_enabled: boolean; live_trading_enabled: boolean; policy: Record<string, string>;
};
export type Overview = {
  environment: string; banner: string;
  equity: { simulated_start: string; pnl: string; fee_drag: string };
  venues: Venue[]; heartbeat_utc: string; uptime_seconds: number; risk: Risk;
  events_accepted: number; duplicates_dropped: number;
};
export type Market = {
  venue: string; symbol: string; bid: string | null; ask: string | null;
  spread_bps: string | null; last_ingested: string | null; stale: boolean; source: string;
};
export type AuditEvent = { id: number; at: string; actor: string; action: string; payload: Record<string, unknown> };
export type Audit = { chain_ok: boolean; first_bad_index: number | null; events: AuditEvent[] };
