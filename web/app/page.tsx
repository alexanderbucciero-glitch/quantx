"use client";
import Link from "next/link";
import { useApi } from "@/lib/api";
import { fmtNum, fmtPct, fmtPrice, tone } from "@/lib/format";
import { PageHead, Panel, Pill, Stat } from "@/components/ui";
import { CryptoTable, MarketsTable, MemeTable, NewsList, SecEvents, type Coin, type MarketRow } from "@/components/widgets";
import { AgentStatusPanel, LatestReports, OpenPositions, PortfolioPanel, TradingActivity } from "@/components/desk";
import { ReportDrawer } from "@/components/Report";
import { useState } from "react";

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

type Opp = { id: number; ts: number; asset: string; strategy: string; direction: string; status: string; reason: string | null };

function DecisionsPanel() {
  const d = useApi<{ opportunities: Opp[] }>("/api/agent/opportunities?limit=6", 15_000);
  return (
    <Panel title="Hypothetical signals" right={<Link href="/signals" className="text-amber hover:underline">All →</Link>}>
      {!d.data ? <div className="p-3 text-xs text-dim">Loading…</div> : d.data.opportunities.length === 0 ? <div className="p-3 text-xs text-mute">No setups yet.</div> : (
        <ul className="divide-y divide-line/60">{d.data.opportunities.map(o => (
          <li key={o.id} className="px-3 py-1.5 text-xs flex gap-2 items-center min-w-0">
            <span className="text-text truncate">{o.asset}</span><Pill kind={o.direction === "long" ? "up" : "down"}>{o.direction}</Pill>
            <span className="text-dim truncate">{o.strategy}</span>
            <span className="ml-auto"><Pill kind={o.status === "accepted" ? "up" : "warn"}>{o.status === "accepted" ? "opened" : "no trade"}</Pill></span>
          </li>))}</ul>
      )}
    </Panel>
  );
}

type StratRow = { label: string; asset_class: string; trades: number; tp1_hit_rate: number; avg_net_pct: number };

function ModelPanel() {
  const d = useApi<{ calibration: { trades_total?: number; combo_stats?: Record<string, StratRow & { tf: string; direction: string; tier: string }> } }>("/api/strategies", 60_000);
  const rows = Object.values(d.data?.calibration?.combo_stats ?? {}).filter(r => r.trades >= 10).sort((a, b) => b.avg_net_pct - a.avg_net_pct).slice(0, 5);
  return (
    <Panel title="Best backtest evidence (after costs)" right={<Link href="/predictions" className="text-amber hover:underline">Details →</Link>}>
      {!d.data ? <div className="p-3 text-xs text-dim">Loading…</div> : rows.length === 0 ? <div className="p-3 text-xs text-mute">Not validated yet. Validation runs after the agent loads candle history.</div> : (
        <ul className="p-3 space-y-1 text-xs">{rows.map(r => (
          <li key={r.label + r.asset_class + r.tf + r.direction} className="flex gap-2"><span className="text-text w-36 truncate">{(r as any).strategy} {r.tf} {r.direction}</span><span className="text-dim w-16">{r.asset_class}</span>
            <span className="num">win {Math.round(((r as any).win_rate ?? 0) * 100)}%</span>
            <span className={`num ml-auto ${tone(r.avg_net_pct)}`}>{r.avg_net_pct >= 0 ? "+" : ""}{fmtNum(r.avg_net_pct)}%/trade</span></li>))}
          <li className="text-2xs text-dim pt-1">{d.data.calibration.trades_total} simulated historical setups, net of costs</li></ul>
      )}
    </Panel>
  );
}

export default function Dashboard() {
  const [report, setReport] = useState<number | null>(null);
  return (
    <>
      <PageHead title="Dashboard" sub="Autonomous paper-trading desk on real market data. All capital and transactions are simulated; QUANTX cannot place real orders." />
      <div className="grid gap-3">
        <AgentStatusPanel />
        <PortfolioPanel />
        <OpenPositions onReport={setReport} />
        <div className="grid gap-3 xl:grid-cols-[1.6fr_1fr]">
          <TradingActivity limit={30} />
          <LatestReports />
        </div>
        <TopStrip />
        <div className="grid gap-3 md:grid-cols-2">
          <DecisionsPanel />
          <ModelPanel />
        </div>
        <div className="grid gap-3 xl:grid-cols-[1.1fr_1fr]">
          <MarketsTable compact />
          <CryptoTable limit={12} title="Crypto leaders" />
        </div>
        <div className="grid gap-3 xl:grid-cols-2">
          <SecEvents limit={14} minImportance={0.6} title="Unusual activity · high-importance SEC filings" />
          <NewsList limit={14} title="Latest breaking news" />
        </div>
        <MemeTable limit={10} title="New Solana / Pump.fun tokens" />
        <Panel title="Social media developments" right={<Pill kind="warn">Not connected</Pill>}>
          <div className="p-3 text-xs text-mute leading-relaxed">Needs Reddit or YouTube API access (free, but Reddit requires approval). X has no free read access. Nothing is shown here until a permitted source is connected. <Link href="/social" className="text-amber hover:underline">Details →</Link></div>
        </Panel>
      </div>
      <ReportDrawer id={report} onClose={() => setReport(null)} />
    </>
  );
}
