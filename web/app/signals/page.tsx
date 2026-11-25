"use client";
import { useState } from "react";
import { useApi } from "@/lib/api";
import { ReportDrawer } from "@/components/Report";
import { Empty, ErrorBox, Loading, PageHead, Panel, Pill } from "@/components/ui";

type Opp = { id: number; ts: number; asset: string; asset_class: string; strategy: string; direction: string; status: string; reason: string | null };

export default function Signals() {
  const [status, setStatus] = useState("");
  const d = useApi<{ opportunities: Opp[]; counts: Record<string, number> }>(`/api/agent/opportunities?limit=300${status ? `&status=${status}` : ""}`, 15_000);
  const [report, setReport] = useState<number | null>(null);
  return (
    <>
      <PageHead title="Long / Short Signals" sub="Every setup the agent found, accepted or rejected, with the gate that decided it. Signals need a realistic 2–5% target, positive expected net return and verified historical evidence."
        right={<select id="sig-status" value={status} onChange={e => setStatus(e.target.value)} className="bg-panel border border-line rounded-[2px] h-7 px-2 text-xs">
          <option value="">All</option><option value="accepted">Accepted (simulated position)</option><option value="rejected">Rejected / NO TRADE</option></select>} />
      <Panel title="Signal log" right={d.data && <span className="flex gap-1.5">{Object.entries(d.data.counts).map(([k, v]) => <Pill key={k} kind={k === "accepted" ? "up" : "warn"}>{k} {v}</Pill>)}</span>}>
        {d.error ? <ErrorBox message={d.error} onRetry={d.reload} /> : !d.data ? <Loading rows={8} /> : d.data.opportunities.length === 0 ? <Empty>No signals yet. Activate the AI agent; setups are evaluated each time a candle closes.</Empty> : (
          <div className="overflow-x-auto"><table className="grid-t">
            <thead><tr><th>Time (UTC)</th><th>Asset</th><th>Class</th><th>Signal</th><th>Strategy</th><th>Decision</th><th>Reason</th><th /></tr></thead>
            <tbody>{d.data.opportunities.map(o => (
              <tr key={o.id}><td className="num text-mute">{new Date(o.ts * 1000).toISOString().slice(5, 19).replace("T", " ")}</td><td>{o.asset}</td><td className="text-mute">{o.asset_class}</td>
                <td><Pill kind={o.direction === "long" ? "up" : "down"}>{o.status === "accepted" ? (o.direction === "long" ? "BUY / LONG" : "SHORT") : "NO TRADE"}</Pill></td>
                <td className="text-mute">{o.strategy}</td><td><Pill kind={o.status === "accepted" ? "up" : "warn"}>{o.status}</Pill></td>
                <td className="!whitespace-normal text-xs text-mute min-w-[280px]">{o.reason ?? "all gates passed"}</td>
                <td><button onClick={() => setReport(o.id)} className="text-2xs text-amber hover:underline">Report</button></td></tr>))}</tbody>
          </table></div>
        )}
      </Panel>
      <p className="text-2xs text-dim mt-2">SELL/EXIT and COVER events for simulated positions appear in the AI Agent activity log and on the Paper Portfolio page.</p>
      <ReportDrawer id={report} onClose={() => setReport(null)} />
    </>
  );
}
