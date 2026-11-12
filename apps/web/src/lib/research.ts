import { api, ApiError } from "./api";

export const RESEARCH_LABELS = ["SIMULATED", "HISTORICAL DATA", "ESTIMATED FILLS", "NOT LIVE TRADING"] as const;

export type NaMetric = { value: null; na_reason: string };
export type Metric = string | number | boolean | null | NaMetric | Record<string, unknown> | unknown[];

export type DatasetRow = {
  version_id: string;
  dataset_id: string;
  venue: string;
  symbol: string;
  timeframe: string;
  checksum: string;
  source: string;
  source_type: string;
  label: string;
  period_start: string;
  period_end: string;
  row_count: number;
  validation_status: "PASS" | "WARNING" | "REJECTED";
  accepted_for_research: boolean;
  provenance: Record<string, unknown>;
  parent_checksums: string[];
  file_name: string;
  imported_at: string;
  imported_by: string;
};
export type Finding = { code: string; severity: string; message: string; count: number; examples: string[] };
export type QualityReport = { status: string; findings: Finding[]; stats: Record<string, unknown> };
export type ImportableFile = {
  file_name: string;
  size_bytes: number;
  symbol: string | null;
  timeframe: string | null;
  source_type: string | null;
  source: string | null;
};
export type StrategyInfo = {
  strategy_id: string;
  version: string;
  display_name: string;
  kind: string;
  description: string;
  default_params: Record<string, unknown>;
};
export type RuleSetInfo = { id: string; venue: string | null; symbols: string[]; verified: boolean; source: string };
export type FeeProfile = { schedule_id: string; venue: string; maker_bps: string; taker_bps: string; verified: boolean; source: string };
export type Profiles = { rule_sets: RuleSetInfo[]; fee_profiles: FeeProfile[]; note: string };

export type RunSummary = {
  net_pnl: string | null;
  net_return_pct: string | null;
  ending_equity_marked: string | null;
  max_drawdown_pct: string | null;
  round_trips: number | null;
  fills: number | null;
  status: string;
  risk_verdict: string;
  research_grade: boolean;
} | null;
export type RunRow = {
  id: string;
  label: string;
  status: "QUEUED" | "RUNNING" | "COMPLETED" | "FAILED" | "CANCELLED";
  progress_pct: number;
  result_status: string | null;
  risk_verdict: string | null;
  research_grade: boolean | null;
  strategy_id: string | null;
  strategy_version: string | null;
  config_hash: string | null;
  config: Record<string, unknown> | null;
  git_commit: string;
  dataset_version_ids: Record<string, string>;
  execution_model_version: string;
  fee_schedule_version: string;
  risk_policy_version: string;
  seed: number;
  result_checksum: string | null;
  error: string | null;
  notes: string[];
  created_by: string;
  queued_at: string;
  started_at: string | null;
  finished_at: string | null;
  summary: RunSummary;
  labels: string[];
};
export type Performance = {
  returns: Record<string, Metric>;
  risk: Record<string, Metric>;
  trading: Record<string, Metric>;
  portfolio: Record<string, Metric>;
  meta: Record<string, Metric>;
};
export type EquityPoint = { ts: string; equity: string; cash: string; exposure: string; drawdown: string };
export type FillRow = {
  fill_id: number;
  order_id: number;
  symbol: string;
  side: string;
  ts: string;
  price: string;
  quantity: string;
  liquidity: string;
  fee_asset: string;
  fee_amount: string;
  slippage_cost: string;
  realized_pnl: string;
  tag: string;
};
export type RiskReport = {
  verdict: string | null;
  result_status: string | null;
  limits: Record<string, unknown> | null;
  events: { seq: number; ts: string; kind: string; detail: string; equity: string }[];
  note: string;
};
export type ValidationRow = {
  id: string;
  kind: string;
  strategy_id: string;
  title: string;
  cycle: number;
  oos_reused: boolean;
  research_grade: boolean;
  created_at: string;
  sample_type?: string;
  strategy_version?: string | null;
  source_artifact?: string | null;
  generated_at?: string | null;
  content_hash?: string | null;
  source_datasets?: Record<string, { checksum?: string; timeframe?: string; root_source_type?: string; validation?: string; rows?: number }> | null;
  warnings?: string[];
  pooled?: Record<string, unknown> | null;
  result?: Record<string, unknown>;
  advisory?: Record<string, unknown>;
};

/** POST helper: CSRF header from the session + a fresh Idempotency-Key per user action. */
export async function post<T>(path: string, body: unknown, csrf: string, key?: string): Promise<T> {
  return api<T>(path, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "X-CSRF-Token": csrf,
      "Idempotency-Key": key ?? crypto.randomUUID(),
    },
    body: JSON.stringify(body),
  });
}

/** Human-readable error from a failed API call (never raw server internals). */
export async function explain(e: unknown): Promise<string> {
  if (e instanceof ApiError) {
    if (e.status === 403) return "Not permitted for your role.";
    if (e.status === 429) return "The research queue is full; try again shortly.";
    return `Request failed (${e.status}).`;
  }
  return "Network error.";
}

export function canWriteResearch(role: string | undefined): boolean {
  return role === "operator" || role === "admin";
}

export function download(name: string, mime: string, text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: mime }));
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

export function csvEscape(v: unknown): string {
  const s = v === null || v === undefined ? "" : String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}

export function toCsv(rows: Record<string, unknown>[]): string {
  if (!rows.length) return "";
  const cols = Object.keys(rows[0]);
  return [cols.join(","), ...rows.map((r) => cols.map((c) => csvEscape(r[c])).join(","))].join("\n");
}
