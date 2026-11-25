"use client";
import { useMemo, useState } from "react";
import { useApi } from "@/lib/api";
import { fmtNum, fmtPct, fmtPrice, tone } from "@/lib/format";
import { CandleChart } from "@/components/Chart";
import { ErrorBox, Loading, PageHead, Panel, SourceTag, Stat } from "@/components/ui";
import { CryptoTable } from "@/components/widgets";

type Candles = {
  product: string; interval: string; fetched_at: number;
  bars: { time: number; open: number; high: number; low: number; close: number; volume: number }[];
  overlays: { sma20: (number | null)[]; sma50: (number | null)[]; rsi14: (number | null)[] };
  stats: Record<string, number | string | null>;
};
const PRODUCTS = ["BTC-USD", "ETH-USD", "SOL-USD", "XRP-USD", "DOGE-USD", "ADA-USD", "AVAX-USD", "LINK-USD", "BTC-EUR", "ETH-EUR"];
const INTERVALS = ["15m", "1h", "6h", "1d"];

export default function Crypto() {
  const [p, setP] = useState("BTC-USD");
  const [iv, setIv] = useState("1d");
  const c = useApi<Candles>(`/api/crypto/candles/${p}?interval=${iv}`, iv === "1d" ? 300_000 : 60_000);
  const overlays = useMemo(() => c.data ? [
    { name: "SMA20", color: "#f0a634", data: c.data.overlays.sma20 },
    { name: "SMA50", color: "#5aa9e6", data: c.data.overlays.sma50 },
  ] : [], [c.data]);
  const st = c.data?.stats;
  const n = (k: string) => (st?.[k] as number | null | undefined) ?? null;
  return (
    <>
      <PageHead title="Cryptocurrency Intelligence" sub="Exchange candles from Coinbase (up to 300 bars per request) with indicators computed locally. Crypto is modelled separately from equities." />
      <div className="grid gap-3">
        <Panel title={`${p} · ${iv}`} right={<>
          <select id="product" value={p} onChange={e => setP(e.target.value)} className="bg-raised border border-line rounded-[2px] text-xs px-1.5 h-6">{PRODUCTS.map(x => <option key={x}>{x}</option>)}</select>
          <div className="flex border border-line rounded-[2px]">{INTERVALS.map(x =>
            <button key={x} onClick={() => setIv(x)} className={`px-2 h-6 text-2xs num ${iv === x ? "bg-raised text-amber" : "text-mute hover:text-text"}`}>{x}</button>)}</div>
          <SourceTag source="Coinbase" at={c.data?.fetched_at} /></>}>
          {c.error ? <ErrorBox message={c.error} onRetry={c.reload} /> : !c.data ? <Loading rows={8} /> : (
            <div className="grid lg:grid-cols-[1fr_260px]">
              <div className="min-w-0"><CandleChart bars={c.data.bars} overlays={overlays} height={420} />
                <div className="px-3 py-1.5 text-2xs text-dim flex gap-4"><span><span style={{ color: "#f0a634" }}>━</span> SMA20</span><span><span style={{ color: "#5aa9e6" }}>━</span> SMA50</span><span>{c.data.bars.length} bars</span></div>
              </div>
              <div className="border-t lg:border-t-0 lg:border-l border-line p-3 grid grid-cols-2 gap-x-4 gap-y-3 content-start">
                <Stat label="Last" value={fmtPrice(n("last"))} />
                <Stat label="Return 1 bar" value={fmtPct(n("return_1"))} valueClass={tone(n("return_1"))} />
                <Stat label="Return 5 bars" value={fmtPct(n("return_5"))} valueClass={tone(n("return_5"))} />
                <Stat label="Return 20 bars" value={fmtPct(n("return_20"))} valueClass={tone(n("return_20"))} />
                <Stat label="RSI 14" value={fmtNum(n("rsi14"), 1)} valueClass={n("rsi14") != null && (n("rsi14")! > 70 || n("rsi14")! < 30) ? "text-warn" : ""} />
                <Stat label="Realized vol 20 (ann.)" value={fmtPct(n("realized_vol_20"), 1).replace("+", "")} />
                <Stat label="SMA 50" value={fmtPrice(n("sma50"))} />
                <Stat label="SMA 200" value={fmtPrice(n("sma200"))} />
                <Stat label="From period high" value={fmtPct(n("drawdown_from_high"), 1)} valueClass={tone(n("drawdown_from_high"))} />
                <Stat label="Volume z-score" value={fmtNum(n("volume_zscore"), 2)} />
                <div className="col-span-2"><div className="label">Regime (descriptive)</div><div className="text-xs mt-0.5">{String(st?.regime ?? "—")}</div></div>
                <p className="col-span-2 text-2xs text-dim leading-snug">{String(st?.note ?? "")} Forecasts with calibrated probabilities come in Stage 3.</p>
              </div>
            </div>
          )}
        </Panel>
        <CryptoTable limit={100} />
      </div>
    </>
  );
}
