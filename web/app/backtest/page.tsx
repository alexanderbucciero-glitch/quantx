"use client";
import { useMemo, useState } from "react";
import { fmtNum, fmtPct, tone } from "@/lib/format";
import { CandleChart, type ChartMarker } from "@/components/Chart";
import { EquityChart } from "@/components/EquityChart";
import { Empty, ErrorBox, Loading, PageHead, Panel, Pill, Stat } from "@/components/ui";
import { useApi } from "@/lib/api";

/* eslint-disable @typescript-eslint/no-explicit-any */
const STRATS = [["momentum", "Momentum"], ["mean_reversion", "Mean reversion"], ["liquidity", "Liquidity sweep"], ["breakout", "Breakout"], ["volume", "Volume surge"]];
const pct = (x: number | null | undefined, d = 1) => (x == null ? "—" : `${(x * 100).toFixed(d)}%`);

function Summary({ s }: { s: any }) {
  if (!s?.trades) return <span className="text-dim">no trades</span>;
  return <span className="num">{s.trades} trades · TP1 {pct(s.tp1_hit_rate)} · win {pct(s.win_rate)} · avg <span className={tone(s.avg_net_pct)}>{fmtNum(s.avg_net_pct)}%</span> · PF {s.profit_factor == null ? "—" : fmtNum(s.profit_factor)}</span>;
}

function outcome(t: any) {
  if (t.hit_tp2) return "TP1+TP2";
  if (t.hit_tp1) return t.stopped ? "TP1 then BE" : "TP1 then time";
  return t.stopped ? "Stopped" : "Time exit";
}

function Replay() {
  const runs = useApi<any>("/api/replay", 4000);
  const [cfg, setCfg] = useState({ tf: "1h", bars: 2400, calib_frac: 0.6 });
  const [err, setErr] = useState<string | null>(null);
  const start = async () => {
    setErr(null);
    const r = await fetch("/api/replay", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(cfg) });
    const j = await r.json();
    if (!r.ok) setErr(j.detail?.error ?? "Could not start"); else runs.reload();
  };
  const list = runs.data?.runs ?? [];
  return (
    <Panel title="Historical replay of the autonomous desk">
      <div className="p-3 grid gap-3 text-xs">
        <p className="text-mute">Runs the real trading desk and paper broker over real history: evidence is calibrated on the first part only, then the rest is replayed candle by candle with a simulated clock. Every decision, order, fill and report is recorded in its own replay account, separate from live paper trading. The 11 assets are 10 large cryptocurrencies plus gold (PAXG).</p>
        <div className="flex flex-wrap gap-3 items-end">
          <label className="grid gap-1 text-2xs text-mute"><span>Timeframe</span><select id="rp-tf" value={cfg.tf} onChange={e => setCfg({ ...cfg, tf: e.target.value })} className="h-7 bg-raised border border-line rounded-[2px] px-1 text-xs">{["15m", "1h", "1d"].map(x => <option key={x}>{x}</option>)}</select></label>
          <label className="grid gap-1 text-2xs text-mute"><span>Bars per asset</span><input id="rp-bars" type="number" value={cfg.bars} onChange={e => setCfg({ ...cfg, bars: Number(e.target.value) })} className="h-7 w-24 bg-raised border border-line rounded-[2px] px-2 num text-xs" /></label>
          <label className="grid gap-1 text-2xs text-mute"><span>Calibration share</span><input id="rp-cal" type="number" step="0.05" value={cfg.calib_frac} onChange={e => setCfg({ ...cfg, calib_frac: Number(e.target.value) })} className="h-7 w-20 bg-raised border border-line rounded-[2px] px-2 num text-xs" /></label>
          <button onClick={start} className="h-7 px-3 rounded-[2px] bg-amber text-ink font-semibold">Start replay</button>
          {err && <span className="text-down">{err}</span>}
        </div>
        {list.length > 0 && (
          <div className="overflow-x-auto"><table className="grid-t">
            <thead><tr><th>Run</th><th>Status</th><th>Progress</th><th className="r">Trades</th><th className="r">W / L</th><th className="r">Final equity</th><th className="r">Net realised</th><th>Results</th></tr></thead>
            <tbody>{list.map((r: any) => (
              <tr key={r.id}><td>{r.account}</td><td><Pill kind={r.status === "finished" ? "up" : r.status === "error" ? "down" : "info"}>{r.status}</Pill></td>
                <td className="text-mute !whitespace-normal min-w-[200px]">{r.status === "finished" ? "done" : r.error ?? `${Math.round((r.progress ?? 0) * 100)}% · ${r.message}`}</td>
                <td className="r num">{r.result?.closed_trades ?? "—"}</td><td className="r num">{r.result ? `${r.result.winning} / ${r.result.losing}` : "—"}</td>
                <td className="r num">{r.result ? `$${fmtNum(r.result.final.equity)}` : "—"}</td>
                <td className={`r num ${tone(r.result?.net_realized)}`}>{r.result ? `$${fmtNum(r.result.net_realized)}` : "—"}</td>
                <td className="whitespace-nowrap">{r.status === "finished" && <><a className="text-amber hover:underline mr-2" href={`/portfolio?account=${r.account}`}>portfolio</a><a className="text-amber hover:underline" href={`/reports?account=${r.account}`}>reports</a></>}</td></tr>))}</tbody>
          </table></div>
        )}
      </div>
    </Panel>
  );
}

