"use client";
import Link from "next/link";
import { useState } from "react";
import { MODE_LABEL, STATE_KIND, STATE_LABEL, useAgent } from "@/lib/agent";

/** ACTIVATE / DEACTIVATE / EMERGENCY STOP. Every button calls the backend; the label shows the backend's real state. */
export function AgentControl({ big = false }: { big?: boolean }) {
  const { status, busy, activate, deactivate, emergencyStop, error } = useAgent();
  const [confirm, setConfirm] = useState(false);
  const active = !!status?.active;
  const state = status?.state ?? "offline";
  const kind = STATE_KIND[state] ?? "neutral";
  const dot = { up: "bg-up", down: "bg-down", warn: "bg-warn", info: "bg-info", neutral: "bg-dim", amber: "bg-amber" }[kind];
  const h = big ? "h-11 text-[13px] px-5" : "h-7 text-[11.5px] px-3";
  const hasRisk = (status?.positions_open ?? 0) > 0 || active;
  return (
    <div className={`flex items-center gap-2 min-w-0 ${big ? "flex-wrap" : ""}`}>
      {!big && (
        <Link href="/agent" className="hidden sm:flex items-center gap-1.5 text-2xs text-mute hover:text-text min-w-0" title={status?.current.task ?? ""}>
          <span className={`w-1.5 h-1.5 rounded-full ${dot} ${active && state !== "error" ? "animate-pulse" : ""}`} />
          <span className="truncate max-w-[200px]">{error && !status ? "Backend offline" : `${STATE_LABEL[state] ?? state} · ${MODE_LABEL[status?.mode ?? "off"] ?? ""}`}</span>
        </Link>
      )}
      <button onClick={active ? deactivate : activate} disabled={busy || (!status && !!error)}
        className={`${h} rounded-[3px] font-semibold tracking-[0.12em] num border transition-colors disabled:opacity-50 ${
          active ? "border-down/60 text-down hover:bg-down/10" : "border-amber bg-amber text-ink hover:brightness-110"}`}>
        {busy ? "…" : active ? "DEACTIVATE" : "ACTIVATE"}
      </button>
      {hasRisk && !confirm && (
        <button onClick={() => setConfirm(true)} disabled={busy}
          className={`${h} rounded-[3px] font-semibold tracking-[0.08em] num border border-down text-down hover:bg-down/10`} title="Close every simulated position at market and stop the agent">
          {big ? "EMERGENCY STOP" : "STOP"}
        </button>
      )}
      {confirm && (
        <span className="flex items-center gap-1.5 text-2xs">
          <span className="text-down hidden sm:inline">Close all positions at market?</span>
          <button onClick={async () => { setConfirm(false); await emergencyStop(); }} className={`${h} rounded-[3px] bg-down text-ink font-semibold`}>Confirm</button>
          <button onClick={() => setConfirm(false)} className={`${h} rounded-[3px] border border-line text-mute`}>Cancel</button>
        </span>
      )}
    </div>
  );
}
