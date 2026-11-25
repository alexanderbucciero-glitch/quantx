"use client";
import { useMemo, useState } from "react";
import { useApi } from "@/lib/api";
import { fmtNum } from "@/lib/format";
import { LineChart } from "@/components/Chart";
import { ErrorBox, Loading, PageHead, Panel, Pill, SourceTag, Stat } from "@/components/ui";

type Cat = { series: { id: string; name: string; group: string; units: string }[] };
type Series = { id: string; name: string; units: string; source_url: string; data: { date: string; value: number | null }[]; fetched_at: number; first_date: string; last_date: string; observations: number; missing: number };

export default function History() {
  const cat = useApi<Cat>("/api/markets/catalogue");
  const [id, setId] = useState("CPIAUCSL");
  const s = useApi<Series>(`/api/markets/series/${id}`);
  const pts = useMemo(() => (s.data?.data || []).filter(o => o.value != null).map(o => ({ time: o.date, value: o.value as number })), [s.data]);
  const years = s.data?.first_date && s.data?.last_date ? (Date.parse(s.data.last_date) - Date.parse(s.data.first_date)) / 3.156e10 : null;
  const groups = [...new Set((cat.data?.series || []).map(x => x.group))];
  return (
    <>
      <PageHead title="Historical Database" sub="Long-history series available free today. Coverage and missing observations are reported as they are; nothing is interpolated." />
      <div className="grid gap-3 lg:grid-cols-[260px_1fr]">
        <Panel title="Series catalogue" bodyClass="max-h-[640px] overflow-y-auto">
          {cat.error ? <ErrorBox message={cat.error} /> : !cat.data ? <Loading /> : groups.map(g => (
            <div key={g} className="py-1">
              <div className="label px-3 py-1 !text-dim">{g}</div>
              {cat.data!.series.filter(x => x.group === g).map(x => (
                <button key={x.id} onClick={() => setId(x.id)} className={`w-full text-left px-3 py-1 text-xs flex justify-between gap-2 hover:bg-raised ${id === x.id ? "text-amber bg-raised" : "text-mute"}`}>
                  <span className="truncate">{x.name}</span><span className="num text-2xs text-dim shrink-0">{x.id.slice(0, 10)}</span>
                </button>))}
            </div>))}
        </Panel>
        <div className="grid gap-3 content-start min-w-0">
          <Panel title={s.data?.name || id} right={s.data && <><a href={s.data.source_url} target="_blank" rel="noreferrer" className="text-amber hover:underline">FRED page</a><SourceTag source="FRED" at={s.data.fetched_at} /></>}>
            {s.error ? <ErrorBox message={s.error} onRetry={s.reload} /> : !s.data ? <Loading rows={8} /> : <LineChart data={pts} height={380} />}
          </Panel>
          {s.data && (
            <Panel title="Coverage" bodyClass="p-3 grid grid-cols-2 sm:grid-cols-5 gap-4">
              <Stat label="First observation" value={s.data.first_date} />
              <Stat label="Last observation" value={s.data.last_date} />
              <Stat label="Span" value={years ? `${years.toFixed(1)} yrs` : "—"} sub={years && years >= 50 ? <Pill kind="up">50-year target met</Pill> : <Pill>Under 50 yrs</Pill>} />
              <Stat label="Observations" value={fmtNum(s.data.observations, 0)} />
              <Stat label="Missing" value={fmtNum(s.data.missing, 0)} sub="reported as empty" />
            </Panel>
          )}
          <Panel title="Planned datasets">
            <div className="overflow-x-auto"><table className="grid-t">
              <thead><tr><th>Dataset</th><th>Free source</th><th>Status</th></tr></thead>
              <tbody>
                <tr><td>US macro, rates, FX (decades)</td><td>FRED / ALFRED vintages</td><td><Pill kind="up">Live (latest values)</Pill> <Pill kind="warn">Vintages: Stage 2</Pill></td></tr>
                <tr><td>US company fundamentals (as filed, 2009+)</td><td>SEC XBRL</td><td><Pill kind="up">Live</Pill></td></tr>
                <tr><td>US &amp; EU stock daily prices</td><td>Tiingo (free key)</td><td><Pill kind="warn">Needs key</Pill></td></tr>
                <tr><td>Delisted / bankrupt US stocks</td><td>Needs a source with delisting coverage (Tiingo's coverage to be checked)</td><td><Pill kind="down">Survivorship bias risk</Pill></td></tr>
                <tr><td>Crypto daily OHLCV</td><td>Coinbase / Kraken public</td><td><Pill kind="up">Live (300 bars per call)</Pill></td></tr>
                <tr><td>Stored database (Postgres/Timescale)</td><td>Schema in db/schema.sql</td><td><Pill kind="warn">Stage 2 ingestion</Pill></td></tr>
              </tbody>
            </table></div>
          </Panel>
        </div>
      </div>
    </>
  );
}
