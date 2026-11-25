"use client";
import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useApi } from "@/lib/api";
import { fmtNum, fmtPct, fmtPrice, tone } from "@/lib/format";
import { dur } from "@/components/desk";
import { Empty, ErrorBox, Loading, PageHead, Panel, Pill, Stat } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
const utc = (t: number | null) => (t ? new Date(t * 1000).toISOString().slice(0, 16).replace("T", " ") : "—");

function TradeReport({ b }: { b: any }) {
  return (
    <div className="grid gap-3 text-[13px]">
      <div className="flex flex-wrap gap-2 items-center">
        <span className="text-[15px] font-semibold">{b.asset}</span>
        <Pill kind={b.direction === "long" ? "up" : "down"}>{b.direction}</Pill><Pill kind="info">{b.strategy}</Pill><Pill>{b.timeframe}</Pill>
        <Pill kind={b.evidence_tier === "active" ? "up" : "amber"}>{b.evidence_tier} tier</Pill>
        <Pill kind={b.analysis_correct ? "up" : "down"}>{b.analysis_correct ? "analysis correct" : "analysis wrong"}</Pill>
        <span className="ml-auto"><Pill kind="amber">SIMULATED</Pill></span>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
        <Stat label="Entry" value={fmtPrice(b.entry_price)} sub={`${utc(b.entry_time)} UTC`} />
        <Stat label="Exit (avg)" value={fmtPrice(b.exit_price)} sub={`${utc(b.exit_time)} UTC`} />
        <Stat label="Position size" value={fmtNum(b.position_size, 4)} sub={`$${fmtNum(b.notional_usd)}`} />
        <Stat label="Holding period" value={<span className="text-[13px]">{b.holding_period}</span>} />
        <Stat label="Gross P&L" value={`$${fmtNum(b.gross_pnl)}`} valueClass={tone(b.gross_pnl)} />
        <Stat label="Trading costs" value={`$${fmtNum(b.trading_costs)}`} sub={`slippage $${fmtNum(b.slippage_cost)}`} />
        <Stat label="Net P&L" value={`$${fmtNum(b.net_pnl)}`} valueClass={tone(b.net_pnl)} sub={`${b.net_pct >= 0 ? "+" : ""}${fmtNum(b.net_pct)}% on notional`} />
        <Stat label="Best / worst move" value={<span className="text-[13px]">{fmtNum(b.max_favourable_pct)}% / {fmtNum(b.max_adverse_pct)}%</span>} />
      </div>
      <div><div className="label mb-1">Reason for entering</div><p className="text-text/90 leading-relaxed">{b.entry_reason || "—"}</p></div>
      <div className="grid md:grid-cols-3 gap-3">
        <div><div className="label mb-1">Reason for selling</div><p>{b.exit_reason}</p></div>
        <div><div className="label mb-1">Was the analysis correct?</div><p>{b.verdict}</p>{b.expected_vs_actual && <p className="text-mute text-xs mt-1">{b.expected_vs_actual}</p>}</div>
        <div><div className="label mb-1">What could be improved</div><ul className="text-xs space-y-0.5">{b.improvements.map((x: string, i: number) => <li key={i}>› {x}</li>)}</ul></div>
      </div>
      <div><div className="label mb-1">Exit fills</div>
        <table className="grid-t"><thead><tr><th>Time</th><th>Kind</th><th>Side</th><th className="r">Price</th><th className="r">Qty</th><th className="r">Fee</th><th>Liquidity</th></tr></thead>
          <tbody>{b.fills.map((f: any) => <tr key={f.id}><td className="num">{utc(f.ts)}</td><td>{f.kind}</td><td>{f.side}</td><td className="r num">{fmtPrice(f.price)}</td><td className="r num">{fmtNum(f.qty, 4)}</td><td className="r num">${fmtNum(f.fee)}</td><td>{f.liquidity}</td></tr>)}</tbody></table></div>
    </div>
  );
}

