"use client";
import { useMemo } from "react";
import { useApi } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { Empty, ErrorBox, Loading, PageHead, Panel, Pill } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
const pct = (x: number | null | undefined, d = 1) => (x == null ? "—" : `${(x * 100).toFixed(d)}%`);
const sgn = (x: number | null | undefined) => (x == null ? "—" : `${x >= 0 ? "+" : ""}${fmtNum(x)}%`);

export default function Predictions() {
  const s = useApi<any>("/api/strategies", 60_000);
  const cal = s.data?.calibration;
  const rows = useMemo(() => Object.values(cal?.combo_stats ?? {}).filter((r: any) => r.trades >= 10)
    .sort((a: any, b: any) => b.avg_net_pct - a.avg_net_pct) as any[], [cal]);
  return (
    <>
      <PageHead title="AI Predictions" sub="What the agent expects from each kind of setup: the historical probability that a setup made money after costs, and its expected net return with an honest uncertainty range." />
      <div className="grid gap-3">
        <Panel title="How to read these numbers">
          <div className="p-3 grid md:grid-cols-3 gap-4 text-xs text-mute leading-relaxed">
            <p><span className="text-text">AI confidence</span> (in each trade report) is a heuristic evidence score for one setup. It is not a probability.</p>
            <p><span className="text-text">Win probability</span> is the fraction of past simulated setups of this exact strategy, market, timeframe and direction that ended with a net profit, using the same fills, fees and slippage as paper trading.</p>
            <p><span className="text-text">Expected net return</span> is the average result per trade after costs, with a 90% confidence interval. A range that includes 0 means the edge is unproven.</p>
          </div>
        </Panel>
        <Panel title="Expected outcome by setup type" right={cal?.updated ? <span className="text-2xs text-dim">calibrated {new Date(cal.updated * 1000).toLocaleString()} · {cal.trades_total} historical setups</span> : null}>
          {s.error ? <ErrorBox message={s.error} /> : !s.data ? <Loading rows={6} /> : rows.length === 0 ? <Empty>Not calibrated yet. Press ACTIVATE; the agent backtests every combination after loading candle history.</Empty> : (
            <div className="overflow-x-auto max-h-[640px]"><table className="grid-t">
              <thead><tr><th>Strategy</th><th>Market</th><th>TF</th><th>Dir.</th><th className="r">Setups</th><th className="r">Win probability</th><th className="r">Expected net / trade</th><th className="r">90% CI</th><th className="r">Profit factor</th><th className="r">Avg hold</th><th>Agent may trade?</th></tr></thead>
              <tbody>{rows.map((r: any) => (
                <tr key={`${r.strategy}${r.asset_class}${r.tf}${r.direction}`}><td>{r.strategy}</td><td className="text-mute">{r.asset_class}</td><td className="num">{r.tf}</td><td>{r.direction}</td>
                  <td className="r num">{r.trades}</td><td className="r num">{pct(r.win_rate)}</td>
                  <td className={`r num ${r.avg_net_pct > 0 ? "text-up" : "text-down"}`}>{sgn(r.avg_net_pct)}</td>
                  <td className="r num text-mute">{r.ci90_net_pct ? `${sgn(r.ci90_net_pct[0])} … ${sgn(r.ci90_net_pct[1])}` : "—"}</td>
                  <td className="r num">{r.profit_factor == null ? "—" : fmtNum(r.profit_factor)}</td><td className="r num">{fmtNum(r.avg_bars_held, 0)} bars</td>
                  <td><Pill kind={r.tier === "active" ? "up" : r.tier === "exploration" ? "amber" : "neutral"}>{r.tier ?? "—"}</Pill></td></tr>))}</tbody>
            </table></div>
          )}
        </Panel>
        <p className="text-2xs text-dim">Historical simulated results do not guarantee future outcomes. {cal?.note}</p>
      </div>
    </>
  );
}
