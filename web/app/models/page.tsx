"use client";
import { useApi } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { Empty, ErrorBox, Loading, PageHead, Panel, Pill } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
const pct = (x: number | null | undefined, d = 1) => (x == null ? "—" : `${(x * 100).toFixed(d)}%`);

export default function Models() {
  const s = useApi<any>("/api/strategies", 60_000);
  const p = useApi<any>("/api/paper/portfolio", 30_000);
  const cal = s.data?.calibration;
  const rows = Object.values(cal?.strategies ?? {}) as any[];
  const regimes = [...new Set(rows.flatMap((r: any) => Object.keys(r.by_regime || {})))].sort();
  return (
    <>
      <PageHead title="Model Performance" sub="Strategy comparison across market regimes, from walk-forward historical simulation, next to the live simulated results." />
      <div className="grid gap-3">
        <Panel title="Strategy × regime: average net return per setup (after costs)" right={cal?.history_window && <span className="text-2xs text-dim">{Object.values(cal.history_window).join(" · ")}</span>}>
          {s.error ? <ErrorBox message={s.error} /> : !s.data ? <Loading /> : rows.length === 0 ? <Empty>Not calibrated yet. Activate the AI agent.</Empty> : (
            <div className="overflow-x-auto"><table className="grid-t">
              <thead><tr><th>Strategy</th><th>Class</th>{regimes.map(r => <th key={r} className="r">{r}</th>)}<th className="r">All</th></tr></thead>
              <tbody>{rows.filter((r: any) => r.trades).map((r: any) => (
                <tr key={r.strategy + r.asset_class}><td>{r.label}</td><td className="text-mute">{r.asset_class}</td>
                  {regimes.map(g => { const x = r.by_regime?.[g]; return <td key={g} className={`r num ${!x?.trades ? "text-dim" : x.avg_net_pct > 0 ? "text-up" : "text-down"}`}>{x?.trades ? <>{x.avg_net_pct >= 0 ? "+" : ""}{fmtNum(x.avg_net_pct)}% <span className="text-dim text-2xs">n{x.trades}</span></> : "—"}</td>; })}
                  <td className={`r num ${r.avg_net_pct > 0 ? "text-up" : "text-down"}`}>{r.avg_net_pct >= 0 ? "+" : ""}{fmtNum(r.avg_net_pct)}% <span className="text-dim text-2xs">n{r.trades}</span></td></tr>))}</tbody>
            </table></div>
          )}
        </Panel>
        <Panel title="Statistical strategy: cointegrated pairs (research only)">
          {!cal?.pairs?.length ? <Empty>Computed after the first validation run.</Empty> : (
            <div className="overflow-x-auto"><table className="grid-t">
              <thead><tr><th>Pair</th><th className="r">Hedge ratio</th><th className="r">ADF t</th><th>Cointegrated (5%)</th><th className="r">Half-life</th><th className="r">Spread z</th><th className="r">Return corr.</th><th>Note</th></tr></thead>
              <tbody>{cal.pairs.map((x: any) => (
                <tr key={x.pair}><td>{x.pair}</td><td className="r num">{fmtNum(x.hedge_ratio, 3)}</td><td className="r num">{fmtNum(x.adf_t, 2)}</td><td>{x.cointegrated_5pct ? <Pill kind="up">yes</Pill> : <Pill>no</Pill>}</td>
                  <td className="r num">{x.half_life_bars ? `${fmtNum(x.half_life_bars, 0)} bars` : "—"}</td><td className="r num">{fmtNum(x.spread_z, 2)}</td><td className="r num">{fmtNum(x.return_correlation, 2)}</td>
                  <td className="!whitespace-normal text-xs text-mute min-w-[240px]">{x.research_note ?? ""}</td></tr>))}</tbody>
            </table></div>
          )}
        </Panel>
        <Panel title="Live simulated results vs historical expectation">
          {!p.data ? <Loading /> : (
            <div className="p-3 text-xs text-mute grid sm:grid-cols-3 gap-3">
              <div>Closed simulated trades: <span className="num text-text">{p.data.closed_trades}</span></div>
              <div>Live win rate: <span className="num text-text">{p.data.win_rate == null ? "—" : pct(p.data.win_rate)}</span></div>
              <div>Total simulated return: <span className="num text-text">{fmtNum(p.data.total_return_pct)}%</span></div>
              <p className="sm:col-span-3 text-dim">Live accuracy becomes meaningful after dozens of resolved trades. Until then, compare against the historical table above rather than drawing conclusions.</p>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