function PeriodReport({ b }: { b: any }) {
  return (
    <div className="grid gap-3 text-[13px]">
      <div className="flex flex-wrap gap-2 items-center"><span className="text-[15px] font-semibold capitalize">{b.kind} report · {b.label}</span>
        {!b.complete && <Pill kind="warn">period in progress</Pill>}<span className="ml-auto"><Pill kind="amber">SIMULATED</Pill></span></div>
      <p className="text-text/90">{b.assessment}</p>
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
        <Stat label="Starting value" value={`$${fmtNum(b.starting_value)}`} />
        <Stat label="Ending value" value={`$${fmtNum(b.ending_value)}`} />
        <Stat label="Total return" value={fmtPct(b.total_return_pct / 100)} valueClass={tone(b.total_return_pct)} />
        <Stat label="Realised P&L" value={`$${fmtNum(b.realized_pnl)}`} valueClass={tone(b.realized_pnl)} />
        <Stat label="Unrealised P&L" value={`$${fmtNum(b.unrealized_pnl)}`} valueClass={tone(b.unrealized_pnl)} />
        <Stat label="Max drawdown" value={`${fmtNum(b.max_drawdown_pct)}%`} valueClass="text-down" />
        <Stat label="Trades closed" value={b.trades_closed} sub={`${b.trades_opened} opened · ${b.fills} fills`} />
        <Stat label="Winning / losing" value={`${b.winning_trades} / ${b.losing_trades}`} />
        <Stat label="Win rate" value={b.win_rate == null ? "—" : `${Math.round(b.win_rate * 100)}%`} />
        <Stat label="Avg win / avg loss" value={<span className="text-[13px]">{b.avg_win == null ? "—" : `$${fmtNum(b.avg_win)}`} / {b.avg_loss == null ? "—" : `$${fmtNum(b.avg_loss)}`}</span>} />
        <Stat label="Profit factor" value={b.profit_factor == null ? "—" : fmtNum(b.profit_factor)} />
        <Stat label="Best / worst strategy" value={<span className="text-[13px]">{b.best_strategy ?? "—"} / {b.worst_strategy ?? "—"}</span>} />
      </div>
      {Object.keys(b.by_strategy).length > 0 && (
        <table className="grid-t"><thead><tr><th>Strategy</th><th className="r">Trades</th><th className="r">Wins</th><th className="r">Net P&L</th></tr></thead>
          <tbody>{Object.entries(b.by_strategy).map(([k, v]: [string, any]) => <tr key={k}><td>{k}</td><td className="r num">{v.trades}</td><td className="r num">{v.wins}</td><td className={`r num ${tone(v.net)}`}>${fmtNum(v.net)}</td></tr>)}</tbody></table>)}
      <div className="grid md:grid-cols-2 gap-3">
        <div><div className="label mb-1">Open positions at report time</div>{b.open_positions.length === 0 ? <div className="text-dim text-xs">None</div> :
          <ul className="text-xs space-y-0.5">{b.open_positions.map((p: any) => <li key={p.id}>{p.display ?? p.asset} {p.direction} @ {fmtPrice(p.entry)} (stop {fmtPrice(p.stop)})</li>)}</ul>}</div>
        <div><div className="label mb-1">Important market events</div>{(b.market_events ?? []).length === 0 ? <div className="text-dim text-xs">None recorded</div> :
          <ul className="text-xs space-y-0.5">{b.market_events.map((e: any, i: number) => <li key={i}><a href={e.url} target="_blank" rel="noreferrer" className="hover:text-amber">{e.title}</a> <span className="text-dim">({e.source})</span></li>)}</ul>}</div>
      </div>
    </div>
  );
}

