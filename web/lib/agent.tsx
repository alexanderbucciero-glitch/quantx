"use client";
import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";

export type AgentEvent = { ts: number; level: string; kind: string; asset: string | null; strategy: string | null; message: string; latency_ms: number | null; data?: unknown };
export type AgentStatus = {
  state: string; active: boolean; current: { task: string | null; asset: string | null; strategy: string | null };
  started_at: number | null; counters: Record<string, number>;
  assets: { total: number; eligible: number; by_class: Record<string, number> };
  positions_open: number;
  latency: Record<string, { avg: number | null; p95: number | null; n: number }>;
  stream: { connected: boolean; messages: number; last_msg_age_s: number | null };
  sources: Record<string, { ok: boolean; detail: string; at: number }>;
  errors: AgentEvent[]; calibration: Record<string, unknown>; research_only: Record<string, string>;
};

type Ctx = {
  status: AgentStatus | null; events: AgentEvent[]; connected: boolean; busy: boolean; error: string | null;
  activate: () => Promise<void>; deactivate: () => Promise<void>;
};
const AgentCtx = createContext<Ctx | null>(null);

function wsUrl() {
  const env = process.env.NEXT_PUBLIC_QUANTX_WS;
  if (env) return env;
  const proto = location.protocol === "https:" ? "wss" : "ws";
  return `${proto}://${location.hostname}:8000/api/ws/agent`;
}

/** One live connection for the whole app: WebSocket push, with HTTP polling as a fallback. */
export function AgentProvider({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [events, setEvents] = useState<AgentEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const wsRef = useRef<WebSocket | null>(null);

  useEffect(() => {
    let stop = false, retry = 1000, poll: ReturnType<typeof setInterval> | null = null;
    const startPoll = () => {
      if (poll) return;
      const tick = async () => {
        try {
          const [s, l] = await Promise.all([fetch("/api/agent/status").then(r => r.json()), fetch("/api/agent/log?limit=200").then(r => r.json())]);
          setStatus(s); setEvents(l.events); setError(null);
        } catch { setError("QUANTX backend is not reachable"); }
      };
      tick(); poll = setInterval(tick, 2500);
    };
    const connect = () => {
      if (stop) return;
      let ws: WebSocket;
      try { ws = new WebSocket(wsUrl()); } catch { startPoll(); return; }
      wsRef.current = ws;
      ws.onopen = () => { setConnected(true); setError(null); retry = 1000; if (poll) { clearInterval(poll); poll = null; } };
      ws.onmessage = m => {
        const msg = JSON.parse(m.data);
        if (msg.type === "snapshot") { setStatus(msg.status); setEvents(msg.events); }
        else if (msg.type === "status") setStatus(msg.status);
        else if (msg.type === "log") setEvents(ev => [...ev.slice(-399), msg.event]);
      };
      ws.onclose = () => { setConnected(false); startPoll(); if (!stop) setTimeout(connect, retry = Math.min(retry * 2, 15000)); };
      ws.onerror = () => ws.close();
    };
    connect();
    return () => { stop = true; wsRef.current?.close(); if (poll) clearInterval(poll); };
  }, []);

  const call = useCallback(async (path: string) => {
    setBusy(true);
    try {
      const r = await fetch(path, { method: "POST" });
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      setStatus(await r.json()); setError(null);
    } catch (e) { setError(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(false); }
  }, []);

  return (
    <AgentCtx.Provider value={{ status, events, connected, busy, error,
      activate: () => call("/api/agent/activate"), deactivate: () => call("/api/agent/deactivate") }}>
      {children}
    </AgentCtx.Provider>
  );
}

export function useAgent() {
  const c = useContext(AgentCtx);
  if (!c) throw new Error("useAgent outside AgentProvider");
  return c;
}

export const STATE_LABEL: Record<string, string> = {
  offline: "Offline", initialising: "Initialising", running: "Running", scanning: "Scanning", analysing: "Analysing",
  evaluating: "Evaluating opportunities", monitoring: "Monitoring positions", error: "Error", stopped: "Stopped",
};
export const STATE_KIND: Record<string, "up" | "down" | "warn" | "info" | "neutral" | "amber"> = {
  offline: "neutral", initialising: "info", running: "up", scanning: "info", analysing: "info", evaluating: "amber",
  monitoring: "up", error: "down", stopped: "neutral",
};
