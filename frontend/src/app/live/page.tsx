"use client";

import { useCallback, useEffect, useState } from "react";
import { Radio, ShieldAlert, Power } from "lucide-react";
import {
  LiveStatus, LiveSnapshot, LiveSignal, CalibrationBucket, LiveAccount, LiveOrderRow, LiveApiError,
  getStatus, getSnapshots, getSignals, getCalibration, getAccount, getOrders, setKillSwitch, placeOrder, closePosition,
} from "../../lib/live";

const CONFIRM_PHRASE = "I UNDERSTAND THIS USES REAL FUNDS";
const MODE_STYLE = {
  disabled: "bg-[#26282f] text-[#878996]",
  test: "bg-[#f7a600]/15 text-[#f7a600]",
  live: "bg-[#e5484d]/20 text-[#e5484d]",
} as const;

const fmt = (n: number | null | undefined, d = 2) => (n === null || n === undefined ? "—" : n.toFixed(d));
const pct = (n: number | null | undefined) => (n === null || n === undefined ? "—" : `${(n * 100).toFixed(2)}%`);

function Card({ title, children, right }: { title: string; children: React.ReactNode; right?: React.ReactNode }) {
  return (
    <section className="rounded-lg bg-[#18191f] border border-[#26282f]">
      <header className="flex items-center justify-between px-4 py-2.5 border-b border-[#26282f] text-xs font-bold uppercase tracking-wider text-[#878996] font-mono">
        <span>{title}</span>
        {right}
      </header>
      <div className="p-4 text-xs font-mono overflow-x-auto">{children}</div>
    </section>
  );
}

