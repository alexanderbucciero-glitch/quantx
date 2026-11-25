"use client";
import { useApi } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { Empty, ErrorBox, Loading, PageHead, Panel, Pill } from "@/components/ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
const pct = (x: number | null | undefined, d = 1) => (x == null ? "—" : `${(x * 100).toFixed(d)}%`);

export default function Predictions() {
  const s = useApi<any>("/api/strategies", 60_000);
  const cal = s.data?.calibration;
  const rows = Object.values(cal?.strategies ?? {}) as any[];
  return (
    <>
      <PageHead title="AI Predictions" sub="Probability that a setup reaches its first target before its stop, estimated per strategy, asset class and market regime with walk-forward validation. Shown only where the evidence is sufficient." />
      <div className="grid gap-3">
        <Panel title="How to read these numbers">
          <div className="p-3 grid md:grid-cols-3 gap-4 text-xs text-mute leading-relaxed">
            <p><span className="text-text">AI confidence</span> is a heuristic evidence score for one setup (0–1). It measures how strongly the rule conditions are met. It is not a probability.</p>
            <p><span className="text-text">Historical win rate</span> is the fraction of past simulated setups that reached TP1, with a 90% Wilson interval. It is in-sample.</p>
            <p><span className="text-text">Calibrated probability</span> is predicted walk-forward: each trade is estimated only from trades that had already closed. Its accuracy is scored with the Brier score against a no-skill baseline.</p>
          </div>
        </Panel>
        <Panel title="Probability engine by strategy" right={cal?.updated ? <span className="text-2xs text-dim">validated {new Date(cal.updated * 1000).toLocaleString()} · {cal.trades_total} historical setups</span> : null}>
          {s.error ? <ErrorBox message={s.error} /> : !s.data ? <Loading rows={6} /> : rows.length === 0 ? <Empty>Not calibrated yet. Press ACTIVATE AI; the agent runs walk-forward validation after loading candle history (a few minutes).</Empty> : (
            <div className="overflow-x-auto"><table className="grid-t">
              <thead><tr><th>Strategy</th><th>Class</th><th className="r">Setups</th><th className="r">TP1 hit rate</th><th className="r">Avg net / trade</th><th className="r">OOS predictions</th><th className="r">Brier</th><th className="r">Baseline</th><th className="r">Calib. error</th><th>Status</th></tr></thead>
              <tbody>{rows.map((r: any) => { const v = r.validation || {}; const ok = r.trades >= 30 && !v.insufficient && (v.brier_skill ?? -1) >= 0; return (
                <tr key={r.strategy + r.asset_class}><td>{r.label}</td><td className="text-mute">{r.asset_class}</td><td className="r num">{r.trades}</td>
                  <td className="r num">{pct(r.tp1_hit_rate)}</td><td className={`r num ${(r.avg_net_pct ?? 0) > 0 ? "text-up" : "text-down"}`}>{r.avg_net_pct == null ? "—" : `${r.avg_net_pct >= 0 ? "+" : ""}${fmtNum(r.avg_net_pct)}%`}</td>
                  <td className="r num">{v.oos_predictions ?? 0}</td><td className="r num">{fmtNum(v.brier, 3)}</td><td className="r num text-mute">{fmtNum(v.brier_baseline, 3)}</td><td className="r num">{fmtNum(v.ece, 3)}</td>
                  <td>{r.trades < 30 ? <Pill>Insufficient samples</Pill> : !ok ? <Pill kind="warn">No skill vs baseline</Pill> : <Pill kind="up">Calibrated</Pill>}</td></tr>); })}</tbody>
            </table></div>
          )}
        </Panel>
        {rows.length > 0 && (
          <Panel title="Reliability (out-of-sample): predicted vs observed TP1 frequency">
            <div className="grid md:grid-cols-2 xl:grid-cols-3 gap-3 p-3">
              {rows.filter((r: any) => r.validation?.reliability?.length).map((r: any) => (
                <div key={r.strategy + r.asset_class} className="border border-line rounded-[3px] p-2">
                  <div className="text-xs mb-1">{r.label} · {r.asset_class}</div>
                  <table className="grid-t"><thead><tr><th>Bin</th><th className="r">n</th><th className="r">Predicted</th><th className="r">Observed</th></tr></thead>
                    <tbody>{r.validation.reliability.map((b: any) => <tr key={b.bin}><td className="num">{b.bin}</td><td className="r num">{b.n}</td><td className="r num">{pct(b.predicted)}</td><td className="r num">{pct(b.observed)}</td></tr>)}</tbody></table>
                </div>))}
            </div>
          </Panel>
        )}
        <p className="text-2xs text-dim">Historical simulated results do not guarantee future outcomes. {cal?.survivorship_warning}</p>
      </div>
    </>
  );
}
