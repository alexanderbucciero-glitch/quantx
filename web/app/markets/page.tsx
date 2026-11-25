"use client";
import { useState } from "react";
import { useApi } from "@/lib/api";
import { LineChart } from "@/components/Chart";
import { ErrorBox, Loading, PageHead, Panel, SourceTag } from "@/components/ui";
import { MarketsTable } from "@/components/widgets";

type Series = { id: string; name: string; units: string; data: { date: string; value: number | null }[]; fetched_at: number; first_date: string; last_date: string; missing: number };
const PICKS = ["SP500", "NASDAQCOM", "DJIA", "VIXCLS", "DGS10", "DGS2", "T10Y2Y", "DEXUSEU", "DCOILBRENTEU", "BAMLH0A0HYM2"];

export default function Markets() {
  const [id, setId] = useState("SP500");
  const s = useApi<Series>(`/api/markets/series/${id}`);
  return (
    <>
      <PageHead title="Global Markets" sub="US indices, volatility, Treasury curve, FX and energy from FRED. European equity indices need a licensed or keyed source (planned via Tiingo/EODHD)." />
      <div className="grid gap-3">
        <Panel title={s.data?.name || id} right={<>
          <select id="series" value={id} onChange={e => setId(e.target.value)} className="bg-raised border border-line rounded-[2px] text-xs px-1.5 h-6">
            {PICKS.map(p => <option key={p} value={p}>{p}</option>)}
          </select>
          <SourceTag source="FRED" at={s.data?.fetched_at} /></>}>
          {s.error ? <ErrorBox message={s.error} onRetry={s.reload} /> : !s.data ? <Loading rows={6} /> : (
            <>
              <LineChart data={s.data.data.filter(o => o.value != null).map(o => ({ time: o.date, value: o.value as number }))} height={340} />
              <div className="px-3 py-2 text-2xs text-dim border-t border-line">
                {s.data.first_date} → {s.data.last_date} · {s.data.data.length} observations · {s.data.missing} missing (holidays / not reported, left empty)
              </div>
            </>
          )}
        </Panel>
        <MarketsTable />
      </div>
    </>
  );
}
