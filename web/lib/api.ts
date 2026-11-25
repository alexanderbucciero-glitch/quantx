"use client";
import { useCallback, useEffect, useRef, useState } from "react";

export type ApiState<T> = { data: T | null; error: string | null; loading: boolean; reload: () => void; updatedAt: number | null };

export async function getJson<T>(path: string): Promise<T> {
  const r = await fetch(path, { cache: "no-store" });
  const body = await r.json().catch(() => null);
  if (!r.ok) {
    const d = body?.detail;
    const msg = typeof d === "string" ? d : d?.error ? `${d.source ? d.source + ": " : ""}${d.error}${d.how_to_fix ? " — " + d.how_to_fix : ""}` : `HTTP ${r.status}`;
    throw new Error(msg);
  }
  return body as T;
}

/** Fetch an API path; optionally refresh every `refreshMs`. Never substitutes placeholder data. */
export function useApi<T>(path: string | null, refreshMs?: number): ApiState<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<boolean>(!!path);
  const [updatedAt, setUpdatedAt] = useState<number | null>(null);
  const seq = useRef(0);
  const load = useCallback(async () => {
    if (!path) return;
    const id = ++seq.current;
    setLoading(true);
    try {
      const d = await getJson<T>(path);
      if (id === seq.current) { setData(d); setError(null); setUpdatedAt(Date.now()); }
    } catch (e) {
      if (id === seq.current) setError(e instanceof Error ? e.message : String(e));
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, [path]);
  useEffect(() => {
    setData(null); setError(null);
    load();
    if (!refreshMs) return;
    const t = setInterval(load, refreshMs);
    return () => clearInterval(t);
  }, [load, refreshMs]);
  return { data, error, loading, reload: load, updatedAt };
}
