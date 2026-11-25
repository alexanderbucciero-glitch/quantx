"use client";
import { useApi } from "@/lib/api";
import { fmtNum, fmtPrice } from "@/lib/format";
import { ErrorBox, Loading, Panel, Pill } from "./ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
export type Report = any;

const pct = (x: number | null | undefined, d = 1) => (x == null ? "—" : `${(x * 100).toFixed(d)}%`);

export function ConfidenceBlock({ c }: { c: Report["confidence"] }) {
  if (!c) return null;
  const ci = c.calibrated_ci90 as [number, number] | null;
  return (
    <div className="grid sm:grid-cols-3 gap-3">
      <div className="border border-line rounded-[3px] p-2.5">
        <div className="label">AI confidence (heuristic)</div>
        <div className="num text-[18px] mt-1">{fmtNum(c.ai_confidence, 2)}</div>
        <div className="text-2xs text-dim mt-1 leading-snug">{c.ai_confidence_note}</div>
      </div>
      <div className="border border-line rounded-[3px] p-2.5">
        <div className="label">Historical win rate</div>
        <div className="num text-[18px] mt-1">{pct(c.historical_win_rate)}</div>
        <div className="text-2xs text-dim mt-1 leading-snug">TP1 reached in {c.historical_samples ?? 0} simulated past setups (in-sample).</div>
      </div>
      <div className={`border rounded-[3px] p-2.5 ${c.calibrated_probability == null ? "border-warn/40" : "border-line"}`}>
        <div className="label">Calibrated probability</div>
        <div className="num text-[18px] mt-1">{c.calibrated_probability == null ? "Insufficient" : pct(c.calibrated_probability)}</div>
        <div className="text-2xs text-dim mt-1 leading-snug">
          {c.calibrated_probability == null ? c.reason : <>90% CI {pct(ci?.[0])}–{pct(ci?.[1])}; walk-forward Brier {fmtNum(c.validation?.brier, 3)} vs baseline {fmtNum(c.validation?.brier_baseline, 3)}{c.reason ? ` — ${c.reason}` : ""}</>}
        </div>
      </div>
    </div>
  );
}

