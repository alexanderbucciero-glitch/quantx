export const fmtNum = (v: number | null | undefined, d = 2) =>
  v == null || Number.isNaN(v) ? "—" : v.toLocaleString("en-US", { minimumFractionDigits: d, maximumFractionDigits: d });

export const fmtPct = (v: number | null | undefined, d = 2, isFraction = true) => {
  if (v == null || Number.isNaN(v)) return "—";
  const x = isFraction ? v * 100 : v;
  return `${x > 0 ? "+" : ""}${x.toFixed(d)}%`;
};

export const fmtCompact = (v: number | null | undefined) => {
  if (v == null || Number.isNaN(v)) return "—";
  const a = Math.abs(v);
  if (a >= 1e12) return (v / 1e12).toFixed(2) + "T";
  if (a >= 1e9) return (v / 1e9).toFixed(2) + "B";
  if (a >= 1e6) return (v / 1e6).toFixed(2) + "M";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + "K";
  return v.toFixed(2);
};

export const fmtPrice = (v: number | null | undefined) => {
  if (v == null) return "—";
  if (v >= 1000) return fmtNum(v, 2);
  if (v >= 1) return fmtNum(v, 4);
  if (v >= 0.0001) return v.toFixed(6);
  return v.toPrecision(3);
};

export const tone = (v: number | null | undefined) => (v == null ? "text-mute" : v > 0 ? "text-up" : v < 0 ? "text-down" : "text-mute");

export const ago = (iso: string | number | null | undefined) => {
  if (iso == null) return "—";
  const t = typeof iso === "number" ? (iso < 1e12 ? iso * 1000 : iso) : Date.parse(iso);
  if (Number.isNaN(t)) return "—";
  const s = Math.max(0, (Date.now() - t) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
};
