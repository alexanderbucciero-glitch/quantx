"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import Link from "next/link";
import { useApi } from "@/lib/api";
import { MODE_LABEL, STATE_KIND, STATE_LABEL, useAgent, type AgentEvent } from "@/lib/agent";
import { AgentControl } from "@/components/AgentControl";
import { fmtCompact, fmtNum, fmtPrice } from "@/lib/format";
import { ReportDrawer } from "@/components/Report";
import { Empty, ErrorBox, Loading, PageHead, Panel, Pill, Stat } from "@/components/ui";

type AssetRow = { symbol: string; display: string; asset_class: string; tf: string; price: number | null; spread_pct: number | null;
  volume_24h_usd: number | null; liquidity_usd: number | null; rank_score: number; rank_parts: Record<string, number>; excluded: string | null;
  analysis: { trend: string; structure: string; atr_pct: number | null; rel_volume: number | null } | null; bars: number; quote_age_s: number | null; is_pump_fun?: boolean };
type Opp = { id: number; ts: number; asset: string; asset_class: string; strategy: string; direction: string; status: string; reason: string | null };

const KIND_COLOR: Record<string, string> = { report: "text-up", learning: "text-info", trade: "text-amber", decision: "text-text", pattern: "text-info", strategy: "text-text/80",
  validation: "text-up", news: "text-mute", data: "text-mute", analysis: "text-mute", scan: "text-mute", system: "text-amber", error: "text-down", latency: "text-dim" };

function LogLine({ e }: { e: AgentEvent }) {
  const t = new Date(e.ts * 1000).toISOString().slice(11, 19);
  return (
    <div className="flex gap-2 px-3 py-[3px] hover:bg-raised text-[12px] leading-snug">
      <span className="num text-dim shrink-0">{t}</span>
      <span className={`num text-2xs w-16 shrink-0 uppercase pt-[2px] ${e.level === "error" ? "text-down" : e.level === "warn" ? "text-warn" : "text-dim"}`}>{e.kind}</span>
      <span className={`min-w-0 break-words ${e.level === "error" ? "text-down" : e.level === "warn" ? "text-warn" : KIND_COLOR[e.kind] ?? "text-text"}`}>{e.message}</span>
      {e.latency_ms != null && <span className="num text-2xs text-dim ml-auto shrink-0 pl-2">{e.latency_ms >= 1000 ? `${(e.latency_ms / 1000).toFixed(1)}s` : `${e.latency_ms.toFixed(1)}ms`}</span>}
    </div>
  );
}

function LiveLog() {
  const { events } = useAgent();
  const [filter, setFilter] = useState("all");
  const [follow, setFollow] = useState(true);
  const box = useRef<HTMLDivElement>(null);
  const shown = useMemo(() => events.filter(e => filter === "all" || (filter === "decisions" ? ["decision", "trade", "strategy"].includes(e.kind) :
    filter === "patterns" ? e.kind === "pattern" : filter === "errors" ? e.level !== "info" : true)), [events, filter]);
  useEffect(() => { if (follow && box.current) box.current.scrollTop = box.current.scrollHeight; }, [shown, follow]);
  return (
    <Panel title="Live AI activity" right={<>
      <select id="log-filter" value={filter} onChange={e => setFilter(e.target.value)} className="bg-raised border border-line rounded-[2px] h-6 px-1 text-2xs">
        <option value="all">All events</option><option value="decisions">Decisions & trades</option><option value="patterns">Patterns</option><option value="errors">Warnings & errors</option>
      </select>
      <label className="flex items-center gap-1 text-2xs"><input id="follow" type="checkbox" checked={follow} onChange={e => setFollow(e.target.checked)} />follow</label></>}>
      <div ref={box} className="h-[420px] overflow-y-auto py-1" onWheel={() => setFollow(false)}>
        {shown.length === 0 ? <Empty>No activity yet. Press ACTIVATE AI to start the agent.</Empty> : shown.map((e, i) => <LogLine key={e.ts + ":" + i} e={e} />)}
      </div>
    </Panel>
  );
}