export function ReportView({ r }: { r: Report }) {
  const p = r.plan;
  return (
    <div className="grid gap-3 text-[13px]">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-[15px] font-semibold">{r.asset}</span>
        <Pill kind={r.direction === "long" ? "up" : "down"}>{r.direction.toUpperCase()}</Pill>
        <Pill kind="info">{r.strategy_label}</Pill>
        <Pill>{r.timeframe}</Pill>
        <Pill kind={r.accepted ? "up" : "warn"}>{r.accepted ? "Simulated position opened" : "Rejected"}</Pill>
        <span className="text-2xs text-dim num ml-auto">{new Date(r.timestamp * 1000).toISOString().replace("T", " ").slice(0, 19)} UTC · price {fmtPrice(r.price)}</span>
      </div>
      <p className="text-text/90 leading-relaxed">{r.explanation}</p>
      <ConfidenceBlock c={r.confidence} />
      {p && (
        <div className="overflow-x-auto"><table className="grid-t">
          <thead><tr><th>Entry ref.</th><th>Stop</th><th>TP1</th><th>TP2</th><th className="r">Gross TP1</th><th className="r">Fees</th><th className="r">Slippage</th><th className="r">Net TP1</th><th className="r">R/R net</th><th className="r">Exp. hold</th></tr></thead>
          <tbody><tr>
            <td className="num">{fmtPrice(p.entry)}</td><td className="num text-down">{fmtPrice(p.stop)} <span className="text-dim">(-{p.stop_pct.toFixed(2)}%)</span></td>
            <td className="num text-up">{fmtPrice(p.tp1)}</td><td className="num text-up">{fmtPrice(p.tp2)}</td>
            <td className="r num">+{p.tp1_pct.toFixed(1)}%</td><td className="r num">{p.fee_pct.toFixed(2)}%</td><td className="r num">{p.slippage_pct.toFixed(2)}%</td>
            <td className="r num">{p.net_tp1_pct >= 0 ? "+" : ""}{p.net_tp1_pct.toFixed(2)}%</td><td className="r num">{p.rr_net.toFixed(2)}</td>
            <td className="r num">{p.expected_hold_bars}/{p.max_hold_bars} bars</td>
          </tr></tbody>
        </table></div>
      )}
      {r.plan_rejection && <div className="text-xs text-warn">No viable plan: {String(r.plan_rejection.reason).replaceAll("_", " ")}</div>}
      <div className="grid md:grid-cols-3 gap-3">
        <div><div className="label mb-1 !text-up">Bullish evidence</div><ul className="text-xs space-y-0.5 text-text/85">{(r.bullish_evidence || []).map((x: string, i: number) => <li key={i}>› {x}</li>)}{!r.bullish_evidence?.length && <li className="text-dim">None</li>}</ul></div>
        <div><div className="label mb-1 !text-down">Bearish evidence</div><ul className="text-xs space-y-0.5 text-text/85">{(r.bearish_evidence || []).map((x: string, i: number) => <li key={i}>› {x}</li>)}{!r.bearish_evidence?.length && <li className="text-dim">None</li>}</ul></div>
        <div><div className="label mb-1">Invalidation</div><ul className="text-xs space-y-0.5 text-text/85">{(r.invalidation || []).map((x: string, i: number) => <li key={i}>› {x}</li>)}</ul></div>
      </div>
      <div className="grid md:grid-cols-3 gap-3 text-xs">
        <div><div className="label mb-1">Liquidity</div>
          <div className="text-mute">24h volume {r.liquidity?.volume_24h_usd ? "$" + fmtNum(r.liquidity.volume_24h_usd / 1e6, 1) + "M" : "—"} · pool {r.liquidity?.liquidity_usd ? "$" + fmtNum(r.liquidity.liquidity_usd / 1e3, 0) + "k" : "—"} · spread {r.liquidity?.spread_pct != null ? r.liquidity.spread_pct.toFixed(3) + "%" : "—"}</div>
          <div className="text-dim mt-1">Buy-side above: {(r.liquidity?.pools?.buy_side || []).slice(0, 3).map((x: any) => fmtPrice(x.price)).join(", ") || "—"}</div>
          <div className="text-dim">Sell-side below: {(r.liquidity?.pools?.sell_side || []).slice(0, 3).map((x: any) => fmtPrice(x.price)).join(", ") || "—"}</div></div>
        <div><div className="label mb-1">Market structure</div><div className="text-mute">{r.market_structure?.label} · last break {r.market_structure?.trend}</div>
          <div className="text-dim mt-1">Relative volume {r.volume?.relative_volume != null ? r.volume.relative_volume.toFixed(2) + "×" : "—"}</div></div>
        <div><div className="label mb-1">News</div>{r.news?.length ? <ul className="space-y-0.5">{r.news.map((n: any) => <li key={n.id}><a className="hover:text-amber" href={n.url} target="_blank" rel="noreferrer">{n.title}</a> <span className="text-dim">({n.primary_source ? "primary" : "unverified"})</span></li>)}</ul> : <div className="text-dim">No linked headlines</div>}</div>
      </div>
      <div>
        <div className="label mb-1">Decision gates</div>
        <div className="grid gap-1">{(r.gates || []).map((g: any) => (
          <div key={g.gate} className="flex gap-2 text-xs"><span className={g.passed ? "text-up" : "text-down"}>{g.passed ? "✓" : "✗"}</span><span className="text-text/90 w-56 shrink-0">{g.gate}</span><span className="text-mute">{g.detail}</span></div>))}
        </div>
      </div>
      <p className="text-2xs text-dim">{r.confidence?.disclaimer} All positions are simulated.</p>
    </div>
  );
}

export function ReportDrawer({ id, onClose }: { id: number | null; onClose: () => void }) {
  return id == null ? null : <ReportLoader id={id} onClose={onClose} />;
}

function ReportLoader({ id, onClose }: { id: number; onClose: () => void }) {
  const d = useApi<{ report: Report }>(`/api/agent/opportunities/${id}`);
  return (
    <div className="fixed inset-0 z-40 flex justify-end">
      <button aria-label="Close report" className="absolute inset-0 bg-black/60" onClick={onClose} />
      <div className="relative w-full max-w-3xl h-full overflow-y-auto bg-ink border-l border-line">
        <Panel title={`Research report #${id}`} right={<button onClick={onClose} className="text-amber hover:underline">Close</button>} className="rounded-none border-0 min-h-full">
          <div className="p-3">{d.error ? <ErrorBox message={d.error} /> : !d.data ? <Loading rows={10} /> : <ReportView r={d.data.report} />}</div>
        </Panel>
      </div>
    </div>
  );
}
