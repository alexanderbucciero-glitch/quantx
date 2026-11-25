"use client";
import Link from "next/link";
import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useApi } from "@/lib/api";
import { fmtNum, fmtPct, fmtPrice, tone } from "@/lib/format";
import { EquityChart } from "@/components/EquityChart";
import { ReportDrawer } from "@/components/Report";
import { dur, OpenPositions, PortfolioPanel, TradingActivity } from "@/components/desk";
import { Empty, ErrorBox, Loading, PageHead, Panel, Pill, Stat } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
const NUM_FIELDS: [string, string, string][] = [
  ["starting_balance", "Starting balance (USD, new accounts)", "100"], ["risk_per_trade_pct", "Risk per trade % (active tier)", "0.05"],
  ["exploration_risk_pct", "Risk per trade % (exploration)", "0.01"], ["max_open_positions", "Max open positions", "1"],
  ["max_position_pct", "Max position % of equity", "1"], ["max_gross_exposure_pct", "Max gross exposure %", "1"],
  ["daily_loss_limit_pct", "Daily loss limit %", "0.1"], ["max_drawdown_pct", "Max drawdown %", "0.5"],
  ["max_correlated_positions", "Max correlated positions", "1"], ["max_entries_per_hour", "Max entries per hour", "1"],
  ["max_entries_per_day", "Max entries per day", "1"], ["cooldown_minutes", "Re-entry cooldown (min)", "5"], ["min_rr", "Min reward/risk after costs", "0.1"],
];

