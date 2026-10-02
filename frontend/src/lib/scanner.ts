// Pair Scanner API client. Values come straight from the backend snapshot; nothing is filled in client-side.
const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";

export interface ScanRow {
  asset: string; rank: number; score: number; top_k_probability: number | null; price: number;
  quote_volume_recent: number; vol_per_bar: number; features_z: Record<string, number>; in_top_k: boolean; flags: string[];
}

export interface ScanSnapshot {
  available: boolean; reason?: string; paper_only: boolean; live_authorized: boolean;
  venue?: string; interval?: string; horizon_h?: number; top_k?: number; n_assets?: number; generated_at?: number; age_ms?: number;
  dropped_assets?: Record<string, string> | string[];
  health?: { mean_rank_ic: number | null; ic_t_stat: number | null; n_bars: number; window_days: number; status: string; note: string };
  model?: { type: string; train_days: number; coefficients: Record<string, number> };
  ranking?: ScanRow[]; disclaimer?: string;
}

export async function getScan(): Promise<ScanSnapshot> {
  const r = await fetch(`${API_BASE}/api/scanner/latest`, { cache: "no-store" });
  if (!r.ok) throw new Error(`scanner ${r.status}`);
  return r.json();
}