export default function AgentPage() {
  const { status, connected, error } = useAgent();
  const assets = useApi<{ assets: AssetRow[] }>("/api/agent/assets", 10_000);
  const opps = useApi<{ opportunities: Opp[]; counts: Record<string, number> }>("/api/agent/opportunities?limit=40", 10_000);
  const [report, setReport] = useState<number | null>(null);
  const c = status?.counters ?? {};
  const lat = status?.latency ?? {};
  const state = status?.state ?? "offline";
  return (
    <>
      <PageHead title="AI Agent" sub="The autonomous desk runs in the backend: it scans crypto, gold, forex and Solana pools, decides on every closed candle, executes simulated orders, manages exits and writes reports, with no approval needed for individual trades."
        right={<span className="text-2xs text-dim">{connected ? "Live (WebSocket)" : error ? error : "Polling"}</span>} />
      <div className="grid gap-3">
        <Panel bodyClass="p-3 grid gap-4 lg:grid-cols-[260px_1fr]">
          <div className="flex flex-col gap-3">
            <div className="flex items-center gap-2"><Pill kind={STATE_KIND[state]}>{STATE_LABEL[state] ?? state}</Pill>{status?.active && <span className="text-2xs text-dim">since {status.started_at ? new Date(status.started_at * 1000).toLocaleTimeString() : "—"}</span>}</div>
            <AgentControl big />
            <div className="text-2xs text-dim">{MODE_LABEL[status?.mode ?? "off"]}{status?.entries_suspended ? ` · entries suspended: ${status.entries_suspended}` : ""}</div>
            <div className="text-xs leading-relaxed">
              <div className="label">Current task</div><div className="text-text">{status?.current.task ?? "—"}</div>
              <div className="flex gap-4 mt-1 text-mute"><span>Asset: <span className="text-text">{status?.current.asset ?? "—"}</span></span><span>Strategy: <span className="text-text">{status?.current.strategy ?? "—"}</span></span></div>
            </div>
          </div>
          <div className="grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-x-4 gap-y-3 content-start">
            <Stat label="Assets scanned" value={status?.assets.total ?? "—"} sub={`${status?.assets.eligible ?? 0} tradeable`} />
            <Stat label="Candles analysed" value={c.candles_analysed ?? 0} sub={`${c.calibrations ?? 0} calibration run(s)`} />
            <Stat label="Opportunities" value={c.opportunities_identified ?? 0} />
            <Stat label="Rejected (no trade)" value={c.opportunities_rejected ?? 0} valueClass="text-warn" />
            <Stat label="Trades executed" value={c.trades_executed ?? 0} sub={`${c.exits ?? 0} exits · ${c.closed_trades ?? 0} closed`} />
            <Stat label="Open positions" value={status?.positions_open ?? 0} sub={`equity $${(status?.account?.equity ?? 0).toFixed(2)}`} />
            <Stat label="Analysis latency" value={lat.analysis_ms?.avg != null ? `${lat.analysis_ms.avg} ms` : "—"} sub={lat.analysis_ms?.p95 != null ? `p95 ${lat.analysis_ms.p95} ms` : "per asset"} />
            <Stat label="Candle close → decision" value={lat.candle_to_decision_ms?.avg != null ? `${(lat.candle_to_decision_ms.avg / 1000).toFixed(1)} s` : "—"} sub={`tick ${lat.tick_ms?.avg ?? "—"} ms · ${status?.stream.messages ?? 0} ticks`} />
            <div className="col-span-2 sm:col-span-4 xl:col-span-8">
              <div className="label mb-1">Data sources</div>
              <div className="flex flex-wrap gap-1.5">{Object.entries(status?.sources ?? {}).map(([k, v]) =>
                <span key={k} title={v.detail}><Pill kind={v.ok ? "up" : "down"}>{k} {v.ok ? "OK" : "down"}</Pill></span>)}
                {!Object.keys(status?.sources ?? {}).length && <span className="text-2xs text-dim">Sources are checked once the agent is active.</span>}
              </div>
            </div>
          </div>
        </Panel>

        <div className="grid gap-3 xl:grid-cols-[1.25fr_1fr]">
          <LiveLog />
          <Panel title="Latest decisions" right={opps.data && <span className="flex gap-1.5">{Object.entries(opps.data.counts).map(([k, v]) => <Pill key={k} kind={k === "accepted" ? "up" : "warn"}>{k} {v}</Pill>)}</span>}>
            {opps.error ? <ErrorBox message={opps.error} /> : !opps.data ? <Loading /> : opps.data.opportunities.length === 0 ? <Empty>No setups yet. Setups appear when a strategy fires on a closed candle.</Empty> : (
              <div className="max-h-[420px] overflow-y-auto divide-y divide-line/60">
                {opps.data.opportunities.map(o => (
                  <button key={o.id} onClick={() => setReport(o.id)} className="w-full text-left px-3 py-2 hover:bg-raised">
                    <div className="flex items-center gap-2"><span className="num text-2xs text-dim">{new Date(o.ts * 1000).toISOString().slice(11, 19)}</span>
                      <span className="text-text">{o.asset}</span><Pill kind={o.direction === "long" ? "up" : "down"}>{o.direction}</Pill><Pill>{o.strategy}</Pill>
                      <span className="ml-auto"><Pill kind={o.status === "accepted" ? "up" : "warn"}>{o.status}</Pill></span></div>
                    {o.reason && <div className="text-2xs text-mute mt-0.5">{o.reason}</div>}
                  </button>))}
              </div>
            )}
          </Panel>
        </div>

        <Panel title="Opportunity ranking" right={<span className="text-2xs text-dim hidden md:inline">liquidity, relative volume, spread, volatility fit, momentum, structure, history, news, data quality</span>}>
          {assets.error ? <ErrorBox message={assets.error} /> : !assets.data ? <Loading rows={8} /> : assets.data.assets.length === 0 ? <Empty>The universe is built when the agent is activated.</Empty> : (
            <div className="overflow-x-auto"><table className="grid-t">
              <thead><tr><th>#</th><th>Asset</th><th>Class</th><th className="r">Score</th><th className="r">Price</th><th className="r">Spread</th><th className="r">Vol 24h / Liq.</th><th className="r">ATR</th><th className="r">Rel vol</th><th>Structure</th><th>Status</th></tr></thead>
              <tbody>{assets.data.assets.map((a, i) => (
                <tr key={a.symbol}>
                  <td className="num text-dim">{i + 1}</td>
                  <td><Link className="hover:text-amber" href={`/structure?symbol=${encodeURIComponent(a.symbol)}&cls=${a.asset_class}&tf=${a.tf}`}>{a.display}</Link>{a.is_pump_fun && <span className="ml-1"><Pill kind="amber">pump</Pill></span>}</td>
                  <td className="text-mute">{a.asset_class} · {a.tf}</td>
                  <td className="r"><div className="flex items-center justify-end gap-2"><div className="w-16 h-1.5 bg-raised rounded-sm overflow-hidden"><div className="h-full bg-amber" style={{ width: `${Math.min(100, a.rank_score)}%` }} /></div><span className="num w-8">{fmtNum(a.rank_score, 0)}</span></div></td>
                  <td className="r num">{fmtPrice(a.price)}</td>
                  <td className="r num text-mute">{a.spread_pct != null ? a.spread_pct.toFixed(3) + "%" : "—"}</td>
                  <td className="r num text-mute">{a.asset_class === "memecoin" ? fmtCompact(a.liquidity_usd) + " liq" : fmtCompact(a.volume_24h_usd)}</td>
                  <td className="r num">{a.analysis?.atr_pct != null ? a.analysis.atr_pct.toFixed(2) + "%" : "—"}</td>
                  <td className="r num">{a.analysis?.rel_volume != null ? a.analysis.rel_volume.toFixed(2) + "×" : "—"}</td>
                  <td className="text-mute text-xs">{a.analysis ? `${a.analysis.structure}` : "loading"}</td>
                  <td>{a.excluded ? <span title={a.excluded}><Pill kind="warn">excluded</Pill> <span className="text-2xs text-dim">{a.excluded}</span></span> : !a.analysis ? <Pill>loading history</Pill> : <Pill kind="up">eligible</Pill>}</td>
                </tr>))}</tbody>
            </table></div>
          )}
        </Panel>
        {status?.data_coverage && (
          <Panel title="Market coverage and data quality">
            <ul className="p-3 text-xs text-mute grid md:grid-cols-2 gap-x-6 gap-y-1">{Object.entries(status.data_coverage).map(([k, v]) => <li key={k}><span className="text-text">{k}</span>: {v}</li>)}</ul>
          </Panel>
        )}
        {status?.research_only && (
          <Panel title="Strategies not yet simulated">
            <ul className="p-3 text-xs text-mute space-y-1">{Object.entries(status.research_only).map(([k, v]) => <li key={k}><span className="text-text">{k}</span>: {v}</li>)}</ul>
          </Panel>
        )}
      </div>
      <ReportDrawer id={report} onClose={() => setReport(null)} />
    </>
  );
}
