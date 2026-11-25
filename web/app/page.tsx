"use client";
import Link from "next/link";
import { useApi } from "@/lib/api";
import { fmtNum, fmtPct, fmtPrice, tone } from "@/lib/format";
import { PageHead, Panel, Pill, Stat } from "@/components/ui";
import { CryptoTable, MarketsTable, MemeTable, NewsList, SecEvents, StageStrip, type Coin, type MarketRow } from "@/components/widgets";

function TopStrip() {
  const m = useApi<{ rows: MarketRow[] }>("/api/markets/overview", 15 * 60_000);
  const c = useApi<{ coins: Coin[] }>("/api/crypto/markets?per_page=10", 120_000);
  const pick = (id: string) => m.data?.rows.find(r => r.id === id);
  const coin = (s: string) => c.data?.coins.find(x => x.symbol === s);
  const eq = ["SP500", "NASDAQCOM", "VIXCLS", "DGS10"].map(pick);
  const cr = ["btc", "eth", "sol"].map(coin);
  return (
    <Panel bodyClass="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-7 gap-4 p-3">
      {eq.map((r, i) => r?.ok ? (
        <Stat key={r.id} label={r.name.replace(" Composite", "").replace("CBOE Volatility Index (VIX)", "VIX").replace("US Treasury ", "UST ")}
          value={r.units === "%" ? `${fmtNum(r.last?.value)}%` : fmtNum(r.last?.value)}
          sub={<span className={tone(r.change)}>{r.units === "%" ? `${(r.change ?? 0) >= 0 ? "+" : ""}${fmtNum((r.change ?? 0) * 100, 0)}bp` : fmtPct(r.change_pct)} · {r.last?.date}</span>} />
      ) : <Stat key={i} label={["S&P 500", "NASDAQ", "VIX", "UST 10Y"][i]} value="—" sub={m.error ? "unavailable" : "loading"} />)}
      {cr.map((x, i) => x ? (
        <Stat key={x.id} label={`${x.symbol.toUpperCase()} / USD`} value={fmtPrice(x.current_price)}
          sub={<span className={tone(x.price_change_percentage_24h)}>{fmtPct(x.price_change_percentage_24h, 2, false)} 24h</span>} />
      ) : <Stat key={i} label={["BTC", "ETH", "SOL"][i] + " / USD"} value="—" sub={c.error ? "unavailable" : "loading"} />)}
    </Panel>
  );
}

function NotBuilt({ title, stage, href, text }: { title: string; stage: number; href: string; text: string }) {
  return (
    <Panel title={title} right={<Pill kind="warn">Stage {stage}</Pill>}>
      <div className="p-3 text-xs text-mute leading-relaxed">{text} <Link href={href} className="text-amber hover:underline">Details →</Link></div>
    </Panel>
  );
}

export default function Dashboard() {
  return (
    <>
      <PageHead title="Dashboard" sub="Live view from free public sources. Panels whose engines are not built yet say so instead of showing placeholder numbers." right={<StageStrip />} />
      <div className="grid gap-3">
        <TopStrip />
        <div className="grid gap-3 xl:grid-cols-[1.1fr_1fr]">
          <MarketsTable compact />
          <CryptoTable limit={12} title="Crypto leaders" />
        </div>
        <div className="grid gap-3 xl:grid-cols-2">
          <SecEvents limit={14} minImportance={0.6} title="Unusual activity · high-importance SEC filings" />
          <NewsList limit={14} title="Latest breaking news" />
        </div>
        <MemeTable limit={10} title="New Solana / Pump.fun tokens" />
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <NotBuilt title="AI forecasts" stage={3} href="/predictions" text="No forecasts until models are trained, calibrated and beat naive baselines out-of-sample." />
          <NotBuilt title="Hypothetical signals" stage={7} href="/signals" text="Signals need validated forecasts plus event and cost gates." />
          <NotBuilt title="Paper portfolio" stage={9} href="/portfolio" text="Paper trading starts after the backtest engine is validated." />
          <NotBuilt title="Model accuracy" stage={10} href="/models" text="Accuracy is measured from resolved live forecasts. There are none yet." />
        </div>
        <NotBuilt title="Social media developments" stage={5} href="/social" text="Needs Reddit/YouTube API access (free, approval required). X is paid-only and is skipped." />
      </div>
    </>
  );
}
