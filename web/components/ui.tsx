"use client";
import Link from "next/link";
import type { ReactNode } from "react";
import { ago } from "@/lib/format";

export function Panel({ title, right, children, className = "", bodyClass = "" }:
  { title?: ReactNode; right?: ReactNode; children: ReactNode; className?: string; bodyClass?: string }) {
  return (
    <section className={`bg-panel border border-line rounded-[3px] flex flex-col min-w-0 ${className}`}>
      {title != null && (
        <header className="flex items-center justify-between gap-3 px-3 h-8 border-b border-line shrink-0">
          <h2 className="label !text-text/90 truncate">{title}</h2>
          <div className="flex items-center gap-2 text-2xs text-mute shrink-0">{right}</div>
        </header>
      )}
      <div className={`min-w-0 ${bodyClass}`}>{children}</div>
    </section>
  );
}

export function SourceTag({ source, at }: { source: string; at?: number | string | null }) {
  return <span className="text-2xs text-dim truncate">{source}{at != null && <> · <span className="num">{ago(at)}</span></>}</span>;
}

export function Loading({ rows = 4 }: { rows?: number }) {
  return (
    <div className="p-3 space-y-2" aria-busy="true">
      {Array.from({ length: rows }).map((_, i) => <div key={i} className="h-3 rounded-sm bg-raised animate-pulse" style={{ width: `${90 - i * 12}%` }} />)}
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div className="m-3 border border-down/40 bg-down/5 rounded-[3px] p-3 text-xs">
      <div className="text-down font-medium mb-1">Source unavailable</div>
      <div className="text-mute break-words">{message}</div>
      <div className="text-dim mt-1">No substitute data is shown.</div>
      {onRetry && <button onClick={onRetry} className="mt-2 text-amber hover:underline">Retry</button>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="p-4 text-xs text-mute">{children}</div>;
}

export function Pill({ children, kind = "neutral" }: { children: ReactNode; kind?: "neutral" | "up" | "down" | "warn" | "info" | "amber" }) {
  const map = {
    neutral: "border-line text-mute", up: "border-up/40 text-up bg-up/5", down: "border-down/40 text-down bg-down/5",
    warn: "border-warn/40 text-warn bg-warn/5", info: "border-info/40 text-info bg-info/5", amber: "border-amber/50 text-amber bg-amber/5",
  };
  return <span className={`inline-flex items-center h-[18px] px-1.5 rounded-[2px] border text-2xs whitespace-nowrap ${map[kind]}`}>{children}</span>;
}

/** Shown on pages whose engine is not built yet. States exactly what is missing. */
export function StageNotice({ stage, title, what, needs, related }:
  { stage: number; title: string; what: string[]; needs: string[]; related?: { href: string; label: string }[] }) {
  return (
    <Panel title={<>Stage {stage} · {title}</>} right={<Pill kind="warn">Not built yet</Pill>}>
      <div className="p-4 grid gap-6 md:grid-cols-2 text-[13px] leading-relaxed">
        <div>
          <div className="label mb-2">This page will show</div>
          <ul className="space-y-1 text-text/90">{what.map(w => <li key={w} className="flex gap-2"><span className="text-dim">›</span>{w}</li>)}</ul>
        </div>
        <div>
          <div className="label mb-2">Required before it can show anything real</div>
          <ul className="space-y-1 text-mute">{needs.map(w => <li key={w} className="flex gap-2"><span className="text-dim">›</span>{w}</li>)}</ul>
          <p className="mt-4 text-dim text-xs">QUANTX does not display placeholder forecasts, signals or P&amp;L. This page stays empty until the engine behind it exists and has been validated.</p>
          {related && <div className="mt-3 flex flex-wrap gap-3 text-xs">{related.map(r => <Link key={r.href} href={r.href} className="text-amber hover:underline">{r.label} →</Link>)}</div>}
        </div>
      </div>
    </Panel>
  );
}

export function PageHead({ title, sub, right }: { title: string; sub?: string; right?: ReactNode }) {
  return (
    <div className="flex flex-wrap items-end justify-between gap-3 mb-3">
      <div className="min-w-0">
        <h1 className="text-[17px] font-semibold tracking-tight text-text">{title}</h1>
        {sub && <p className="text-xs text-mute mt-0.5 max-w-3xl">{sub}</p>}
      </div>
      {right}
    </div>
  );
}

export function Sparkline({ values, w = 96, h = 24 }: { values: (number | null)[]; w?: number; h?: number }) {
  const v = values.filter((x): x is number => x != null);
  if (v.length < 2) return <span className="text-dim text-2xs">—</span>;
  const min = Math.min(...v), max = Math.max(...v), span = max - min || 1;
  const pts = v.map((x, i) => `${(i / (v.length - 1)) * w},${h - 2 - ((x - min) / span) * (h - 4)}`).join(" ");
  const up = v[v.length - 1] >= v[0];
  const col = up ? "var(--up)" : "var(--down)";
  const last = pts.split(" ").pop()!.split(",");
  return (
    <svg width={w} height={h} viewBox={`0 0 ${w} ${h}`} className="block" aria-hidden>
      <polyline points={`0,${h} ${pts} ${w},${h}`} fill={col} opacity={0.08} />
      <polyline points={pts} fill="none" stroke={col} strokeWidth={1.25} />
      <circle cx={last[0]} cy={last[1]} r={1.8} fill={col} />
    </svg>
  );
}

export function Stat({ label, value, sub, valueClass = "" }: { label: string; value: ReactNode; sub?: ReactNode; valueClass?: string }) {
  return (
    <div className="min-w-0">
      <div className="label truncate">{label}</div>
      <div className={`num text-[15px] mt-0.5 ${valueClass}`}>{value}</div>
      {sub && <div className="text-2xs text-dim mt-0.5 truncate">{sub}</div>}
    </div>
  );
}
