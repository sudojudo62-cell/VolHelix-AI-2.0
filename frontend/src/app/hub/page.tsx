"use client";

import { useEffect, useState } from "react";
import { Boxes, ExternalLink, RefreshCw } from "lucide-react";

interface HubProject {
  id: string;
  name: string;
  kind: string;
  description: string;
  repo?: string;
  dashboard_url: string;
  status: "online" | "offline" | "degraded" | "unconfigured";
  latency_ms: number | null;
  portfolio?: { total_value?: number; cash?: number } | null;
  auto_trader?: { is_running: boolean; total_automated_trades: number; guardian_active: boolean } | null;
}

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "";
const STATUS_COLOR: Record<HubProject["status"], string> = {
  online: "text-[#20b26c] bg-[#20b26c]/15",
  degraded: "text-[#f7a600] bg-[#f7a600]/15",
  offline: "text-[#e5484d] bg-[#e5484d]/15",
  unconfigured: "text-[#878996] bg-[#18191f]",
};

export default function HubPage() {
  const [projects, setProjects] = useState<HubProject[]>([]);
  const [error, setError] = useState(false);
  const [preview, setPreview] = useState<HubProject | null>(null);

  const load = async () => {
    try {
      const res = await fetch(`${API_BASE}/api/hub/overview`);
      if (!res.ok) throw new Error();
      setProjects((await res.json()).projects);
      setError(false);
    } catch {
      setError(true);
    }
  };

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    load();
    const t = setInterval(load, 10000);
    return () => clearInterval(t);
  }, []);

  return (
    <div className="p-6 space-y-6 text-[#f5f5f5]">
      <div className="flex items-center justify-between">
        <h1 className="flex items-center gap-2 text-lg font-extrabold">
          <Boxes className="w-5 h-5 text-[#f7a600]" /> Unified Hub
        </h1>
        <button onClick={load} className="p-1.5 rounded hover:bg-[#18191f] text-[#878996]" aria-label="Refresh">
          <RefreshCw className="w-4 h-4" />
        </button>
      </div>

      {error && <p className="text-xs font-mono text-[#e5484d]">Hub backend unreachable.</p>}

      <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
        {projects.map((p) => (
          <div key={p.id} className="p-4 rounded-lg bg-[#18191f] border border-[#26282f] space-y-3">
            <div className="flex items-center justify-between">
              <span className="font-bold text-sm">{p.name}</span>
              <span className={`px-1.5 py-0.5 rounded text-[10px] font-mono font-bold uppercase ${STATUS_COLOR[p.status]}`}>
                {p.status}
                {p.latency_ms ? ` · ${p.latency_ms}ms` : ""}
              </span>
            </div>
            <p className="text-xs text-[#878996]">{p.description}</p>
            {p.auto_trader && (
              <dl className="grid grid-cols-3 gap-2 text-[11px] font-mono">
                <div><dt className="text-[#5e6673]">Auto-pilot</dt><dd>{p.auto_trader.is_running ? "ON" : "OFF"}</dd></div>
                <div><dt className="text-[#5e6673]">Guardian</dt><dd>{p.auto_trader.guardian_active ? "ACTIVE" : "DOWN"}</dd></div>
                <div><dt className="text-[#5e6673]">Trades</dt><dd>{p.auto_trader.total_automated_trades}</dd></div>
              </dl>
            )}
            <div className="flex gap-3 text-[11px] font-mono">
              {p.id === "volhelix" ? (
                <a href={p.dashboard_url} className="text-[#f7a600] hover:underline">Open dashboard</a>
              ) : (
                <>
                  <button onClick={() => setPreview(p)} className="text-[#f7a600] hover:underline">Preview</button>
                  <a href={p.dashboard_url} target="_blank" rel="noreferrer" className="inline-flex items-center gap-1 text-[#878996] hover:text-[#f5f5f5]">
                    Open <ExternalLink className="w-3 h-3" />
                  </a>
                </>
              )}
            </div>
          </div>
        ))}
      </div>

      {preview && (
        <div className="rounded-lg border border-[#26282f] overflow-hidden">
          <div className="flex justify-between px-3 py-2 bg-[#18191f] text-xs font-mono">
            <span>{preview.name}</span>
            <button onClick={() => setPreview(null)} className="text-[#878996] hover:text-[#f5f5f5]">Close</button>
          </div>
          <iframe src={preview.dashboard_url} title={preview.name} className="w-full h-[70vh] bg-black" />
        </div>
      )}
    </div>
  );
}
