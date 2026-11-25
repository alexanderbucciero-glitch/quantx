"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { useApi } from "@/lib/api";
import { MODE_LABEL, STATE_LABEL, useAgent } from "@/lib/agent";
import { fmtNum, fmtPct, fmtPrice, tone } from "@/lib/format";
import { AgentControl } from "./AgentControl";
import { Empty, ErrorBox, Loading, Panel, Pill, Stat } from "./ui";

/* eslint-disable @typescript-eslint/no-explicit-any */
export const dur = (s: number | null | undefined) => s == null ? "—" : s < 3600 ? `${Math.max(0, Math.round(s / 60))}m` : s < 86400 ? `${(s / 3600).toFixed(1)}h` : `${(s / 86400).toFixed(1)}d`;
const utc = (t: number) => new Date(t * 1000).toISOString().slice(5, 16).replace("T", " ");

function useNow(ms = 1000) {
  const [n, setN] = useState(() => Date.now() / 1000);
  useEffect(() => { const t = setInterval(() => setN(Date.now() / 1000), ms); return () => clearInterval(t); }, [ms]);
  return n;
}

export function AgentStatusPanel() {
  const { status } = useAgent();
  const c = status?.counters ?? {};
  const ld = status?.last_decision;
  return (
    <Panel title="Autonomous agent" right={<Link href="/agent" className="text-amber hover:underline">Live log →</Link>}>
      <div className="p-3 grid gap-3">
        <AgentControl big />
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <Stat label="Status" value={<span className="font-sans text-[13px]">{status?.active ? "ACTIVE" : "INACTIVE"}</span>}
            sub={MODE_LABEL[status?.mode ?? "off"]} valueClass={status?.active ? "text-up" : "text-mute"} />
          <Stat label="Now" value={<span className="font-sans text-[13px]">{STATE_LABEL[status?.state ?? "offline"]}</span>} sub={status?.current.task ?? "—"} />
          <Stat label="Markets analysed" value={status?.assets.total ?? 0}
            sub={Object.entries(status?.assets.by_class ?? {}).map(([k, v]) => `${v} ${k}`).join(" · ") || "—"} />
          <Stat label="Opportunities / executed" value={`${c.opportunities_identified ?? 0} / ${c.trades_executed ?? 0}`} sub={`${c.opportunities_rejected ?? 0} rejected by gates`} />
        </div>
        <div className="text-xs grid gap-1">
          <div><span className="label mr-2">Current strategy</span><span className="text-text">{status?.current.strategy ?? "—"}</span>
            <span className="label ml-4 mr-2">Evidence tiers</span>
            {Object.entries(status?.tiers ?? {}).map(([k, v]) => <span key={k} className="mr-1.5"><Pill kind={k === "active" ? "up" : k === "exploration" ? "amber" : "neutral"}>{v} {k}</Pill></span>)}
            {!Object.keys(status?.tiers ?? {}).length && <span className="text-dim">not calibrated yet</span>}</div>
          <div><span className="label mr-2">Last decision</span>{ld ? <>
            <Pill kind={ld.decision === "NO TRADE" ? "neutral" : ld.decision.startsWith("BUY") || ld.decision === "COVER" ? "up" : "down"}>{ld.decision}</Pill>
            <span className="text-text ml-1.5">{ld.asset}</span><span className="text-mute ml-1.5">{ld.reason}</span>
            <span className="text-dim num ml-1.5">{utc(ld.ts)} UTC</span></> : <span className="text-dim">none yet</span>}</div>
          {status?.entries_suspended && <div className="text-warn">New entries suspended: {status.entries_suspended}</div>}
          {(status as any)?.selection_warning && <div className="text-warn">{(status as any).selection_warning}</div>}
        </div>
      </div>
    </Panel>
  );
}