function ReportsInner() {
  const sp = useSearchParams();
  const [account, setAccount] = useState(sp.get("account") || "live");
  const [kind, setKind] = useState("");
  const [sel, setSel] = useState<number | null>(sp.get("id") ? Number(sp.get("id")) : null);
  const [msg, setMsg] = useState<string | null>(null);
  const accts = useApi<{ accounts: { account: string; kind: string }[] }>("/api/paper/accounts");
  const list = useApi<{ reports: any[] }>(`/api/reports?account=${account}${kind ? `&kind=${kind}` : ""}&limit=300`, 15_000);
  const detail = useApi<any>(sel ? `/api/reports/${sel}` : null);
  useEffect(() => { if (!sel && list.data?.reports.length) setSel(list.data.reports[0].id); }, [list.data, sel]);
  const gen = async (k: string) => {
    setMsg(null);
    const r = await fetch("/api/reports/generate", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind: k, account }) });
    setMsg(r.ok ? `${k} report generated from recorded data` : "Could not generate report");
    list.reload();
  };
  return (
    <>
      <PageHead title="Reports" sub="Written automatically from recorded simulated fills and positions: one report per closed trade, plus daily, weekly and monthly performance reports. Live paper trading and historical replays are kept separate."
        right={<div className="flex flex-wrap gap-2 items-center">
          <select id="acct" value={account} onChange={e => { setAccount(e.target.value); setSel(null); }} className="bg-panel border border-line rounded-[2px] h-7 px-2 text-xs">
            {(accts.data?.accounts ?? [{ account: "live", kind: "live paper trading" }]).map(a => <option key={a.account} value={a.account}>{a.account} ({a.kind})</option>)}
          </select>
          {["daily", "weekly", "monthly"].map(k => <button key={k} onClick={() => gen(k)} className="h-7 px-2 rounded-[2px] border border-amber/60 text-amber text-xs">Generate {k}</button>)}
        </div>} />
      {msg && <div className="text-xs text-mute mb-2">{msg}</div>}
      <div className="grid gap-3 lg:grid-cols-[340px_1fr]">
        <Panel title="All reports" right={<select id="kind" value={kind} onChange={e => setKind(e.target.value)} className="bg-raised border border-line rounded-[2px] h-6 px-1 text-2xs">
          <option value="">All</option><option value="trade">Trade</option><option value="daily">Daily</option><option value="weekly">Weekly</option><option value="monthly">Monthly</option></select>}
          bodyClass="max-h-[720px] overflow-y-auto">
          {list.error ? <ErrorBox message={list.error} /> : !list.data ? <Loading /> : list.data.reports.length === 0 ? <Empty>No reports yet for this account.</Empty> : (
            <ul className="divide-y divide-line/60">{list.data.reports.map(r => (
              <li key={r.id}><button onClick={() => setSel(r.id)} className={`w-full text-left px-3 py-2 text-xs hover:bg-raised ${sel === r.id ? "bg-raised" : ""}`}>
                <div className="flex gap-2 items-center"><Pill kind={r.kind === "trade" ? "info" : "amber"}>{r.kind}</Pill>
                  <span className="text-text truncate">{r.kind === "trade" ? r.summary.asset : r.summary.label}</span>
                  <span className={`num ml-auto ${tone(r.kind === "trade" ? r.summary.net_pnl : r.summary.total_return_pct)}`}>
                    {r.kind === "trade" ? `${r.summary.net_pnl >= 0 ? "+" : ""}$${fmtNum(r.summary.net_pnl)}` : fmtPct((r.summary.total_return_pct ?? 0) / 100)}</span></div>
                <div className="text-2xs text-dim mt-0.5">{r.kind === "trade" ? `${r.summary.strategy} · ${r.summary.exit_reason}` : `${r.summary.trades_closed} trades closed`} · {utc(r.period_end)}</div>
              </button></li>))}</ul>
          )}
        </Panel>
        <Panel title={detail.data ? `Report #${detail.data.id}` : "Report"}>
          <div className="p-3">{!sel ? <Empty>Select a report.</Empty> : detail.error ? <ErrorBox message={detail.error} /> : !detail.data ? <Loading rows={8} /> :
            detail.data.kind === "trade" ? <TradeReport b={detail.data.body} /> : <PeriodReport b={detail.data.body} />}</div>
        </Panel>
      </div>
      <p className="text-2xs text-dim mt-2">Holding periods use simulated timestamps ({dur(3600)} = one hour). Past simulated results do not guarantee future results.</p>
    </>
  );
}

export default function ReportsPage() {
  return <Suspense fallback={<Loading rows={8} />}><ReportsInner /></Suspense>;
}
