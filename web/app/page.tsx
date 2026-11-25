"use client";
import Link from "next/link";
import { useApi } from "@/lib/api";
import { STATE_LABEL, useAgent } from "@/lib/agent";
import { fmtNum, fmtPct, fmtPrice, tone } from "@/lib/format";
import { PageHead, Panel, Pill, Stat } from "@/components/ui";
import { CryptoTable, MarketsTable, MemeTable, NewsList, SecEvents, type Coin, type MarketRow } from "@/components/widgets";

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

function AgentPanel() {
  const { status } = useAgent();
  const c = status?.counters ?? {};
  return (
    <Panel title="AI agent" right={<Link href="/agent" className="text-amber hover:underline">Open →</Link>}>
      <div className="p-3 grid grid-cols-2 gap-3 text-xs">
        <Stat label="State" value={<span className="font-sans text-[13px]">{STATE_LABEL[status?.state ?? "offline"] ?? status?.state}</span>} sub={status?.current.task ?? "Press ACTIVATE AI to start"} />
        <Stat label="Assets scanned" value={status?.assets.total ?? 0} sub={`${status?.assets.eligible ?? 0} tradeable`} />
        <Stat label="Setups / rejected" value={`${c.opportunities_detected ?? 0} / ${c.opportunities_rejected ?? 0}`} />
        <Stat label="Analysis latency" value={status?.latency.analysis_ms?.avg != null ? `${status.latency.analysis_ms.avg} ms` : "—"} sub="per asset, per candle" />
      </div>
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

function PaperPanel() {
  const d = useApi<{ equity: number; total_return_pct: number; open_positions: number; closed_trades: number; win_rate: number | null }>("/api/paper/portfolio", 15_000);
  return (
    <Panel title="Paper portfolio (simulated)" right={<Link href="/portfolio" className="text-amber hover:underline">Open →</Link>}>
      {!d.data ? <div className="p-3 text-xs text-dim">Loading…</div> : (
        <div className="p-3 grid grid-cols-2 gap-3">
          <Stat label="Simulated equity" value={`$${fmtNum(d.data.equity)}`} />
          <Stat label="Total return" value={fmtPct(d.data.total_return_pct / 100)} valueClass={tone(d.data.total_return_pct)} />
          <Stat label="Open positions" value={d.data.open_positions} />
          <Stat label="Closed / win rate" value={`${d.data.closed_trades} / ${d.data.win_rate == null ? "—" : Math.round(d.data.win_rate * 100) + "%"}`} />
        </div>
      )}
    </Panel>
  );
}

type StratRow = { label: string; asset_class: string; trades: number; tp1_hit_rate: number; avg_net_pct: number };

function ModelPanel() {
  const d = useApi<{ calibration: { trades_total?: number; strategies?: Record<string, StratRow> } }>("/api/strategies", 60_000);
  const rows = Object.values(d.data?.calibration?.strategies ?? {}).filter(r => r.trades >= 30).sort((a, b) => b.avg_net_pct - a.avg_net_pct).slice(0, 4);
  return (
    <Panel title="Model accuracy (walk-forward)" right={<Link href="/predictions" className="text-amber hover:underline">Details →</Link>}>
      {!d.data ? <div className="p-3 text-xs text-dim">Loading…</div> : rows.length === 0 ? <div className="p-3 text-xs text-mute">Not validated yet. Validation runs after the agent loads candle history.</div> : (
        <ul className="p-3 space-y-1 text-xs">{rows.map(r => (
          <li key={r.label + r.asset_class} className="flex gap-2"><span className="text-text w-28 truncate">{r.label}</span><span className="text-dim w-16">{r.asset_class}</span>
            <span className="num">TP1 {Math.round(r.tp1_hit_rate * 100)}%</span>
            <span className={`num ml-auto ${tone(r.avg_net_pct)}`}>{r.avg_net_pct >= 0 ? "+" : ""}{fmtNum(r.avg_net_pct)}%/trade</span></li>))}
          <li className="text-2xs text-dim pt-1">{d.data.calibration.trades_total} simulated historical setups, net of costs</li></ul>
      )}
    </Panel>
  );
}

export default function Dashboard() {
  return (
    <>
      <PageHead title="Dashboard" sub="Live view from free public sources. Everything labelled simulated is paper trading; nothing here can place a real order." />
      <div className="grid gap-3">
        <TopStrip />
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
          <AgentPanel />
          <DecisionsPanel />
          <PaperPanel />
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
    </>
  );
}