export function PortfolioPanel({ account = "live" }: { account?: string }) {
  const d = useApi<any>(`/api/paper/portfolio?account=${account}`, 5000);
  const p = d.data;
  return (
    <Panel title={`Portfolio (simulated)${account !== "live" ? ` · ${account}` : ""}`} right={<Link href={`/portfolio${account !== "live" ? `?account=${account}` : ""}`} className="text-amber hover:underline">Details →</Link>}>
      {d.error ? <ErrorBox message={d.error} /> : !p ? <Loading rows={3} /> : (
        <div className="p-3 grid grid-cols-2 sm:grid-cols-4 xl:grid-cols-7 gap-3">
          <Stat label="Starting capital" value={`$${fmtNum(p.starting_balance, 0)}`} />
          <Stat label="Cash balance" value={`$${fmtNum(p.cash)}`} />
          <Stat label="Available capital" value={`$${fmtNum(p.available)}`} sub={`exposure $${fmtNum(p.exposure, 0)}`} />
          <Stat label="Total portfolio value" value={`$${fmtNum(p.equity)}`} />
          <Stat label="Realised P&L" value={`$${fmtNum(p.realized)}`} valueClass={tone(p.realized)} sub={`fees $${fmtNum(p.fees_paid)}`} />
          <Stat label="Unrealised P&L" value={`$${fmtNum(p.unrealized)}`} valueClass={tone(p.unrealized)} />
          <Stat label="Overall return" value={fmtPct(p.total_return_pct / 100)} valueClass={tone(p.total_return_pct)} sub={`${p.closed_trades} closed trades`} />
        </div>
      )}
    </Panel>
  );
}

export function OpenPositions({ account = "live", onReport }: { account?: string; onReport?: (id: number) => void }) {
  const h = useApi<any>(`/api/paper/history?account=${account}&limit=50`, 4000);
  const now = useNow(5000);
  const close = async (id: number) => { await fetch(`/api/paper/positions/${id}/close`, { method: "POST" }); h.reload(); };
  const rows = h.data ? [...h.data.open, ...h.data.pending] : [];
  return (
    <Panel title="Open positions" right={<span className="text-2xs text-dim">prices update live; stops & targets are resting simulated orders</span>}>
      {h.error ? <ErrorBox message={h.error} /> : !h.data ? <Loading rows={3} /> : rows.length === 0 ? <Empty>No open simulated positions. The agent enters only when a setup passes its evidence and risk gates.</Empty> : (
        <div className="overflow-x-auto"><table className="grid-t">
          <thead><tr><th>Asset</th><th>Dir.</th><th>Strategy</th><th className="r">Entry</th><th className="r">Current</th><th className="r">Size</th><th className="r">P&L</th><th className="r">Stop</th><th className="r">Take-profit</th><th className="r">Held</th><th /></tr></thead>
          <tbody>{rows.map((p: any) => {
            const pending = p.status === "pending";
            const px = p.last_price ?? p.entry;
            const pnl = pending ? null : (p.direction === "long" ? 1 : -1) * (px - p.entry) * p.remaining_qty + (p.realized_pnl ?? 0);
            return (
              <tr key={p.id}>
                <td><button className="hover:text-amber" onClick={() => p.opportunity_id && onReport?.(p.opportunity_id)}>{p.display ?? p.asset}</button> <span className="text-2xs text-dim">{p.tf}</span>{p.tier === "exploration" && <span className="ml-1"><Pill kind="amber">explore</Pill></span>}</td>
                <td><Pill kind={p.direction === "long" ? "up" : "down"}>{p.direction}</Pill></td>
                <td className="text-mute">{p.strategy}</td>
                <td className="r num">{pending ? <span className="text-dim">limit pending</span> : fmtPrice(p.entry)}</td>
                <td className="r num">{fmtPrice(px)}</td>
                <td className="r num">{fmtNum(pending ? p.qty : p.remaining_qty, 4)}{p.tp1_done ? <span className="text-dim"> (½)</span> : null}</td>
                <td className={`r num ${tone(pnl)}`}>{pnl == null ? "—" : `$${fmtNum(pnl)}`}</td>
                <td className="r num text-down">{fmtPrice(p.stop)}</td>
                <td className="r num text-up">{p.tp1 ? `${fmtPrice(p.tp1)}${p.tp2 && p.tp2 !== p.tp1 ? ` / ${fmtPrice(p.tp2)}` : ""}` : <span className="text-dim">trailing</span>}</td>
                <td className="r num text-mute">{pending ? "—" : dur(now - p.opened_at)}</td>
                <td>{!pending && account === "live" && <button onClick={() => close(p.id)} className="text-2xs text-amber hover:underline">Close</button>}</td>
              </tr>);
          })}</tbody>
        </table></div>
      )}
    </Panel>
  );
}