function Settings({ s, onSaved }: { s: any; onSaved: () => void }) {
  const [v, setV] = useState<any>({ ...s, targets_pct: s.targets_pct.join(", ") });
  const [msg, setMsg] = useState<string | null>(null);
  const save = async () => {
    const body = { ...v, targets_pct: String(v.targets_pct).split(",").map((x: string) => Number(x.trim())).filter((x: number) => x > 0) };
    const r = await fetch("/api/paper/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json();
    setMsg(r.ok ? "Saved — applies to the next decision" : j.detail?.error ?? "Could not save");
    if (r.ok) onSaved();
  };
  const inp = "h-7 w-full min-w-0 bg-raised border border-line rounded-[2px] px-2 num text-xs text-text";
  return (
    <div className="p-3 grid gap-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
        {NUM_FIELDS.map(([k, label, step]) => (
          <label key={k} className="grid gap-1 text-2xs text-mute min-w-0"><span>{label}</span>
            <input id={`set-${k}`} type="number" step={step} value={v[k]} onChange={e => setV({ ...v, [k]: Number(e.target.value) })} className={inp} /></label>))}
        <label className="grid gap-1 text-2xs text-mute min-w-0"><span>Target ladder % (agent picks)</span>
          <input id="set-targets" value={v.targets_pct} onChange={e => setV({ ...v, targets_pct: e.target.value })} className={inp} /></label>
        <label className="grid gap-1 text-2xs text-mute min-w-0"><span>Entry order type</span>
          <select id="set-entry" value={v.entry_order} onChange={e => setV({ ...v, entry_order: e.target.value })} className={inp}><option value="limit">limit (maker fee, may not fill)</option><option value="market">market (taker fee)</option></select></label>
        <label className="grid gap-1 text-2xs text-mute min-w-0"><span>On deactivate</span>
          <select id="set-deact" value={v.deactivate_mode} onChange={e => setV({ ...v, deactivate_mode: e.target.value })} className={inp}><option value="manage">keep managing open positions</option><option value="flatten">close all positions at market</option></select></label>
        <label className="flex items-center gap-2 text-xs text-mute mt-4"><input id="set-short" type="checkbox" checked={v.allow_short} onChange={e => setV({ ...v, allow_short: e.target.checked })} />Allow simulated shorts</label>
        <label className="flex items-center gap-2 text-xs text-mute mt-4"><input id="set-explore" type="checkbox" checked={v.exploration_enabled} onChange={e => setV({ ...v, exploration_enabled: e.target.checked })} />Exploration trades</label>
      </div>
      <div className="flex items-center gap-3"><button onClick={save} className="h-7 px-3 rounded-[2px] bg-amber text-ink text-xs font-semibold">Save settings</button>{msg && <span className="text-xs text-mute">{msg}</span>}
        <span className="text-2xs text-dim">Fees per side: {Object.entries(s.fees).map(([k, f]: [string, any]) => `${k} ${f.maker}/${f.taker}%`).join(" · ")} (maker/taker)</span></div>
    </div>
  );
}

function PortfolioInner() {
  const sp = useSearchParams();
  const [account, setAccount] = useState(sp.get("account") || "live");
  const accts = useApi<{ accounts: { account: string; kind: string }[] }>("/api/paper/accounts", 30_000);
  const m = useApi<any>(`/api/paper/portfolio?account=${account}`, 5000);
  const h = useApi<any>(`/api/paper/history?account=${account}&limit=300`, 8000);
  const [report, setReport] = useState<number | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const d = m.data;
  return (
    <>
      <PageHead title="Paper Trading Portfolio" sub="Every number here is SIMULATED, from the paper broker's ledger of fills. QUANTX has no brokerage or wallet connection and cannot place real orders."
        right={<div className="flex gap-2 items-center"><select id="acct" value={account} onChange={e => setAccount(e.target.value)} className="bg-panel border border-line rounded-[2px] h-7 px-2 text-xs">
          {(accts.data?.accounts ?? [{ account: "live", kind: "live paper trading" }]).map(a => <option key={a.account} value={a.account}>{a.account} ({a.kind})</option>)}</select><Pill kind="amber">SIMULATED</Pill></div>} />
      <div className="grid gap-3">
        <PortfolioPanel account={account} />
        {m.error ? <ErrorBox message={m.error} /> : !d ? <Loading rows={3} /> : (<>
          <Panel bodyClass="p-3 grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-7 gap-4">
            <Stat label="Daily return" value={d.daily_return_pct == null ? "—" : fmtPct(d.daily_return_pct / 100)} valueClass={tone(d.daily_return_pct)} />
            <Stat label="Win rate" value={d.win_rate == null ? "—" : `${Math.round(d.win_rate * 100)}%`} sub={`${d.closed_trades} closed`} />
            <Stat label="Profit factor" value={d.profit_factor == null ? "—" : fmtNum(d.profit_factor)} />
            <Stat label="Avg win / loss" value={<span className="text-[13px]">{d.avg_win == null ? "—" : `$${fmtNum(d.avg_win)}`} / {d.avg_loss == null ? "—" : `$${fmtNum(d.avg_loss)}`}</span>} />
            <Stat label="Max drawdown" value={d.max_drawdown_pct == null ? "—" : `${fmtNum(d.max_drawdown_pct)}%`} valueClass="text-down" />
            <Stat label="Avg holding time" value={dur(d.avg_holding_s)} />
            <Stat label="Entries" value={d.entries_suspended ? <span className="text-warn text-[13px]">suspended</span> : <span className="text-[13px]">allowed</span>} sub={d.entries_suspended ?? "within risk limits"} />
          </Panel>
          <div className="grid gap-3 xl:grid-cols-[1.4fr_1fr]">
            <Panel title="Simulated equity curve"><EquityChart points={d.equity_curve} /></Panel>
            <Panel title="Strategy performance (closed simulated trades)">
              {Object.keys(d.by_strategy).length === 0 ? <Empty>No closed trades yet.</Empty> :
                <table className="grid-t"><thead><tr><th>Strategy</th><th className="r">Trades</th><th className="r">Win rate</th><th className="r">Net P&L</th></tr></thead>
                  <tbody>{Object.entries(d.by_strategy).map(([k, v]: [string, any]) => <tr key={k}><td>{k}</td><td className="r num">{v.trades}</td><td className="r num">{fmtNum(100 * v.wins / v.trades, 0)}%</td><td className={`r num ${tone(v.pnl)}`}>${fmtNum(v.pnl)}</td></tr>)}</tbody></table>}
            </Panel>
          </div>
        </>)}
        <OpenPositions account={account} onReport={setReport} />
        <TradingActivity account={account} limit={200} />
        <Panel title="Closed positions">
          {!h.data ? <Loading /> : h.data.closed.length === 0 ? <Empty>No closed simulated trades yet.</Empty> : (
            <div className="overflow-x-auto max-h-[480px]"><table className="grid-t">
              <thead><tr><th>Closed (UTC)</th><th>Asset</th><th>Side</th><th>Strategy</th><th>Tier</th><th className="r">Entry</th><th className="r">Avg exit</th><th className="r">Held</th><th>Exit</th><th className="r">Gross</th><th className="r">Costs</th><th className="r">Net P&L</th><th /></tr></thead>
              <tbody>{h.data.closed.map((p: any) => (
                <tr key={p.id}><td className="num text-2xs text-mute">{new Date(p.closed_at * 1000).toISOString().slice(5, 16).replace("T", " ")}</td>
                  <td>{p.display ?? p.asset} <span className="text-2xs text-dim">{p.tf}</span></td><td><Pill kind={p.direction === "long" ? "up" : "down"}>{p.direction}</Pill></td>
                  <td className="text-mute">{p.strategy}</td><td className="text-2xs text-mute">{p.tier}</td>
                  <td className="r num">{fmtPrice(p.entry)}</td><td className="r num">{fmtPrice(p.exit_avg)}</td><td className="r num">{dur(p.closed_at - p.opened_at)}</td>
                  <td className="text-xs">{(p.exit_reason ?? "").replace("_", " ")}</td><td className={`r num ${tone(p.gross_pnl)}`}>${fmtNum(p.gross_pnl)}</td>
                  <td className="r num text-mute">${fmtNum(p.fees)}</td><td className={`r num ${tone(p.realized_pnl)}`}>${fmtNum(p.realized_pnl)}</td>
                  <td className="text-2xs whitespace-nowrap">{p.opportunity_id && <button onClick={() => setReport(p.opportunity_id)} className="text-amber hover:underline mr-2">entry</button>}
                    <Link href={`/reports${account !== "live" ? `?account=${account}` : ""}`} className="text-amber hover:underline">report</Link></td></tr>))}</tbody>
            </table></div>
          )}
        </Panel>
        {account === "live" && d && (
          <Panel title="Simulated account & risk settings" right={<button onClick={() => setShowSettings(x => !x)} className="text-amber hover:underline">{showSettings ? "Hide" : "Edit"}</button>}>
            {showSettings ? <Settings s={d.settings} onSaved={m.reload} /> : <div className="p-3 text-xs text-mute">Risk {d.settings.risk_per_trade_pct}% (active) / {d.settings.exploration_risk_pct}% (exploration) per trade · max {d.settings.max_open_positions} positions · gross exposure ≤ {d.settings.max_gross_exposure_pct}% · daily loss limit {d.settings.daily_loss_limit_pct}% · max drawdown {d.settings.max_drawdown_pct}% · {d.settings.entry_order} entries · targets {d.settings.targets_pct.join("/")}%</div>}
          </Panel>
        )}
      </div>
      <ReportDrawer id={report} onClose={() => setReport(null)} />
    </>
  );
}

export default function Portfolio() {
  return <Suspense fallback={<Loading rows={8} />}><PortfolioInner /></Suspense>;
}