export default function Backtest() {
  const [f, setF] = useState({ symbol: "BTC-USD", tf: "15m", bars: 2400, fee_pct: 0.4, starting_balance: 10000, risk_per_trade_pct: 0.5, targets: "2, 3, 4, 5", max_hold_bars: 64 });
  const [sel, setSel] = useState<string[]>(STRATS.map(x => x[0]));
  const [res, setRes] = useState<any>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const run = async () => {
    setBusy(true); setErr(null);
    try {
      const r = await fetch("/api/backtest", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({
        symbol: f.symbol, asset_class: "crypto", tf: f.tf, bars: f.bars, strategies: sel, fee_pct: f.fee_pct, starting_balance: f.starting_balance,
        risk_per_trade_pct: f.risk_per_trade_pct, max_hold_bars: f.max_hold_bars,
        targets_pct: f.targets.split(",").map(x => Number(x.trim())).filter(x => x > 0) }) });
      const j = await r.json();
      if (!r.ok) throw new Error(j.detail?.error ?? `HTTP ${r.status}`);
      setRes(j);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const markers = useMemo<ChartMarker[]>(() => (res?.trades ?? []).flatMap((t: any) => [
    { time: t.entry_time, position: t.direction === "long" ? "belowBar" : "aboveBar", color: t.direction === "long" ? "#2fb67c" : "#e5534b",
      shape: t.direction === "long" ? "arrowUp" : "arrowDown", text: t.strategy.slice(0, 3) },
    { time: t.exit_time, position: "inBar", color: t.net_pct > 0 ? "#2fb67c" : "#e5534b", shape: "circle" }]), [res]);
  const input = (k: keyof typeof f, label: string, type = "number") => (
    <label className="grid gap-1 text-2xs text-mute min-w-0"><span>{label}</span>
      <input id={`bt-${k}`} type={type} value={f[k] as any} onChange={e => setF({ ...f, [k]: type === "number" ? Number(e.target.value) : e.target.value })}
        className="h-7 w-full min-w-0 bg-raised border border-line rounded-[2px] px-2 num text-xs text-text" /></label>);
  const p = res?.portfolio;
  return (
    <>
      <PageHead title="Backtesting Laboratory" sub="Replays real Coinbase candles bar by bar. Signals fire on closed candles, fills happen at the next open, a stop is assumed to fill before a target when both are hit in the same bar, and fees and slippage are deducted." />
      <div className="grid gap-3">
        <Replay />
        <Panel title="Strategy backtest · configuration">
          <div className="p-3 grid gap-3">
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-8 gap-3">
              {input("symbol", "Asset (Coinbase product)", "text")}
              <label className="grid gap-1 text-2xs text-mute min-w-0"><span>Timeframe</span>
                <select id="bt-tf" value={f.tf} onChange={e => setF({ ...f, tf: e.target.value })} className="h-7 bg-raised border border-line rounded-[2px] px-1 text-xs">
                  {["5m", "15m", "1h", "6h", "1d"].map(x => <option key={x}>{x}</option>)}</select></label>
              {input("bars", "Bars of history (≤ 6000)")}
              {input("max_hold_bars", "Max hold (bars)")}
              {input("targets", "Targets %", "text")}
              {input("fee_pct", "Fee % per side")}
              {input("starting_balance", "Starting balance")}
              {input("risk_per_trade_pct", "Risk per trade %")}
            </div>
            <div className="flex flex-wrap gap-1.5">{STRATS.map(([k, l]) => (
              <button key={k} onClick={() => setSel(s => s.includes(k) ? s.filter(x => x !== k) : [...s, k])}
                className={`h-6 px-2 rounded-[2px] border text-2xs ${sel.includes(k) ? "border-amber/60 text-amber bg-amber/5" : "border-line text-dim"}`}>{l}</button>))}</div>
            <div className="flex items-center gap-3">
              <button onClick={run} disabled={busy || !sel.length} className="h-8 px-4 rounded-[2px] bg-amber text-ink text-xs font-semibold disabled:opacity-50">{busy ? "Running…" : "Run backtest"}</button>
              <span className="text-2xs text-dim">Coinbase returns 300 candles per request, so 2,400 bars takes about 8 requests.</span></div>
          </div>
        </Panel>
        {err && <ErrorBox message={err} />}
        {busy && !res && <Loading rows={8} />}
        {res && (<>
          <Panel bodyClass="p-3 grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-8 gap-4">
            <Stat label="Total return" value={fmtPct(p.total_return_pct / 100)} valueClass={tone(p.total_return_pct)} sub={`$${fmtNum(p.final_equity)} final`} />
            <Stat label="Annualised" value={p.annualised_return_pct == null ? "—" : fmtPct(p.annualised_return_pct / 100)} sub="shown only for 3+ months of data" />
            <Stat label="Max drawdown" value={`${fmtNum(p.max_drawdown_pct)}%`} valueClass="text-down" />
            <Stat label="Sharpe (per trade)" value={fmtNum(p.sharpe_per_trade)} />
            <Stat label="Sortino (per trade)" value={fmtNum(p.sortino_per_trade)} />
            <Stat label="Win rate" value={pct(res.summary.win_rate)} sub={`profit factor ${res.summary.profit_factor == null ? "—" : fmtNum(res.summary.profit_factor)}`} />
            <Stat label="Avg win / loss" value={<span className="text-[13px]">{fmtNum(res.summary.avg_win_pct)}% / {fmtNum(res.summary.avg_loss_pct)}%</span>} />
            <Stat label="Benchmark (buy & hold)" value={fmtPct((p.benchmark_buy_hold_pct ?? 0) / 100)} valueClass={tone(p.benchmark_buy_hold_pct)} sub={`exposure ${fmtNum(p.exposure_pct, 1)}% · ${p.transactions} fills`} />
          </Panel>
          <div className="grid gap-3 xl:grid-cols-2">
            <Panel title="Equity curve (fixed-fractional risk)"><EquityChart points={res.equity_curve} /></Panel>
            <Panel title="Validation">
              <div className="p-3 grid gap-2 text-xs">
                <div><span className="text-mute">First 70% of trades:</span> <Summary s={res.out_of_sample.in_sample} /></div>
                <div><span className="text-mute">Last 30% of trades:</span> <Summary s={res.out_of_sample.out_of_sample} /></div>
                <div className="text-2xs text-dim">{res.out_of_sample.note}</div>
                <div className="label mt-2">By strategy</div>
                {Object.entries(res.by_strategy).map(([k, v]) => <div key={k}><span className="text-mute w-28 inline-block">{k}</span> <Summary s={v} /></div>)}
                <div className="label mt-2">By market regime</div>
                {Object.entries(res.by_regime).map(([k, v]) => <div key={k}><span className="text-mute w-40 inline-block">{k}</span> <Summary s={v} /></div>)}
                <div className="label mt-2">Rejected setups</div>
                <div className="text-mute">{Object.entries(res.rejected_setups).map(([k, v]) => `${k.replaceAll("_", " ")}: ${v}`).join(" · ") || "none"}</div>
              </div>
            </Panel>
          </div>
          <Panel title={`Trades on chart · ${res.symbol} ${res.tf}`}><CandleChart bars={res.candles} markers={markers} height={380} /></Panel>
          <Panel title="Trade list (last 300)">
            {res.trades.length === 0 ? <Empty>No trades: no setup passed the target and risk/reward checks.</Empty> : (
              <div className="overflow-x-auto max-h-[420px]"><table className="grid-t">
                <thead><tr><th>Entry (UTC)</th><th>Strategy</th><th>Side</th><th>Regime</th><th className="r">Entry</th><th className="r">Exit avg</th><th className="r">Bars</th><th>Outcome</th><th className="r">Gross</th><th className="r">Net</th></tr></thead>
                <tbody>{[...res.trades].reverse().map((t: any, i: number) => (
                  <tr key={i}><td className="num text-mute">{new Date(t.entry_time * 1000).toISOString().slice(5, 16).replace("T", " ")}</td><td>{t.strategy}</td>
                    <td><Pill kind={t.direction === "long" ? "up" : "down"}>{t.direction}</Pill></td><td className="text-mute text-xs">{t.regime}</td>
                    <td className="r num">{fmtNum(t.entry, 4)}</td><td className="r num">{fmtNum(t.exit_avg, 4)}</td><td className="r num">{t.bars_held}</td>
                    <td className="text-xs">{outcome(t)}</td>
                    <td className={`r num ${tone(t.gross_pct)}`}>{fmtNum(t.gross_pct)}%</td><td className={`r num ${tone(t.net_pct)}`}>{fmtNum(t.net_pct)}%</td></tr>))}</tbody>
              </table></div>
            )}
          </Panel>
          <p className="text-2xs text-dim">Assumptions: {Object.entries(res.assumptions).map(([k, v]) => `${k.replaceAll("_", " ")}: ${v}`).join(" · ")}. Runtime {res.runtime_ms} ms. Past simulated results do not guarantee future results.</p>
        </>)}
      </div>
    </>
  );
}
