"use client";
import { Suspense, useEffect, useMemo, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useApi } from "@/lib/api";
import { fmtNum, fmtPrice } from "@/lib/format";
import { CandleChart, type ChartMarker, type PriceLine } from "@/components/Chart";
import { ConfidenceBlock } from "@/components/Report";
import { Empty, ErrorBox, Loading, PageHead, Panel, Pill, SourceTag, Stat } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
type Det = { kind: string; direction: string; i: number; t: number; price: number; title: string; evidence: Record<string, any> };
type Resp = {
  symbol: string; tf: string; asset_class: string; source: string; fetched_at: number;
  candles: { time: number; open: number; high: number; low: number; close: number; volume: number }[];
  provisional: { t: number; o: number; h: number; l: number; c: number; v: number } | null; gaps: number;
  trend: string; structure_label: string; detections: Det[]; pools: { buy_side: { price: number }[]; sell_side: { price: number }[] };
  atr_pct: number; rsi: number; rel_volume: number | null; ema20: (number | null)[]; ema50: (number | null)[];
  setups: { signal: any; plan: any; rejection: any; regime: string; probability: any }[]; latency_ms: { fetch: number; analysis: number };
};

const LAYERS = [
  { key: "structure", label: "BOS / CHoCH" }, { key: "sweeps", label: "Confirmed sweeps" }, { key: "liquidity", label: "Liquidity pools" },
  { key: "fvg", label: "Open FVGs" }, { key: "ob", label: "Order blocks" }, { key: "sr", label: "Support / resistance" },
  { key: "patterns", label: "Candle patterns" }, { key: "plan", label: "Setup levels" },
];
const CRYPTO = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD", "ADA-USD", "AVAX-USD", "LINK-USD", "SUI-USD", "LTC-USD"];
const TFS = ["5m", "15m", "1h", "6h", "1d"];

function evidenceText(d: Det): string {
  const e = d.evidence;
  switch (d.kind) {
    case "liquidity_sweep":
      return `Breached ${e.level_type} ${fmtPrice(e.swept_level)} by ${e.penetration_pct.toFixed(2)}%, closed back inside at ${fmtPrice(e.close)}. ` +
        `Volume ${e.volume_ratio ? e.volume_ratio.toFixed(2) + "× avg" : "n/a"} (${e.volume_confirmed ? "confirms" : "does not confirm"}). ` +
        `Reversal ${e.reversal_confirmed ? "confirmed by a later close beyond the sweep candle" : e.confirmation_pending ? "confirmation pending" : "NOT confirmed"}.`;
    case "bos": case "choch":
      return `Close ${fmtPrice(e.close)} beyond swing level ${fmtPrice(e.broken_level)} (${e.close_beyond_pct.toFixed(2)}%); prior trend ${e.prior_trend}; volume ${e.volume_ratio ? e.volume_ratio.toFixed(2) + "×" : "n/a"}.`;
    case "fvg": return `Gap ${fmtPrice(e.gap_low)}–${fmtPrice(e.gap_high)} (${e.gap_pct.toFixed(2)}%), ${e.open ? "still open" : "filled"}.`;
    case "order_block": return `Zone ${fmtPrice(e.zone_low)}–${fmtPrice(e.zone_high)} before ${e.caused.toUpperCase()}; ${e.mitigated_at != null ? "mitigated" : "unmitigated"}.`;
    case "failed_breakout": return `Closed beyond ${fmtPrice(e.level)} then back inside within 3 bars.`;
    case "sr_level": return `${e.touches} swing touches, ${e.distance_pct.toFixed(2)}% from price${e.equal_levels ? ", equal highs/lows" : ""}.`;
    case "divergence": return `Price ${fmtPrice(e.price_1)} → ${fmtPrice(e.price_2)}, RSI ${e.rsi_1.toFixed(1)} → ${e.rsi_2.toFixed(1)}.`;
    case "volume_imbalance": return `Relative volume ${e.relative_volume.toFixed(2)}×, body ${(e.body_to_range * 100).toFixed(0)}% of range.`;
    case "volatility_expansion": return `ATR(5)/ATR(50) = ${e.atr5_over_atr50.toFixed(2)}.`;
    default: return Object.entries(e).map(([k, v]) => `${k} ${typeof v === "number" ? v.toFixed(2) : v}`).join(", ");
  }
}

