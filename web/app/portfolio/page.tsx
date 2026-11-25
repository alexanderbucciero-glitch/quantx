"use client";
import { useState } from "react";
import { useApi } from "@/lib/api";
import { fmtNum, fmtPct, fmtPrice, tone } from "@/lib/format";
import { EquityChart } from "@/components/EquityChart";
import { ReportDrawer } from "@/components/Report";
import { Empty, ErrorBox, Loading, PageHead, Panel, Pill, Stat } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
const dur = (s: number | null | undefined) => s == null ? "—" : s < 3600 ? `${Math.round(s / 60)}m` : s < 86400 ? `${(s / 3600).toFixed(1)}h` : `${(s / 86400).toFixed(1)}d`;

function Settings({ s, onSaved }: { s: any; onSaved: () => void }) {
  const [v, setV] = useState({ starting_balance: s.starting_balance, risk_per_trade_pct: s.risk_per_trade_pct, max_open_positions: s.max_open_positions,
    max_position_pct: s.max_position_pct, max_gross_exposure_pct: s.max_gross_exposure_pct, daily_loss_limit_pct: s.daily_loss_limit_pct,
    min_rr: s.min_rr, min_expected_net_pct: s.min_expected_net_pct, allow_short: s.allow_short, targets_pct: s.targets_pct.join(", "),
    fee_crypto: s.fees.crypto, fee_memecoin: s.fees.memecoin });
  const [msg, setMsg] = useState<string | null>(null);
  const field = (k: keyof typeof v, label: string, step = "0.1") => (
    <label className="grid gap-1 text-2xs text-mute min-w-0"><span>{label}</span>
      <input id={`set-${k}`} type="number" step={step} value={v[k] as number} onChange={e => setV({ ...v, [k]: Number(e.target.value) })} className="h-7 w-full min-w-0 bg-raised border border-line rounded-[2px] px-2 num text-xs text-text" /></label>);
  const save = async () => {
    setMsg(null);
    const body = { starting_balance: v.starting_balance, risk_per_trade_pct: v.risk_per_trade_pct, max_open_positions: v.max_open_positions,
      max_position_pct: v.max_position_pct, max_gross_exposure_pct: v.max_gross_exposure_pct, daily_loss_limit_pct: v.daily_loss_limit_pct,
      min_rr: v.min_rr, min_expected_net_pct: v.min_expected_net_pct, allow_short: v.allow_short,
      targets_pct: String(v.targets_pct).split(",").map(x => Number(x.trim())).filter(x => x > 0),
      fees: { crypto: v.fee_crypto, memecoin: v.fee_memecoin, equity: s.fees.equity } };
    const r = await fetch("/api/paper/settings", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const j = await r.json();
    setMsg(r.ok ? "Saved" : j.detail?.error ?? "Could not save");
    if (r.ok) onSaved();
  };
  return (
    <div className="p-3 grid gap-3">
      <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-3">
        {field("starting_balance", "Starting balance (USD)", "100")}
        {field("risk_per_trade_pct", "Risk per trade %")}
        {field("max_open_positions", "Max open positions", "1")}
        {field("max_position_pct", "Max position % of equity", "1")}
        {field("max_gross_exposure_pct", "Max gross exposure %", "1")}
        {field("daily_loss_limit_pct", "Daily loss limit %")}
        {field("min_rr", "Min reward/risk (net)")}
        {field("min_expected_net_pct", "Min expected net %", "0.01")}
        {field("fee_crypto", "Crypto fee % per side", "0.01")}
        {field("fee_memecoin", "DEX fee % per side", "0.01")}
        <label className="grid gap-1 text-2xs text-mute min-w-0"><span>Targets % (comma separated)</span>
          <input id="set-targets" value={v.targets_pct} onChange={e => setV({ ...v, targets_pct: e.target.value })} className="h-7 w-full min-w-0 bg-raised border border-line rounded-[2px] px-2 num text-xs text-text" /></label>
        <label className="flex items-center gap-2 text-xs text-mute mt-4"><input id="set-short" type="checkbox" checked={v.allow_short} onChange={e => setV({ ...v, allow_short: e.target.checked })} />Allow simulated shorts</label>
      </div>
      <div className="flex items-center gap-3"><button onClick={save} className="h-7 px-3 rounded-[2px] bg-amber text-ink text-xs font-semibold">Save settings</button>{msg && <span className="text-xs text-mute">{msg}</span>}
        <span className="text-2xs text-dim">Defaults are conservative: 0.5% risk per trade, 5 positions, 2% daily loss limit, 0.40% taker fee per side.</span></div>
    </div>
  );
}

export default function Portfolio() {
  const m = useApi<any>("/api/paper/portfolio", 5000);
  const h = useApi<any>("/api/paper/history?limit=200", 5000);
  const [report, setReport] = useState<number | null>(null);
  const [showSettings, setShowSettings] = useState(false);
  const d = m.data;
  const close = async (id: number) => { await fetch(`/api/paper/positions/${id}/close`, { method: "POST" }); m.reload(); h.reload(); };
  return (
    <>
      <PageHead title="Paper Trading Portfolio" sub="Every number on this page is SIMULATED. QUANTX has no brokerage or wallet connection and cannot place real orders."
        right={<Pill kind="amber">SIMULATED ACCOUNT</Pill>} />
      {m.error ? <ErrorBox message={m.error} onRetry={m.reload} /> : !d ? <Loading rows={8} /> : (
        <div className="grid gap-3">
          <Panel bodyClass="p-3 grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-4">
            <Stat label="Simulated equity" value={`$${fmtNum(d.equity)}`} sub={`balance $${fmtNum(d.balance)}`} />
            <Stat label="Total return" value={fmtPct(d.total_return_pct / 100)} valueClass={tone(d.total_return_pct)} sub={`from $${fmtNum(d.starting_balance, 0)}`} />
            <Stat label="Daily return" value={d.daily_return_pct == null ? "—" : fmtPct(d.daily_return_pct / 100)} valueClass={tone(d.daily_return_pct)} />
            <Stat label="Unrealised P&L" value={`$${fmtNum(d.unrealized)}`} valueClass={tone(d.unrealized)} />
            <Stat label="Win rate" value={d.win_rate == null ? "—" : fmtPct(d.win_rate, 1).replace("+", "")} sub={`${d.closed_trades} closed trades`} />
            <Stat label="Profit factor" value={d.profit_factor == null ? "—" : fmtNum(d.profit_factor)} />
            <Stat label="Max drawdown" value={d.max_drawdown_pct == null ? "—" : `${fmtNum(d.max_drawdown_pct)}%`} valueClass="text-down" />
            <Stat label="Avg holding time" value={dur(d.avg_holding_s)} sub={`fees paid $${fmtNum(d.fees_paid)}`} />
          </Panel>
          <div className="grid gap-3 xl:grid-cols-[1.4fr_1fr]">
            <Panel title="Simulated equity curve"><EquityChart points={d.equity_curve} /></Panel>
            <Panel title="Risk exposure">
              <div className="p-3 grid gap-3 text-xs">
                <div className="flex justify-between"><span className="text-mute">Gross exposure</span><span className="num">${fmtNum(d.exposure)} ({fmtNum(d.equity ? 100 * d.exposure / d.equity : 0, 1)}% of equity; limit {d.settings.max_gross_exposure_pct}%)</span></div>
                <div className="h-2 bg-raised rounded-sm overflow-hidden"><div className="h-full bg-amber" style={{ width: `${Math.min(100, d.equity ? 100 * d.exposure / d.equity / d.settings.max_gross_exposure_pct * 100 : 0)}%` }} /></div>
                {Object.entries(d.exposure_by_class).map(([k, v]) => <div key={k} className="flex justify-between"><span className="text-mute">{k}</span><span className="num">${fmtNum(v as number)}</span></div>)}
                <div className="flex justify-between"><span className="text-mute">Open positions</span><span className="num">{d.open_positions} / {d.settings.max_open_positions}</span></div>
                <div className="flex justify-between"><span className="text-mute">Risk per trade</span><span className="num">{d.settings.risk_per_trade_pct}% of equity</span></div>
                <div className="flex justify-between"><span className="text-mute">Daily loss limit</span><span className="num">{d.settings.daily_loss_limit_pct}%</span></div>
                <div className="label mt-2">Strategy performance (closed simulated trades)</div>
                {Object.keys(d.by_strategy).length === 0 ? <div className="text-dim">No closed trades yet.</div> :
                  <table className="grid-t"><thead><tr><th>Strategy</th><th className="r">Trades</th><th className="r">Win rate</th><th className="r">P&L</th></tr></thead>
                    <tbody>{Object.entries(d.by_strategy).map(([k, v]: [string, any]) => <tr key={k}><td>{k}</td><td className="r num">{v.trades}</td><td className="r num">{fmtNum(100 * v.wins / v.trades, 0)}%</td><td className={`r num ${tone(v.pnl)}`}>${fmtNum(v.pnl)}</td></tr>)}</tbody></table>}
              </div>
            </Panel>
          </div>
          <Panel title="Open simulated positions">
            {!h.data ? <Loading /> : h.data.open.length === 0 ? <Empty>No open simulated positions. The agent opens one only when every decision gate passes.</Empty> : (
              <div className="overflow-x-auto"><table className="grid-t">
                <thead><tr><th>Asset</th><th>Side</th><th>Strategy</th><th className="r">Entry</th><th className="r">Last</th><th className="r">Qty</th><th className="r">Stop</th><th className="r">TP1 / TP2</th><th className="r">Unrealised</th><th>Opened</th><th /></tr></thead>
                <tbody>{h.data.open.map((p: any) => { const u = (p.direction === "long" ? 1 : -1) * ((p.last_price ?? p.entry) - p.entry) * p.remaining_qty; return (
                  <tr key={p.id}><td><button className="hover:text-amber" onClick={() => p.opportunity_id && setReport(p.opportunity_id)}>{p.report?.asset ?? p.asset}</button></td>
                    <td><Pill kind={p.direction === "long" ? "up" : "down"}>{p.direction}</Pill></td><td className="text-mute">{p.strategy}</td>
                    <td className="r num">{fmtPrice(p.entry)}</td><td className="r num">{fmtPrice(p.last_price)}</td><td className="r num">{fmtNum(p.remaining_qty, 4)}{p.tp1_done ? <span className="text-dim"> (½)</span> : null}</td>
                    <td className="r num text-down">{fmtPrice(p.stop)}</td><td className="r num text-up">{fmtPrice(p.tp1)} / {fmtPrice(p.tp2)}</td>
                    <td className={`r num ${tone(u)}`}>${fmtNum(u)}</td><td className="num text-2xs text-mute">{new Date(p.opened_at * 1000).toISOString().slice(5, 16).replace("T", " ")}</td>
                    <td><button onClick={() => close(p.id)} className="text-2xs text-amber hover:underline">Close (sim)</button></td></tr>); })}</tbody>
              </table></div>
            )}
          </Panel>
          <Panel title="Closed positions / trade history">
            {!h.data ? <Loading /> : h.data.closed.length === 0 ? <Empty>No closed simulated trades yet.</Empty> : (
              <div className="overflow-x-auto max-h-[420px]"><table className="grid-t">
                <thead><tr><th>Closed</th><th>Asset</th><th>Side</th><th>Strategy</th><th className="r">Entry</th><th className="r">Avg exit</th><th className="r">Held</th><th className="r">Fees</th><th className="r">Net P&L</th></tr></thead>
                <tbody>{h.data.closed.map((p: any) => (
                  <tr key={p.id}><td className="num text-2xs text-mute">{new Date(p.closed_at * 1000).toISOString().slice(5, 16).replace("T", " ")}</td>
                    <td><button className="hover:text-amber" onClick={() => p.opportunity_id && setReport(p.opportunity_id)}>{p.report?.asset ?? p.asset}</button></td>
                    <td><Pill kind={p.direction === "long" ? "up" : "down"}>{p.direction}</Pill></td><td className="text-mute">{p.strategy}</td>
                    <td className="r num">{fmtPrice(p.entry)}</td><td className="r num">{fmtPrice(p.exit_avg)}</td><td className="r num">{dur(p.closed_at - p.opened_at)}</td>
                    <td className="r num text-mute">${fmtNum(p.fees)}</td><td className={`r num ${tone(p.realized_pnl)}`}>${fmtNum(p.realized_pnl)}</td></tr>))}</tbody>
              </table></div>
            )}
          </Panel>
          <Panel title="Simulated account settings" right={<button onClick={() => setShowSettings(x => !x)} className="text-amber hover:underline">{showSettings ? "Hide" : "Edit"}</button>}>
            {showSettings ? <Settings s={d.settings} onSaved={m.reload} /> : <div className="p-3 text-xs text-mute">Risk {d.settings.risk_per_trade_pct}% per trade · max {d.settings.max_open_positions} positions · targets {d.settings.targets_pct.join("/")}% · min R/R {d.settings.min_rr} · fees {d.settings.fees.crypto}% (crypto), {d.settings.fees.memecoin}% (DEX) per side</div>}
          </Panel>
        </div>
      )}
      <ReportDrawer id={report} onClose={() => setReport(null)} />
    </>
  );
}