export function TradingActivity({ account = "live", limit = 40 }: { account?: string; limit?: number }) {
  const d = useApi<any>(`/api/paper/activity?account=${account}&limit=${limit}`, 4000);
  return (
    <Panel title="Trading activity · every executed BUY and SELL">
      {d.error ? <ErrorBox message={d.error} /> : !d.data ? <Loading rows={4} /> : d.data.fills.length === 0 ? <Empty>No simulated transactions yet.</Empty> : (
        <div className="overflow-x-auto max-h-[420px]"><table className="grid-t">
          <thead><tr><th>Time (UTC)</th><th>Action</th><th>Asset</th><th className="r">Price</th><th className="r">Quantity</th><th className="r">Fee</th><th>Strategy</th><th>Reason</th></tr></thead>
          <tbody>{d.data.fills.map((f: any) => (
            <tr key={f.id}><td className="num text-mute">{utc(f.ts)}</td>
              <td><Pill kind={f.side === "buy" ? "up" : "down"}>{f.side === "buy" ? (f.kind === "entry" ? "BUY" : "BUY (cover)") : (f.kind === "entry" ? "SELL SHORT" : "SELL")}</Pill> <span className="text-2xs text-dim">{f.kind.replace("_", " ")}</span></td>
              <td>{f.display ?? f.asset}</td><td className="r num">{fmtPrice(f.price)}</td><td className="r num">{fmtNum(f.qty, 4)}</td>
              <td className="r num text-mute">${fmtNum(f.fee)} <span className="text-dim">{f.liquidity}</span></td>
              <td className="text-mute">{f.strategy}</td><td className="!whitespace-normal text-xs text-mute min-w-[220px]">{f.reason}</td></tr>))}</tbody>
        </table></div>
      )}
    </Panel>
  );
}

export function LatestReports({ account = "live" }: { account?: string }) {
  const d = useApi<any>(`/api/reports?account=${account}&limit=8`, 15_000);
  return (
    <Panel title="Reports" right={<Link href={`/reports${account !== "live" ? `?account=${account}` : ""}`} className="text-amber hover:underline">All reports →</Link>}>
      {!d.data ? <Loading rows={3} /> : d.data.reports.length === 0 ? <Empty>A trade report is written when each simulated position closes; daily reports after each UTC midnight.</Empty> : (
        <ul className="divide-y divide-line/60">{d.data.reports.map((r: any) => (
          <li key={r.id}><Link href={`/reports?id=${r.id}${account !== "live" ? `&account=${account}` : ""}`} className="px-3 py-1.5 text-xs flex gap-2 hover:bg-raised">
            <Pill kind={r.kind === "trade" ? "info" : "amber"}>{r.kind}</Pill>
            {r.kind === "trade" ? <><span className="text-text">{r.summary.asset}</span><span className="text-dim">{r.summary.strategy}</span>
              <span className={`num ml-auto ${tone(r.summary.net_pnl)}`}>{r.summary.net_pnl >= 0 ? "+" : ""}${fmtNum(r.summary.net_pnl)}</span></>
              : <><span className="text-text">{r.summary.label}</span><span className="text-dim">{r.summary.trades_closed} trades</span>
                <span className={`num ml-auto ${tone(r.summary.total_return_pct)}`}>{fmtPct((r.summary.total_return_pct ?? 0) / 100)}</span></>}
          </Link></li>))}</ul>
      )}
    </Panel>
  );
}
