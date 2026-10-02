// Live Desk API client. Every value shown on the Live Desk comes from these calls; there are no client-side fallbacks.
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export interface LiveStatus {
  mode: "disabled" | "test" | "live";
  trading_enabled: boolean;
  order_mode: string;
  kill_switch: boolean;
  keys_configured: boolean;
  token_configured: boolean;
  limits: { max_order_usdt: number; max_daily_notional_usdt: number; min_order_usdt: number; allowlist: string[] };
  executed_notional_today: number;
  confirm_phrase_required: boolean;
  feeds: { flow_enabled: boolean; streams: Record<string, string> };
}

export interface LiveSnapshot {
  symbol: string;
  live: boolean;
  reason?: string;
  price?: number;
  ts: number;
  features?: {
    spread_bps: number;
    book_imbalance: number;
    delta_percent: number;
    session_cvd: number;
    price_vs_value: string;
    buy_sell_ratio: number;
  };
  cvd_scope?: string;
  closed_bars_1m?: number;
  confluence?: { score: number; veto: boolean; veto_reason: string | null; reasons: string[] };
  health?: { status: string; lag_ms: number; messages_per_sec: number; gap_events: number } | null;
}

export interface LiveSignal {
  id: number;
  ts: number;
  symbol: string;
  source: string;
  score: number | null;
  valid: boolean | null;
  decision: string | null;
  reason: string | null;
  price: number;
  ret_1h: number | null;
  ret_4h: number | null;
  ret_24h: number | null;
}

export interface CalibrationBucket { bucket: number; n: number; avg_ret: number; hit_rate: number }

export interface LiveAccount {
  can_trade: boolean;
  equity: number;
  usdt_free: number;
  balances: Record<string, { free: number; locked: number; total: number; value_usdt: number }>;
  unpriced_assets: string[];
}

export interface LiveOrderRow {
  id: number; ts: number; client_order_id: string; symbol: string; side: string; notional: number;
  mode: string; status: string; reason: string;
}

export class LiveApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

async function call<T>(path: string, token?: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}/api/live${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(token ? { "X-Live-Token": token } : {}), ...(init?.headers || {}) },
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    const d = body?.detail;
    throw new LiveApiError(res.status, typeof d === "string" ? d : d?.message || `HTTP ${res.status}`);
  }
  return body as T;
}

export const getStatus = () => call<LiveStatus>("/status");
export const getSnapshots = () => call<{ snapshots: LiveSnapshot[]; flow_enabled: boolean }>("/snapshots");
export const getSignals = (limit = 50) => call<{ signals: LiveSignal[] }>(`/signals?limit=${limit}`);
export const getCalibration = (horizon = "4h") => call<{ buckets: CalibrationBucket[] }>(`/signals/calibration?horizon=${horizon}`);
export const getAccount = (token: string) => call<LiveAccount>("/account", token);
export const getOrders = (token: string) => call<{ orders: LiveOrderRow[] }>("/orders?limit=25", token);
export const setKillSwitch = (token: string, engaged: boolean) =>
  call<{ kill_switch: boolean }>("/kill-switch", token, { method: "POST", body: JSON.stringify({ engaged }) });
export const placeOrder = (token: string, body: { symbol: string; quote_qty: number; stop_loss: number; take_profit: number; confirm?: string }) =>
  call<Record<string, unknown>>("/order", token, { method: "POST", body: JSON.stringify(body) });
export const closePosition = (token: string, body: { symbol: string; confirm?: string }) =>
  call<Record<string, unknown>>("/close", token, { method: "POST", body: JSON.stringify(body) });