function StructureInner() {
  const sp = useSearchParams();
  const [symbol, setSymbol] = useState(sp.get("symbol") || "BTC-USD");
  const [cls, setCls] = useState(sp.get("cls") || "crypto");
  const [tf, setTf] = useState(sp.get("tf") || "15m");
  const [layers, setLayers] = useState<Record<string, boolean>>(Object.fromEntries(LAYERS.map(l => [l.key, !["patterns", "ob"].includes(l.key)])));
  const pools = useApi<{ assets: { symbol: string; display: string; asset_class: string }[] }>("/api/agent/assets");
  const d = useApi<Resp>(`/api/structure?symbol=${encodeURIComponent(symbol)}&tf=${tf}&asset_class=${cls}&bars=${cls === "memecoin" ? 500 : 600}`, 60_000);
  useEffect(() => { if (cls === "memecoin" && !["1m", "5m", "15m", "1h", "1d"].includes(tf)) setTf("5m"); }, [cls, tf]);

  const r = d.data;
  const view = useMemo(() => {
    if (!r) return { markers: [] as ChartMarker[], lines: [] as PriceLine[] };
    const cut = r.candles.length > 0 ? r.candles[Math.max(0, r.candles.length - 120)].time : 0;   // annotate the most recent 120 bars
    const m: ChartMarker[] = [];
    const L: PriceLine[] = [];
    const up = "#2fb67c", dn = "#e5534b", am = "#f0a634", info = "#5aa9e6";
    for (const x of r.detections) {
      if (x.t < cut) continue;
      if (layers.structure && (x.kind === "bos" || x.kind === "choch"))
        m.push({ time: x.t, position: x.direction === "bullish" ? "belowBar" : "aboveBar", color: x.kind === "choch" ? am : x.direction === "bullish" ? up : dn, shape: x.direction === "bullish" ? "arrowUp" : "arrowDown", text: x.kind.toUpperCase() });
      if (layers.sweeps && x.kind === "liquidity_sweep" && (x.evidence.volume_confirmed || x.evidence.reversal_confirmed))
        m.push({ time: x.t, position: x.direction === "bullish" ? "belowBar" : "aboveBar", color: x.evidence.reversal_confirmed ? info : "#8593a1", shape: "circle", text: x.direction === "bullish" ? "SSL" : "BSL" });
      if (layers.patterns && x.kind === "pattern")
        m.push({ time: x.t, position: x.direction === "bearish" ? "aboveBar" : "belowBar", color: "#8593a1", shape: "square", text: x.title.split(" ")[0] });
    }
    if (layers.liquidity) {
      r.pools.buy_side.slice(0, 2).forEach(p => L.push({ price: p.price, color: dn, title: "BSL", style: "dotted" }));
      r.pools.sell_side.slice(0, 2).forEach(p => L.push({ price: p.price, color: up, title: "SSL", style: "dotted" }));
    }
    if (layers.fvg) {
      const last = r.candles[r.candles.length - 1]?.close ?? 0;
      r.detections.filter(x => x.kind === "fvg" && x.evidence.open).sort((a, b) => Math.abs(a.price - last) - Math.abs(b.price - last)).slice(0, 2)
        .forEach(x => { L.push({ price: x.evidence.gap_high, color: info, title: "FVG", style: "dashed" }); L.push({ price: x.evidence.gap_low, color: info, title: "", style: "dashed" }); });
    }
    if (layers.ob) r.detections.filter(x => x.kind === "order_block" && x.evidence.mitigated_at == null).slice(-2)
      .forEach(x => { L.push({ price: x.evidence.zone_high, color: "#a78bfa", title: "OB", style: "solid" }); L.push({ price: x.evidence.zone_low, color: "#a78bfa", title: "", style: "solid" }); });
    if (layers.sr) r.detections.filter(x => x.kind === "sr_level").slice(0, 4).forEach(x => L.push({ price: x.price, color: "#56636f", title: x.title === "Support" ? "S" : "R", style: "solid" }));
    if (layers.plan) for (const s of r.setups) if (s.plan) {
      L.push({ price: s.plan.entry, color: am, title: "Entry", style: "solid", width: 2 });
      L.push({ price: s.plan.stop, color: dn, title: "Stop", style: "solid", width: 2 });
      L.push({ price: s.plan.tp1, color: up, title: `TP1 +${s.plan.tp1_pct}%`, style: "solid", width: 2 });
      if (s.plan.tp2 !== s.plan.tp1) L.push({ price: s.plan.tp2, color: up, title: `TP2 +${s.plan.tp2_pct}%`, style: "dashed" });
    }
    return { markers: m, lines: L };
  }, [r, layers]);
  const overlays = useMemo(() => r ? [{ name: "EMA20", color: "#f0a634", data: r.ema20 }, { name: "EMA50", color: "#5aa9e6", data: r.ema50 }] : [], [r]);
  const bars = useMemo(() => r ? r.candles.slice(-300) : [], [r]);
  const ov = useMemo(() => overlays.map(o => ({ ...o, data: o.data.slice(-300) })), [overlays]);
  const recent = (r?.detections ?? []).filter(x => !["sr_level", "pattern", "fvg", "order_block"].includes(x.kind)).slice(-25).reverse();

  const assetOpts = pools.data?.assets ?? [];
  return (
    <>
      <PageHead title="Market Structure" sub="Liquidity, BOS/CHoCH, fair value gaps, order blocks and setups computed from closed candles. Each marker links to the exact prices that justify it." />
      <div className="grid gap-3">
        <Panel title={<>{assetOpts.find(a => a.symbol === symbol)?.display ?? symbol} · {tf}</>} right={<>
          <select id="asset" value={`${cls}|${symbol}`} onChange={e => { const [c, s] = e.target.value.split("|"); setCls(c); setSymbol(s); }} className="bg-raised border border-line rounded-[2px] text-xs px-1.5 h-6 max-w-[180px]">
            {!assetOpts.some(a => a.symbol === symbol) && <option value={`${cls}|${symbol}`}>{symbol}</option>}
            <optgroup label="Crypto">{[...new Set([...CRYPTO, ...assetOpts.filter(a => a.asset_class === "crypto").map(a => a.symbol)])].map(s => <option key={s} value={`crypto|${s}`}>{s}</option>)}</optgroup>
            <optgroup label="Solana pools (agent universe)">{assetOpts.filter(a => a.asset_class === "memecoin").map(a => <option key={a.symbol} value={`memecoin|${a.symbol}`}>{a.display}</option>)}</optgroup>
          </select>
          <div className="flex border border-line rounded-[2px]">{TFS.filter(x => cls !== "memecoin" || x !== "6h").map(x =>
            <button key={x} onClick={() => setTf(x)} className={`px-2 h-6 text-2xs num ${tf === x ? "bg-raised text-amber" : "text-mute hover:text-text"}`}>{x}</button>)}</div>
          <SourceTag source={r?.source ?? ""} at={r?.fetched_at} /></>}>
          {d.error ? <ErrorBox message={d.error} onRetry={d.reload} /> : !r ? <Loading rows={10} /> : (
            <div className="grid xl:grid-cols-[1fr_280px]">
              <div className="min-w-0">
                <div className="flex flex-wrap gap-1.5 px-3 py-2 border-b border-line">{LAYERS.map(l => (
                  <button key={l.key} onClick={() => setLayers(x => ({ ...x, [l.key]: !x[l.key] }))}
                    className={`h-6 px-2 rounded-[2px] border text-2xs ${layers[l.key] ? "border-amber/60 text-amber bg-amber/5" : "border-line text-dim"}`}>{l.label}</button>))}</div>
                <CandleChart bars={bars} overlays={ov} markers={view.markers} lines={view.lines} height={460} />
                <div className="px-3 py-1.5 text-2xs text-dim flex flex-wrap gap-x-4">
                  <span>{r.candles.length} closed candles · {r.gaps} missing bars (not filled) · markers on the last 120 bars; sweeps shown when volume or reversal confirms</span>
                  {r.provisional && <span className="text-warn">Forming candle {new Date(r.provisional.t * 1000).toISOString().slice(11, 16)} UTC is provisional and excluded from signals</span>}
                  <span>fetch {r.latency_ms.fetch} ms · analysis {r.latency_ms.analysis} ms</span>
                </div>
              </div>
              <div className="border-t xl:border-t-0 xl:border-l border-line p-3 grid grid-cols-2 gap-3 content-start">
                <Stat label="Last close" value={fmtPrice(r.candles[r.candles.length - 1]?.close)} />
                <Stat label="Last break" value={<span className={r.trend === "bullish" ? "text-up" : r.trend === "bearish" ? "text-down" : ""}>{r.trend}</span>} />
                <div className="col-span-2"><div className="label">Swing structure</div><div className="text-xs mt-0.5">{r.structure_label}</div></div>
                <Stat label="ATR / bar" value={`${r.atr_pct.toFixed(2)}%`} />
                <Stat label="RSI 14" value={fmtNum(r.rsi, 1)} />
                <Stat label="Relative volume" value={r.rel_volume != null ? `${r.rel_volume.toFixed(2)}×` : "—"} />
                <Stat label="2–5% reachable" value={`${[2, 3, 4, 5].filter(t => t <= r.atr_pct * Math.sqrt(r.asset_class === "memecoin" ? 48 : 64)).length}/4 targets`} sub="vs typical move over max hold" />
                <div className="col-span-2"><div className="label mb-1">Buy-side liquidity (above)</div><div className="num text-xs text-down">{r.pools.buy_side.slice(0, 4).map(p => fmtPrice(p.price)).join(" · ") || "—"}</div></div>
                <div className="col-span-2"><div className="label mb-1">Sell-side liquidity (below)</div><div className="num text-xs text-up">{r.pools.sell_side.slice(0, 4).map(p => fmtPrice(p.price)).join(" · ") || "—"}</div></div>
              </div>
            </div>
          )}
        </Panel>

        {r && (
          <Panel title="Setups on the latest closed candle">
            {r.setups.length === 0 ? <Empty>No strategy conditions are met on the latest closed candle. QUANTX does not force a trade.</Empty> : (
              <div className="divide-y divide-line">{r.setups.map((s, i) => (
                <div key={i} className="p-3 grid gap-2">
                  <div className="flex flex-wrap gap-2 items-center"><Pill kind={s.signal.direction === "long" ? "up" : "down"}>{s.signal.direction.toUpperCase()}</Pill><Pill kind="info">{s.signal.strategy}</Pill><Pill>regime {s.regime}</Pill>
                    {s.plan ? <span className="num text-xs">entry {fmtPrice(s.plan.entry)} · stop {fmtPrice(s.plan.stop)} · TP1 {fmtPrice(s.plan.tp1)} (+{s.plan.tp1_pct}%) · R/R net {s.plan.rr_net.toFixed(2)} · costs {s.plan.costs_pct.toFixed(2)}%</span>
                      : <span className="text-xs text-warn">Rejected: {String(s.rejection?.reason).replaceAll("_", " ")}</span>}</div>
                  <ul className="text-xs text-mute">{[...s.signal.bullish, ...s.signal.bearish].map((x: string, j: number) => <li key={j}>› {x}</li>)}</ul>
                  {s.probability ? <ConfidenceBlock c={{ ai_confidence: s.signal.score, ai_confidence_note: "Heuristic evidence score, not a probability.", historical_win_rate: s.probability.historical_win_rate, historical_samples: s.probability.historical_samples, calibrated_probability: s.probability.insufficient ? null : s.probability.calibrated_probability, calibrated_ci90: s.probability.calibrated_ci90, validation: s.probability.validation, reason: s.probability.reason }} />
                    : <div className="text-2xs text-dim">Probability engine not calibrated yet. Activate the AI agent to run walk-forward validation.</div>}
                </div>))}</div>
            )}
          </Panel>
        )}

        {r && (
          <Panel title="Detections with evidence (most recent first)">
            {recent.length === 0 ? <Empty>No structure events in this window.</Empty> : (
              <div className="overflow-x-auto"><table className="grid-t">
                <thead><tr><th>Time (UTC)</th><th>Event</th><th className="r">Level</th><th>Evidence from price data</th></tr></thead>
                <tbody>{recent.map((x, i) => (
                  <tr key={i}><td className="num text-mute">{new Date(x.t * 1000).toISOString().slice(5, 16).replace("T", " ")}</td>
                    <td><Pill kind={x.direction === "bullish" ? "up" : x.direction === "bearish" ? "down" : "neutral"}>{x.title}</Pill></td>
                    <td className="r num">{fmtPrice(x.price)}</td><td className="!whitespace-normal text-xs text-mute min-w-[360px]">{evidenceText(x)}</td></tr>))}</tbody>
              </table></div>
            )}
          </Panel>
        )}
      </div>
    </>
  );
}

export default function StructurePage() {
  return <Suspense fallback={<Loading rows={8} />}><StructureInner /></Suspense>;
}
