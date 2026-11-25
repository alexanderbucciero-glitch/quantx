"use client";
import Link from "next/link";
import { useState } from "react";
import { useApi } from "@/lib/api";
import { fmtCompact, fmtPct, fmtPrice, tone } from "@/lib/format";
import { Empty, ErrorBox, Loading, PageHead, Panel, Pill, SourceTag } from "@/components/ui";
import { MemeTable } from "@/components/widgets";

type Pool = {
  pool: string; name: string; dex: string; token_address: string | null; price_usd: number | null; liquidity_usd: number | null;
  market_cap_usd: number | null; fdv_usd: number | null; volume_usd: Record<string, number>; price_change: Record<string, number>;
  transactions: Record<string, { buys: number; sells: number; unique_traders?: number }>; age_hours?: number; is_pump_fun: boolean;
  flags: { flag: string; severity: number; detail: string }[];
};
const FLAG: Record<string, string> = { low_liquidity: "Low liquidity", possible_wash_trading: "Possible wash trading", one_sided_flow: "One-sided flow",
  few_wallets_many_trades: "Few wallets, many trades", thin_liquidity_vs_valuation: "Thin vs valuation", very_new: "< 24h old", unreliable_data: "Unreliable data" };

function Pools() {
  const [kind, setKind] = useState("trending_pools");
  const d = useApi<{ pools: Pool[]; fetched_at: number; holder_note: string }>(`/api/memecoins/pools?kind=${kind}`, 120_000);
  return (
    <Panel title="Solana pools · volume, liquidity and manipulation checks" right={<>
      <div className="flex border border-line rounded-[2px]">{[["trending_pools", "Trending"], ["new_pools", "New"]].map(([k, l]) =>
        <button key={k} onClick={() => setKind(k)} className={`px-2 h-6 text-2xs ${kind === k ? "bg-raised text-amber" : "text-mute hover:text-text"}`}>{l}</button>)}</div>
      <SourceTag source="GeckoTerminal" at={d.data?.fetched_at} /></>}>
      {d.error ? <ErrorBox message={d.error} onRetry={d.reload} /> : !d.data ? <Loading rows={8} /> : d.data.pools.length === 0 ? <Empty>No pools returned.</Empty> : (
        <>
          <div className="overflow-x-auto"><table className="grid-t">
            <thead><tr><th>Pool</th><th className="r">Age</th><th className="r">Price</th><th className="r">Liquidity</th><th className="r">Mkt cap</th><th className="r">Vol 1h</th><th className="r">Vol 24h</th><th className="r">Δ 5m</th><th className="r">Δ 1h</th><th className="r">Δ 24h</th><th className="r">Trades 1h</th><th>Flags</th><th /></tr></thead>
            <tbody>{d.data.pools.map(p => {
              const tx = p.transactions?.h1;
              return (
                <tr key={p.pool}>
                  <td><span className="text-text">{p.name}</span>{p.is_pump_fun && <span className="ml-1"><Pill kind="amber">pump</Pill></span>}<div className="num text-2xs text-dim">{p.dex}</div></td>
                  <td className="r num">{p.age_hours == null ? "—" : p.age_hours < 48 ? `${p.age_hours.toFixed(1)}h` : `${(p.age_hours / 24).toFixed(0)}d`}</td>
                  <td className="r num">{fmtPrice(p.price_usd)}</td>
                  <td className={`r num ${(p.liquidity_usd ?? 0) < 100_000 ? "text-warn" : ""}`}>{fmtCompact(p.liquidity_usd)}</td>
                  <td className="r num">{fmtCompact(p.market_cap_usd ?? p.fdv_usd)}</td>
                  <td className="r num text-mute">{fmtCompact(p.volume_usd?.h1)}</td>
                  <td className="r num text-mute">{fmtCompact(p.volume_usd?.h24)}</td>
                  {(["m5", "h1", "h24"] as const).map(k => <td key={k} className={`r num ${tone(p.price_change?.[k])}`}>{fmtPct(p.price_change?.[k], 1, false)}</td>)}
                  <td className="r num text-mute">{tx ? `${tx.buys}/${tx.sells}` : "—"}{tx?.unique_traders ? <span className="text-dim"> · {tx.unique_traders}w</span> : null}</td>
                  <td><div className="flex flex-wrap gap-1 max-w-[300px] whitespace-normal">{p.flags.map(f => <span key={f.flag} title={f.detail}><Pill kind={f.severity >= 3 ? "down" : f.severity === 2 ? "warn" : "neutral"}>{FLAG[f.flag] ?? f.flag}</Pill></span>)}</div></td>
                  <td><Link href={`/structure?symbol=${p.pool}&cls=memecoin&tf=5m`} className="text-2xs text-amber hover:underline">Chart</Link></td>
                </tr>);
            })}</tbody>
          </table></div>
          <div className="px-3 py-2 text-2xs text-dim border-t border-line">{d.data.holder_note} Probabilities are not shown for individual new tokens: there are too few comparable historical launches to estimate one reliably.</div>
        </>
      )}
    </Panel>
  );
}

export default function Memecoins() {
  return (
    <>
      <PageHead title="Memecoin Scanner" sub="Solana and Pump.fun tokens from public market data (GeckoTerminal, DexScreener). No wallet is connected or requested. A large % move on a thin pool is flagged rather than ranked highly." />
      <div className="grid gap-3">
        <Pools />
        <MemeTable limit={40} title="Newly profiled Solana tokens (DexScreener)" />
        <Panel title="How the flags work">
          <div className="p-3 grid md:grid-cols-2 gap-x-8 gap-y-1.5 text-xs text-mute leading-relaxed">
            <p><span className="text-text">Low liquidity</span>: pool under $100k. The simulator refuses to paper-trade these.</p>
            <p><span className="text-text">Thin vs valuation</span>: market cap over 50× pool liquidity, so the price is easily moved.</p>
            <p><span className="text-text">Possible wash trading</span>: 1h volume over 5× liquidity.</p>
            <p><span className="text-text">Few wallets, many trades</span>: more than 8 trades per active wallet in an hour, typical of bot churn.</p>
            <p><span className="text-text">One-sided flow</span>: more than 85% of 1h trades in one direction.</p>
            <p><span className="text-text">Holder concentration</span>: needs on-chain holder data (free Helius key). Not checked yet.</p>
          </div>
        </Panel>
      </div>
    </>
  );
}