export default function LivePage() {
  const [status, setStatus] = useState<LiveStatus | null>(null);
  const [snaps, setSnaps] = useState<LiveSnapshot[]>([]);
  const [signals, setSignals] = useState<LiveSignal[]>([]);
  const [calib, setCalib] = useState<CalibrationBucket[]>([]);
  const [token, setToken] = useState("");
  const [account, setAccount] = useState<LiveAccount | null>(null);
  const [orders, setOrders] = useState<LiveOrderRow[]>([]);
  const [gatedError, setGatedError] = useState<string | null>(null);
  const [backendDown, setBackendDown] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [form, setForm] = useState({ symbol: "", quote: "", sl: "", tp: "", confirm: "" });

  useEffect(() => {
    // eslint-disable-next-line react-hooks/set-state-in-effect
    try { setToken(sessionStorage.getItem("volhelix.live.token") || ""); } catch { /* ignore */ }
  }, []);

  const loadPublic = useCallback(async () => {
    try {
      const [st, sn] = await Promise.all([getStatus(), getSnapshots()]);
      setStatus(st); setSnaps(sn.snapshots); setBackendDown(false);
    } catch { setBackendDown(true); }
  }, []);

  const loadSlow = useCallback(async () => {
    try {
      const [sg, cb] = await Promise.all([getSignals(40), getCalibration("4h")]);
      setSignals(sg.signals); setCalib(cb.buckets);
    } catch { /* shown via backendDown */ }
  }, []);

  const loadGated = useCallback(async () => {
    if (!token) { setAccount(null); setOrders([]); setGatedError(null); return; }
    try {
      const [a, o] = await Promise.allSettled([getAccount(token), getOrders(token)]);
      if (o.status === "fulfilled") setOrders(o.value.orders);
      if (a.status === "fulfilled") { setAccount(a.value); setGatedError(null); }
      else { setAccount(null); setGatedError((a.reason as Error).message); }
      if (o.status === "rejected" && a.status === "fulfilled") setGatedError((o.reason as Error).message);
    } catch (e) { setGatedError((e as Error).message); }
  }, [token]);

  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { loadPublic(); const t = setInterval(loadPublic, 2000); return () => clearInterval(t); }, [loadPublic]);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { loadSlow(); const t = setInterval(loadSlow, 10000); return () => clearInterval(t); }, [loadSlow]);
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { loadGated(); const t = setInterval(loadGated, 10000); return () => clearInterval(t); }, [loadGated]);

  const saveToken = (v: string) => {
    setToken(v);
    try { sessionStorage.setItem("volhelix.live.token", v); } catch { /* ignore */ }
  };

  const act = async (fn: () => Promise<unknown>) => {
    setResult(null);
    try { setResult(JSON.stringify(await fn(), null, 2)); }
    catch (e) { setResult(`${e instanceof LiveApiError ? `HTTP ${e.status}: ` : ""}${(e as Error).message}`); }
    loadPublic(); loadGated();
  };

  const mode = status?.mode ?? "disabled";
  const isLive = mode === "live";
  const symbols = status?.limits.allowlist ?? [];
  const symbol = form.symbol || symbols[0] || "";

  return (
    <div className="p-6 space-y-5 text-[#f5f5f5]">
      <div className="flex flex-wrap items-center gap-3">
        <h1 className="flex items-center gap-2 text-lg font-extrabold"><Radio className="w-5 h-5 text-[#e5484d]" /> Live Desk</h1>
        <span className={`px-2 py-0.5 rounded text-[11px] font-mono font-bold uppercase ${MODE_STYLE[mode]}`}>
          {mode === "disabled" ? "read-only" : mode === "test" ? "order test mode (nothing executes)" : "LIVE — real funds"}
        </span>
        {status?.kill_switch && <span className="px-2 py-0.5 rounded bg-[#e5484d]/20 text-[#e5484d] text-[11px] font-mono font-bold">KILL SWITCH ENGAGED</span>}
        {backendDown && <span className="text-xs font-mono text-[#e5484d]">Backend unreachable — showing nothing rather than stale numbers.</span>}
        <span className="ml-auto text-[11px] font-mono text-[#5e6673]">Production market streams only · no simulated data</span>
      </div>

      <Card title="Live streams & signals">
        {!status?.feeds.flow_enabled && status && <p className="text-[#878996]">Order-flow streams are disabled (FLOW_ENABLED=false).</p>}
        <table className="w-full text-left">
          <thead className="text-[#5e6673]"><tr>
            <th className="pr-4">Symbol</th><th className="pr-4">Stream</th><th className="pr-4">Price</th><th className="pr-4">Spread bps</th>
            <th className="pr-4">Book imb.</th><th className="pr-4">Bar Δ%</th><th className="pr-4">CVD (buffer)</th><th className="pr-4">Value</th><th>Confluence</th>
          </tr></thead>
          <tbody>
            {snaps.map((s) => (
              <tr key={s.symbol} className="border-t border-[#26282f]">
                <td className="pr-4 py-1.5 font-bold">{s.symbol}</td>
                {s.live && s.features ? (
                  <>
                    <td className="pr-4 text-[#20b26c]">LIVE · {s.health?.lag_ms ?? "—"}ms</td>
                    <td className="pr-4">{fmt(s.price)}</td>
                    <td className="pr-4">{fmt(s.features.spread_bps, 2)}</td>
                    <td className="pr-4">{fmt(s.features.book_imbalance, 2)}</td>
                    <td className="pr-4">{fmt(s.features.delta_percent * 100, 1)}</td>
                    <td className="pr-4">{fmt(s.features.session_cvd, 2)}</td>
                    <td className="pr-4">{s.features.price_vs_value}</td>
                    <td className={s.confluence?.veto ? "text-[#878996]" : "text-[#f7a600]"}>
                      {s.confluence?.veto ? `veto: ${s.confluence.veto_reason}` : `${fmt((s.confluence?.score ?? 0) * 100, 0)}%`}
                    </td>
                  </>
                ) : (
                  <td colSpan={8} className="text-[#e5484d]">NO LIVE DATA — {s.reason ?? "unavailable"}</td>
                )}
              </tr>
            ))}
            {snaps.length === 0 && <tr><td colSpan={9} className="text-[#878996] py-2">No subscribed streams.</td></tr>}
          </tbody>
        </table>
        <p className="mt-2 text-[#5e6673]">CVD covers buffered trades only, not the full UTC session.</p>
      </Card>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card title="Logged live signals (forward-return labeled)">
          <table className="w-full text-left">
            <thead className="text-[#5e6673]"><tr><th className="pr-3">Time</th><th className="pr-3">Symbol</th><th className="pr-3">Score</th><th className="pr-3">Decision</th><th className="pr-3">1h</th><th className="pr-3">4h</th><th>24h</th></tr></thead>
            <tbody>
              {signals.map((g) => (
                <tr key={g.id} className="border-t border-[#26282f]">
                  <td className="pr-3 py-1">{new Date(g.ts).toLocaleTimeString()}</td><td className="pr-3">{g.symbol}</td>
                  <td className="pr-3">{g.score === null ? "—" : `${(g.score * 100).toFixed(0)}%`}</td><td className="pr-3">{g.decision ?? "—"}</td>
                  <td className="pr-3">{pct(g.ret_1h)}</td><td className="pr-3">{pct(g.ret_4h)}</td><td>{pct(g.ret_24h)}</td>
                </tr>
              ))}
              {signals.length === 0 && <tr><td colSpan={7} className="text-[#878996] py-2">No live signals logged yet (needs live streams).</td></tr>}
            </tbody>
          </table>
        </Card>
        <Card title="Calibration: avg 4h forward return by score (before fees)">
          <table className="w-full text-left">
            <thead className="text-[#5e6673]"><tr><th>Score bucket</th><th>n</th><th>avg return</th><th>hit rate</th></tr></thead>
            <tbody>
              {calib.map((b) => (
                <tr key={b.bucket} className="border-t border-[#26282f]"><td className="py-1">{(b.bucket * 100).toFixed(0)}–{(b.bucket * 100 + 9).toFixed(0)}%</td><td>{b.n}</td><td>{pct(b.avg_ret)}</td><td>{pct(b.hit_rate)}</td></tr>
              ))}
              {calib.length === 0 && <tr><td colSpan={4} className="text-[#878996] py-2">No labeled signals yet; labels arrive 4h after each signal.</td></tr>}
            </tbody>
          </table>
        </Card>
      </div>

      <Card title="Account & orders (requires access token)">
        <div className="flex flex-wrap items-center gap-3 mb-3">
          <input type="password" value={token} onChange={(e) => saveToken(e.target.value)} placeholder="X-Live-Token"
            className="bg-[#121214] border border-[#26282f] rounded px-2 py-1 w-64" aria-label="Live access token" />
          <span className="text-[#5e6673]">Kept in this tab only (sessionStorage).</span>
          {status && !status.token_configured && <span className="text-[#e5484d]">LIVE_API_TOKEN is not set on the server; account and orders are disabled.</span>}
          {status && !status.keys_configured && <span className="text-[#878996]">Live API keys not configured.</span>}
          {gatedError && token && <span className="text-[#e5484d]">{gatedError}</span>}
        </div>
        {account && (
          <div className="space-y-3">
            <div className="flex gap-6">
              <div><div className="text-[#5e6673]">Equity (USDT)</div><div className="text-sm font-bold">{fmt(account.equity)}</div></div>
              <div><div className="text-[#5e6673]">Free USDT</div><div className="text-sm font-bold">{fmt(account.usdt_free)}</div></div>
              <div><div className="text-[#5e6673]">Can trade</div><div className="text-sm font-bold">{account.can_trade ? "yes" : "NO"}</div></div>
              {status && <div><div className="text-[#5e6673]">Executed today / cap</div><div className="text-sm font-bold">{fmt(status.executed_notional_today)} / {fmt(status.limits.max_daily_notional_usdt)}</div></div>}
            </div>
            {account.unpriced_assets.length > 0 && <p className="text-[#f7a600]">Not valued in equity (no USDT pair): {account.unpriced_assets.join(", ")}</p>}
            <table className="w-full text-left"><thead className="text-[#5e6673]"><tr><th>Asset</th><th>Free</th><th>Locked</th><th>Value USDT</th><th /></tr></thead>
              <tbody>{Object.entries(account.balances).map(([a, b]) => (
                <tr key={a} className="border-t border-[#26282f]"><td className="py-1">{a}</td><td>{b.free}</td><td>{b.locked}</td><td>{fmt(b.value_usdt)}</td>
                  <td>{a !== "USDT" && symbols.includes(`${a}USDT`) && (
                    <button className="text-[#e5484d] hover:underline" onClick={() => act(() => closePosition(token, { symbol: `${a}USDT`, confirm: form.confirm || undefined }))}>close</button>)}</td></tr>))}
              </tbody></table>
          </div>
        )}
        {token && (
          <div className="mt-4 grid gap-3 md:grid-cols-6 items-end">
            <label className="flex flex-col gap-1">Symbol
              <select value={symbol} onChange={(e) => setForm({ ...form, symbol: e.target.value })} className="bg-[#121214] border border-[#26282f] rounded px-2 py-1">
                {symbols.map((s) => <option key={s}>{s}</option>)}</select></label>
            {(["quote", "sl", "tp"] as const).map((k) => (
              <label key={k} className="flex flex-col gap-1">{k === "quote" ? "USDT amount" : k === "sl" ? "Stop-loss price" : "Take-profit price"}
                <input inputMode="decimal" value={form[k]} onChange={(e) => setForm({ ...form, [k]: e.target.value })} className="bg-[#121214] border border-[#26282f] rounded px-2 py-1" /></label>))}
            {isLive && (
              <label className="flex flex-col gap-1 md:col-span-2">Type to confirm: {CONFIRM_PHRASE}
                <input value={form.confirm} onChange={(e) => setForm({ ...form, confirm: e.target.value })} className="bg-[#121214] border border-[#e5484d]/50 rounded px-2 py-1" /></label>)}
            <button disabled={mode === "disabled"} className={`px-3 py-1.5 rounded font-bold disabled:opacity-40 ${isLive ? "bg-[#e5484d] text-white" : "bg-[#f7a600] text-[#121214]"}`}
              onClick={() => act(() => placeOrder(token, { symbol, quote_qty: Number(form.quote), stop_loss: Number(form.sl), take_profit: Number(form.tp), confirm: form.confirm || undefined }))}>
              {mode === "disabled" ? "Trading disabled" : isLive ? "Place LIVE order" : "Validate order (test)"}</button>
            <button className="px-3 py-1.5 rounded border border-[#26282f] flex items-center gap-1.5 justify-center" onClick={() => act(() => setKillSwitch(token, !status?.kill_switch))}>
              <Power className="w-3.5 h-3.5" />{status?.kill_switch ? "Release kill switch" : "Engage kill switch"}</button>
          </div>
        )}
        {mode === "live" && <p className="mt-3 flex items-center gap-1.5 text-[#e5484d]"><ShieldAlert className="w-4 h-4" /> Orders are real. Entries are risk-gated, capped at {fmt(status?.limits.max_order_usdt)} USDT, and exits are placed as an OCO immediately after the fill.</p>}
        {result && <pre className="mt-3 p-3 rounded bg-[#121214] border border-[#26282f] whitespace-pre-wrap break-all max-h-64 overflow-auto">{result}</pre>}
        {orders.length > 0 && (
          <table className="w-full text-left mt-4"><thead className="text-[#5e6673]"><tr><th>Time</th><th>Symbol</th><th>Notional</th><th>Mode</th><th>Status</th><th>Reason</th></tr></thead>
            <tbody>{orders.map((o) => (<tr key={o.id} className="border-t border-[#26282f]"><td className="py-1 pr-3">{new Date(o.ts).toLocaleString()}</td><td className="pr-3">{o.symbol}</td><td className="pr-3">{fmt(o.notional)}</td><td className="pr-3">{o.mode}</td><td className="pr-3">{o.status}</td><td className="text-[#878996]">{o.reason}</td></tr>))}</tbody></table>
        )}
      </Card>
    </div>
  );
}
