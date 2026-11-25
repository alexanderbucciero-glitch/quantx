"use client";
import { LineChart } from "./Chart";
export function EquityChart({ points, height = 240 }: { points: { ts?: number; time?: number; equity: number }[]; height?: number }) {
  // lightweight-charts needs strictly increasing times
  const seen = new Set<number>();
  const data = points.map(p => ({ time: Math.floor((p.ts ?? p.time) as number), value: p.equity }))
    .filter(p => (seen.has(p.time) ? false : (seen.add(p.time), true))).sort((a, b) => a.time - b.time);
  if (data.length < 2) return <div className="p-4 text-xs text-mute">Not enough equity history yet. A snapshot is stored every minute while QUANTX runs.</div>;
  return <LineChart data={data} height={height} />;
}
