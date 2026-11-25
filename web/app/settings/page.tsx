"use client";
import { useApi } from "@/lib/api";
import { ago } from "@/lib/format";
import { ErrorBox, Loading, PageHead, Panel, Pill } from "@/components/ui";

type Check = { key: string; name: string; category: string; needs: string; status: string; latency_ms?: number; error?: string; checked_at: number };
type Other = { key: string; name: string; category: string; status: string; needs: string; how: string };

const ST: Record<string, ["up" | "down" | "warn" | "neutral" | "info", string]> = {
  live: ["up", "Live"], error: ["down", "Error"], not_configured: ["warn", "Not configured"],
  configured: ["info", "Key set (unverified)"], unavailable_free: ["neutral", "No free access"],
};

export default function Settings() {
  const d = useApi<{ live_checks: Check[]; other: Other[]; user_agent_set: boolean }>("/api/integrations");
  return (
    <>
      <PageHead title="Integrations & Settings" sub="Each source is checked with a real request every time you load this page. A source shows as live only if that request just succeeded." right={<button onClick={d.reload} className="text-xs text-amber hover:underline">Re-run checks</button>} />
      <div className="grid gap-3">
        <Panel title="Free sources (no account needed)">
          {d.error ? <ErrorBox message={d.error} onRetry={d.reload} /> : !d.data ? <Loading rows={9} /> : (
            <div className="overflow-x-auto"><table className="grid-t">
              <thead><tr><th>Source</th><th>Used for</th><th>Access</th><th>Status</th><th className="r">Latency</th><th>Last answered</th></tr></thead>
              <tbody>{d.data.live_checks.map(c => (
                <tr key={c.key}><td>{c.name}</td><td className="text-mute">{c.category}</td><td className="text-mute">{c.needs}</td>
                  <td><Pill kind={ST[c.status]?.[0]}>{ST[c.status]?.[1] || c.status}</Pill>{c.error && <span className="ml-2 text-2xs text-down">{c.error}</span>}</td>
                  <td className="r num text-mute">{c.latency_ms != null ? `${c.latency_ms} ms` : c.status === "live" ? "cached" : "—"}</td><td className="num text-2xs text-dim">{ago(c.checked_at)}</td></tr>))}</tbody>
            </table></div>
          )}
          {d.data && !d.data.user_agent_set && <div className="px-3 py-2 text-2xs text-warn border-t border-line">Set QUANTX_USER_AGENT in backend/.env (SEC requires contact details in the User-Agent).</div>}
        </Panel>
        <Panel title="Sources that need a free key, approval or a paid licence">
          {!d.data ? <Loading rows={6} /> : (
            <div className="overflow-x-auto"><table className="grid-t">
              <thead><tr><th>Source</th><th>Used for</th><th>Status</th><th>Requirement</th><th>How</th></tr></thead>
              <tbody>{d.data.other.map(o => (
                <tr key={o.key}><td>{o.name}</td><td className="text-mute">{o.category}</td><td><Pill kind={ST[o.status]?.[0]}>{ST[o.status]?.[1] || o.status}</Pill></td>
                  <td className="text-mute">{o.needs}</td><td className="text-mute !whitespace-normal min-w-[280px] text-xs">{o.how}</td></tr>))}</tbody>
            </table></div>
          )}
        </Panel>
        <Panel title="Safety">
          <div className="p-3 text-xs text-mute leading-relaxed">QUANTX has no brokerage, exchange-account or wallet connection, and no code path to place real orders. API keys are read from <span className="num text-text">backend/.env</span> on your machine and are never sent to the browser.</div>
        </Panel>
      </div>
    </>
  );
}
