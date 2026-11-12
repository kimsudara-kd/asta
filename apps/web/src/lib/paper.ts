export const PAPER_BANNER = "PAPER TRADING — VIRTUAL FUNDS ONLY";

export type PaperSession = {
  id: string; name: string; state: string; state_reason: string; created_at: string;
  started_at: string | null; stopped_at: string | null; last_step_ts: string | null; last_step_seq: number;
  last_reconcile_ok: boolean | null; last_reconciled_at: string | null; config_hash: string;
  equity: string; starting_equity: string; timeframe: string; enabled_venues: string[];
  idle_cash_venues: string[]; wallets: Record<string, string>;
  strategies: { strategy_id: string; version: string; deployment_candidate: boolean; research_status: string }[];
};
export type PaperAccount = {
  venue: string; strategy: string; cash_available: string; cash_reserved: string; cash_total: string;
  assets: { asset: string; quantity: string; reserved: string; cost_basis: string; mark: string | null; value: string; unrealized_pnl: string }[];
  unrealized_pnl: string; realized_pnl: string; equity: string;
};
export type PaperPortfolio = {
  accounts: Record<string, PaperAccount>; idle_wallet_cash: string;
  totals: { equity: string; cash: string; inventory_value: string; realized_pnl: string; unrealized_pnl: string };
  exposure_by_venue: Record<string, string>; exposure_by_asset: Record<string, string>;
};
export type PaperOrderRow = {
  account: string; order_id: number; venue: string; symbol: string; side: string; type: string; quantity: string;
  filled_qty: string; limit_price: string | null; status: string; reason: string; created_ts: string;
  estimated_fee: string; estimated_slippage: string; filled_notional: string;
};
export type PaperSignal = {
  account: string; signal_id: number; step_ts: string; strategy: string; strategy_version: string; venue: string;
  symbol: string; side: string; type: string; accepted: boolean; reason: string;
};
export type PaperRisk = {
  policy: Record<string, string | null>; daily_auto_reset: boolean;
  current: Record<string, string>; halts: { daily_loss_tripped: boolean; drawdown_halted: boolean };
  events: { seq: number; ts: string; kind: string; detail: string }[];
  rejections_by_reason: Record<string, number>; market_health: Record<string, string>;
  open_incidents: { id: string; kind: string; severity: string; detail: string; opened_at: string }[];
};
export type PaperPerf = {
  points: { ts: string; equity: string; drawdown: string; constrained_hold: string | null; unconstrained_hold: string | null; all_cash: string | null }[];
  strategies: { strategy: string; starting_cash: string; equity: string; net_pnl: string; fills: number; fees: string; estimated_slippage: string; realized_pnl: string; deployment_candidate: boolean }[];
  data_coverage: Record<string, { steps: number; complete_and_fresh: number }>; equity: string | null; net_pnl: string; note: string;
};
export type FeedsStatus = {
  feeds: { venue: string; state: string; source: string; reason: string; reconnects: number; last_event_age_s: number | null; clock_offset_ms: number | null }[];
  gaps: { venue: string; symbol: string; start: string; end: string; missing: number; status: string; detail: string }[];
};
export type DailyReport = { session_id: string; date: string; content_hash: string; reproducible: boolean; body: Record<string, any> }; // eslint-disable-line @typescript-eslint/no-explicit-any
export type Alert = { id: number; kind: string; severity: string; message: string; created_at: string; acknowledged_at: string | null };
