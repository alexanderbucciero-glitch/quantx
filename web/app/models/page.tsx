"use client";
import { useMemo, useState } from "react";
import { useApi } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { Empty, ErrorBox, Loading, PageHead, Panel, Pill } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
const sgn = (x: number | null | undefined, d = 2) => (x == null ? "—" : `${x >= 0 ? "+" : ""}${fmtNum(x, d)}%`);
const TK: Record<string, "up" | "amber" | "neutral"> = { active: "up", exploration: "amber", disabled: "neutral" };

export default function Models() {
  const ev = useApi<any>("/api/strategies/evidence", 30_000);
  const s = useApi<any>("/api/strategies", 60_000);
  const [filter, setFilter] = useState("tradable");
  const combos = useMemo(() => Object.entries(ev.data?.combos ?? {}).map(([k, v]: [string, any]) => {
    const [strategy, cls, tf, dir] = k.split("|");
    return { k, strategy, cls, tf, dir, ...v };
  }).filter(r => filter === "all" || (filter === "tradable" ? r.tier !== "disabled" : r.tier === filter))
    .sort((a, b) => (b.backtest?.mean ?? -99) - (a.backtest?.mean ?? -99)), [ev.data, filter]);
  const sv = ev.data?.selection_validation;
  const pairs = s.data?.calibration?.pairs ?? [];
  return (
    <>
      <PageHead title="Model Performance" sub="How the agent decides which strategies it may trade, and whether that decision process works out of sample. Backtest evidence and live paper results are tracked separately." />
      <div className="grid gap-3">
        <Panel title="Self-improvement rules">
          <div className="p-3 grid md:grid-cols-2 gap-x-6 gap-y-1 text-xs text-mute">
            {Object.entries(ev.data?.rules ?? {}).map(([k, v]) => <div key={k}><span className="text-text">{k.replace("_", " ")}</span>: {v as string}</div>)}
            <div className="md:col-span-2 text-dim">A combination is one strategy × asset class × timeframe × direction. One losing trade never changes a tier, because minimum sample sizes apply.</div>
          </div>
        </Panel>
        <Panel title="Walk-forward test of the selection rule itself">
          {!sv ? <Loading rows={3} /> : !sv.folds ? <Empty>Not enough history yet (needs at least 40 backtest trades).</Empty> : (
            <div className="p-3 grid gap-2 text-xs">
              <p className="text-mute">For each fold, tiers were decided using only earlier trades, then the trades the rule would have allowed were measured on the unseen fold.</p>
              <table className="grid-t"><thead><tr><th>Fold</th><th className="r">Training trades</th><th className="r">Test trades</th><th className="r">Selected</th><th className="r">Selected mean net</th><th className="r">All test trades mean</th></tr></thead>
                <tbody>{sv.by_fold.map((f: any) => <tr key={f.fold}><td>{f.fold}</td><td className="r num">{f.train_trades}</td><td className="r num">{f.test_trades}</td><td className="r num">{f.selected_trades}</td>
                  <td className={`r num ${(f.selected_mean_net_pct ?? 0) > 0 ? "text-up" : "text-down"}`}>{sgn(f.selected_mean_net_pct)}</td><td className="r num text-mute">{sgn(f.all_mean_net_pct)}</td></tr>)}</tbody></table>
              <p>Out of sample, selected trades averaged <b className={(sv.selected?.mean ?? 0) > 0 ? "text-up" : "text-down"}>{sgn(sv.selected?.mean)}</b> per trade (n={sv.selected?.n}, 90% CI {sgn(sv.selected?.lo90)} to {sgn(sv.selected?.hi90)}), versus {sgn(sv.unselected_baseline?.mean)} for all setups.
                {(sv.selected?.hi90 ?? 1) < 0 ? <span className="text-warn"> Even the evidence-selected strategies lost money out of sample over this history. Treat every result as unproven.</span> : null}</p>
            </div>
          )}
        </Panel>
        <Panel title="Evidence tiers by combination" right={<>
          {Object.entries(ev.data?.counts ?? {}).map(([k, v]) => <Pill key={k} kind={TK[k]}>{v as number} {k}</Pill>)}
          <select id="tier-filter" value={filter} onChange={e => setFilter(e.target.value)} className="bg-raised border border-line rounded-[2px] h-6 px-1 text-2xs">
            <option value="tradable">Active + exploration</option><option value="all">All</option><option value="disabled">Disabled</option></select></>}>
          {ev.error ? <ErrorBox message={ev.error} /> : !ev.data ? <Loading rows={8} /> : combos.length === 0 ? <Empty>{filter === "tradable" ? "No combination currently has enough evidence to trade. The agent stays out of the market." : "Not calibrated yet. Press ACTIVATE; calibration runs after candle history loads."}</Empty> : (
            <div className="overflow-x-auto max-h-[560px]"><table className="grid-t">
              <thead><tr><th>Strategy</th><th>Class</th><th>TF</th><th>Dir.</th><th>Tier</th><th className="r">Backtest n</th><th className="r">Mean net</th><th className="r">90% CI</th><th className="r">Win rate</th><th className="r">Live n</th><th className="r">Live mean</th><th>Reason</th></tr></thead>
              <tbody>{combos.map(r => (
                <tr key={r.k}><td>{r.strategy}</td><td className="text-mute">{r.cls}</td><td className="num">{r.tf}</td><td>{r.dir}</td><td><Pill kind={TK[r.tier]}>{r.tier}</Pill></td>
                  <td className="r num">{r.backtest?.n ?? 0}</td><td className={`r num ${(r.backtest?.mean ?? 0) > 0 ? "text-up" : "text-down"}`}>{sgn(r.backtest?.mean)}</td>
                  <td className="r num text-mute">{r.backtest?.n > 1 ? `${sgn(r.backtest.lo90, 1)} … ${sgn(r.backtest.hi90, 1)}` : "—"}</td>
                  <td className="r num">{r.backtest?.win_rate == null ? "—" : `${Math.round(r.backtest.win_rate * 100)}%`}</td>
                  <td className="r num">{r.live?.n ?? 0}</td><td className="r num">{sgn(r.live?.mean)}</td>
                  <td className="!whitespace-normal text-xs text-mute min-w-[260px]">{r.reason}</td></tr>))}</tbody>
            </table></div>
          )}
        </Panel>
        <Panel title="Strategy selection changes (learning log)">
          {!ev.data ? <Loading /> : ev.data.changes.length === 0 ? <Empty>No tier changes yet. Changes need enough backtest or live evidence.</Empty> : (
            <div className="overflow-x-auto"><table className="grid-t"><thead><tr><th>Time (UTC)</th><th>Combination</th><th>Change</th><th>Reason</th></tr></thead>
              <tbody>{ev.data.changes.map((c: any) => <tr key={c.id}><td className="num text-mute">{new Date(c.ts * 1000).toISOString().slice(5, 16).replace("T", " ")}</td><td>{c.combo}</td>
                <td><Pill kind={TK[c.old_tier]}>{c.old_tier}</Pill> → <Pill kind={TK[c.new_tier]}>{c.new_tier}</Pill></td><td className="!whitespace-normal text-xs text-mute">{c.reason}</td></tr>)}</tbody></table></div>
          )}
        </Panel>
        <Panel title="Statistical strategy: cointegrated pairs (research only, not traded)">
          {!pairs.length ? <Empty>Computed after the first calibration.</Empty> : (
            <div className="overflow-x-auto"><table className="grid-t">
              <thead><tr><th>Pair</th><th className="r">Hedge ratio</th><th className="r">ADF t</th><th>Cointegrated (5%)</th><th className="r">Half-life</th><th className="r">Spread z</th><th className="r">Return corr.</th></tr></thead>
              <tbody>{pairs.map((x: any) => (
                <tr key={x.pair}><td>{x.pair}</td><td className="r num">{fmtNum(x.hedge_ratio, 3)}</td><td className="r num">{fmtNum(x.adf_t, 2)}</td><td>{x.cointegrated_5pct ? <Pill kind="up">yes</Pill> : <Pill>no</Pill>}</td>
                  <td className="r num">{x.half_life_bars ? `${fmtNum(x.half_life_bars, 0)} bars` : "—"}</td><td className="r num">{fmtNum(x.spread_z, 2)}</td><td className="r num">{fmtNum(x.return_correlation, 2)}</td></tr>))}</tbody>
            </table></div>
          )}
        </Panel>
      </div>
    </>
  );
}
