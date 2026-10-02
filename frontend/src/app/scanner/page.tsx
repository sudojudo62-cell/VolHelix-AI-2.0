"use client";

import { useEffect, useState } from "react";
import { ScanSearch, ShieldCheck, AlertTriangle } from "lucide-react";
import { ScanSnapshot, getScan } from "../../lib/scanner";

const HEALTH_STYLE: Record<string, string> = {
  ok: "bg-[#2ebd85]/15 text-[#2ebd85]",
  degraded: "bg-[#e5484d]/20 text-[#e5484d]",
  insufficient_data: "bg-[#f7a600]/15 text-[#f7a600]",
};
const fmt = (n: number | null | undefined, d = 2) => (n === null || n === undefined ? "—" : n.toFixed(d));

export default function ScannerPage() {
  const [snap, setSnap] = useState<ScanSnapshot | null>(null);
  const [down, setDown] = useState(false);

  useEffect(() => {
    const load = async () => {
      try { setSnap(await getScan()); setDown(false); } catch { setDown(true); }
    };
    load();
    const t = setInterval(load, 15000);
    return () => clearInterval(t);
  }, []);

  const h = snap?.health;
  return (
    <div className="p-4 space-y-4 text-xs font-mono text-[#d1d4dc]">
      <div className="flex items-center gap-2 text-sm font-bold"><ScanSearch size={16} /> Pair Scanner
        <span className="px-2 py-0.5 rounded bg-[#26282f] text-[#878996]">PAPER ONLY</span>
        {snap && (
          <span className={`px-2 py-0.5 rounded ${snap.live_authorized ? "bg-[#f7a600]/15 text-[#f7a600]" : "bg-[#26282f] text-[#878996]"}`}>
            {snap.live_authorized ? "live tickets authorized (manual)" : "not live-authorized"}
          </span>
        )}
      </div>
      {down && <div className="p-3 rounded bg-[#e5484d]/15 text-[#e5484d]">Backend unreachable.</div>}
      {snap && !snap.available && (
        <div className="p-3 rounded bg-[#18191f] border border-[#26282f]">No snapshot: {snap.reason}</div>
      )}
      {snap?.available && h && (
        <>
          <div className="flex flex-wrap items-center gap-3 p-3 rounded bg-[#18191f] border border-[#26282f]">
            <span className={`px-2 py-0.5 rounded font-bold ${HEALTH_STYLE[h.status] || ""}`}>
              {h.status === "ok" ? <ShieldCheck size={12} className="inline mr-1" /> : <AlertTriangle size={12} className="inline mr-1" />}
              model health: {h.status}
            </span>
            <span>rank IC {fmt(h.mean_rank_ic, 3)}</span>
            <span>t {fmt(h.ic_t_stat, 2)}</span>
            <span>{h.n_bars} bars / {h.window_days}d</span>
            <span className="text-[#878996]">{snap.venue} · {snap.interval} · {snap.horizon_h}h horizon · {snap.n_assets} assets · {fmt((snap.age_ms ?? 0) / 60000, 0)} min old</span>
          </div>
          {h.status !== "ok" && (
            <div className="p-3 rounded bg-[#f7a600]/10 text-[#f7a600]">The model has not shown out-of-sample skill recently; treat the ranking as uninformative.</div>
          )}
          <div className="rounded-lg bg-[#18191f] border border-[#26282f] overflow-x-auto">
            <table className="w-full">
              <thead className="text-[#878996] uppercase"><tr>
                {["#", "Asset", "Score", "P(top-" + snap.top_k + ")", "Price", "24h quote vol", "Flags"].map((c) => <th key={c} className="px-3 py-2 text-left">{c}</th>)}
              </tr></thead>
              <tbody>
                {snap.ranking?.map((r) => (
                  <tr key={r.asset} className={`border-t border-[#26282f] ${r.in_top_k ? "bg-[#2ebd85]/5" : ""}`}>
                    <td className="px-3 py-1.5">{r.rank}</td>
                    <td className="px-3 py-1.5 font-bold">{r.asset}</td>
                    <td className="px-3 py-1.5">{fmt(r.score, 4)}</td>
                    <td className="px-3 py-1.5">{r.top_k_probability === null ? "—" : `${(r.top_k_probability * 100).toFixed(0)}%`}</td>
                    <td className="px-3 py-1.5">{fmt(r.price, r.price < 1 ? 5 : 2)}</td>
                    <td className="px-3 py-1.5">{Math.round(r.quote_volume_recent).toLocaleString()}</td>
                    <td className="px-3 py-1.5 text-[#f7a600]">{r.flags.join(", ")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="text-[#878996]">{snap.disclaimer}</p>
        </>
      )}
    </div>
  );
}
