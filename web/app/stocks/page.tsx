"use client";
import { useEffect, useMemo, useState } from "react";
import { useApi } from "@/lib/api";
import { fmtCompact, fmtNum } from "@/lib/format";
import { CandleChart } from "@/components/Chart";
import { Empty, ErrorBox, Loading, PageHead, Panel, Pill, SourceTag, Stat } from "@/components/ui";

type Profile = { ticker: string; cik: string; name: string; industry: string; exchanges: string[]; fiscal_year_end: string; state: string; fetched_at: number;
  filings: { form: string; filed: string; items: string; description: string; url: string }[] };
type Fact = { end: string; value: number; fp: string; fy: number; form: string; filed: string; period: string | null };
type Fund = { metrics: Record<string, Fact[]>; fetched_at: number };
type Prices = { bars: { date: string; open: number; high: number; low: number; close: number; volume: number }[]; stats: Record<string, number | string | null>; fetched_at: number };

function Search({ onPick }: { onPick: (t: string) => void }) {
  const [q, setQ] = useState("");
  const [debounced, setDebounced] = useState("");
  useEffect(() => { const t = setTimeout(() => setDebounced(q.trim()), 250); return () => clearTimeout(t); }, [q]);
  const r = useApi<{ results: { ticker: string; name: string; cik: string }[] }>(debounced ? `/api/stocks/search?q=${encodeURIComponent(debounced)}` : null);
  return (
    <div className="relative w-full sm:w-80">
      <input id="ticker-search" value={q} onChange={e => setQ(e.target.value)} placeholder="Search ticker or company (SEC list)"
        onKeyDown={e => { if (e.key === "Enter" && q.trim()) { onPick(q.trim().toUpperCase()); setQ(""); } }}
        className="w-full h-8 bg-panel border border-line rounded-[3px] px-2.5 text-[13px] placeholder:text-dim" />
      {q && r.data && r.data.results.length > 0 && (
        <ul className="absolute z-10 mt-1 w-full bg-panel border border-line rounded-[3px] max-h-72 overflow-auto shadow-xl">
          {r.data.results.map(x => (
            <li key={x.cik + x.ticker}><button className="w-full text-left px-2.5 py-1.5 hover:bg-raised flex gap-2" onClick={() => { onPick(x.ticker); setQ(""); }}>
              <span className="num text-amber w-14">{x.ticker}</span><span className="truncate text-mute">{x.name}</span></button></li>
          ))}
        </ul>
      )}
    </div>
  );
}

function quarterly(rows: Fact[] | undefined) {
  return (rows || []).filter(r => r.period === "quarter").slice(-12).reverse();
}
function annual(rows: Fact[] | undefined) {
  return (rows || []).filter(r => r.period === "annual" || (r.period === null && r.fp === "FY")).slice(-10).reverse();
}

export default function Stocks() {
  const [t, setT] = useState("AAPL");
  const prof = useApi<Profile>(`/api/stocks/${t}/profile`);
  const fund = useApi<Fund>(`/api/stocks/${t}/fundamentals`);
  const px = useApi<Prices>(`/api/stocks/${t}/prices`);
  const bars = useMemo(() => (px.data?.bars || []).map(b => ({ time: b.date, open: b.open, high: b.high, low: b.low, close: b.close, volume: b.volume })), [px.data]);
  const m = fund.data?.metrics;
  const q = quarterly(m?.revenue);
  const a = annual(m?.revenue);
  const eps = (end: string, set: Fact[] | undefined) => set?.find(e => e.end === end)?.value;
  return (
    <>
      <PageHead title="Stock Intelligence" sub="Company data straight from SEC EDGAR (primary source). Fundamentals are shown with their filing date, so you see what was known when." right={<Search onPick={setT} />} />
      <div className="grid gap-3">
        <div className="border border-warn/40 bg-warn/5 rounded-[3px] px-3 py-2 text-xs text-mute">
          <span className="text-warn">Stock scanning by the AI agent is inactive.</span> It needs intraday prices, and the only free option found (Tiingo) requires a free API key in <span className="num">backend/.env</span>. Company filings, events and fundamentals below work without it.
        </div>
        <Panel title={prof.data ? <>{prof.data.name} <span className="num text-amber ml-1">{prof.data.ticker}</span></> : t} right={<SourceTag source="SEC EDGAR" at={prof.data?.fetched_at} />}>
          {prof.error ? <ErrorBox message={prof.error} onRetry={prof.reload} /> : !prof.data ? <Loading rows={2} /> : (
            <div className="p-3 grid grid-cols-2 md:grid-cols-5 gap-4">
              <Stat label="Industry (SIC)" value={<span className="font-sans text-[13px]">{prof.data.industry || "—"}</span>} />
              <Stat label="Exchange" value={(prof.data.exchanges || []).join(", ") || "—"} />
              <Stat label="CIK" value={prof.data.cik} />
              <Stat label="Fiscal year end" value={prof.data.fiscal_year_end ? `${prof.data.fiscal_year_end.slice(0, 2)}/${prof.data.fiscal_year_end.slice(2)}` : "—"} />
              <Stat label="Incorporated" value={prof.data.state || "—"} />
            </div>
          )}
        </Panel>

        <Panel title="Price history" right={<SourceTag source="Tiingo EOD (free key)" at={px.data?.fetched_at} />}>
          {px.error ? (
            <div className="p-4 text-xs text-mute leading-relaxed">
              <div className="text-warn mb-1">Price data not connected</div>
              {px.error}
              <div className="text-dim mt-1">Free, keyless stock-price sources either block automated access or forbid it in their terms, so QUANTX uses a free Tiingo key. Filings and fundamentals below work without it.</div>
            </div>
          ) : !px.data ? <Loading rows={6} /> : <CandleChart bars={bars} height={380} />}
        </Panel>

        <div className="grid gap-3 xl:grid-cols-2">
          <Panel title="Quarterly results (as filed)" right={<SourceTag source="XBRL company facts" at={fund.data?.fetched_at} />}>
            {fund.error ? <ErrorBox message={fund.error} onRetry={fund.reload} /> : !fund.data ? <Loading rows={6} /> : q.length === 0 ? <Empty>No quarterly XBRL revenue facts for this filer.</Empty> : (
              <div className="overflow-x-auto"><table className="grid-t">
                <thead><tr><th>Period end</th><th>FY/FP</th><th className="r">Revenue</th><th className="r">Net income</th><th className="r">Diluted EPS</th><th>Filed</th></tr></thead>
                <tbody>{q.map(r => (
                  <tr key={r.end + r.filed}><td className="num">{r.end}</td><td className="num text-mute">{r.fy} {r.fp}</td>
                    <td className="r num">{fmtCompact(r.value)}</td>
                    <td className="r num">{fmtCompact(quarterly(m?.net_income).find(x => x.end === r.end)?.value)}</td>
                    <td className="r num">{fmtNum(eps(r.end, quarterly(m?.eps_diluted)))}</td>
                    <td className="num text-mute">{r.filed}</td></tr>))}</tbody>
              </table></div>
            )}
          </Panel>
          <Panel title="Annual results (as filed)" right={<span className="text-2xs text-dim">Original values, not split-adjusted</span>}>
            {fund.error ? <ErrorBox message={fund.error} /> : !fund.data ? <Loading rows={6} /> : a.length === 0 ? <Empty>No annual XBRL revenue facts for this filer.</Empty> : (
              <div className="overflow-x-auto"><table className="grid-t">
                <thead><tr><th>FY end</th><th className="r">Revenue</th><th className="r">Op. income</th><th className="r">Net income</th><th className="r">Diluted EPS</th><th>Filed</th></tr></thead>
                <tbody>{a.map(r => (
                  <tr key={r.end + r.filed}><td className="num">{r.end}</td>
                    <td className="r num">{fmtCompact(r.value)}</td>
                    <td className="r num">{fmtCompact(annual(m?.operating_income).find(x => x.end === r.end)?.value)}</td>
                    <td className="r num">{fmtCompact(annual(m?.net_income).find(x => x.end === r.end)?.value)}</td>
                    <td className="r num">{fmtNum(eps(r.end, annual(m?.eps_diluted)))}</td>
                    <td className="num text-mute">{r.filed}</td></tr>))}</tbody>
              </table></div>
            )}
          </Panel>
        </div>

        <Panel title="Recent filings">
          {prof.error ? <ErrorBox message={prof.error} /> : !prof.data ? <Loading rows={6} /> : (
            <div className="overflow-x-auto max-h-[420px]"><table className="grid-t">
              <thead><tr><th>Filed</th><th>Form</th><th>8-K items</th><th>Document</th></tr></thead>
              <tbody>{prof.data.filings.map(f => (
                <tr key={f.url}><td className="num text-mute">{f.filed}</td><td><Pill kind={f.form.startsWith("8-K") ? "amber" : f.form.startsWith("10-") ? "info" : "neutral"}>{f.form}</Pill></td>
                  <td className="num text-2xs text-mute">{f.items || ""}</td>
                  <td><a href={f.url} target="_blank" rel="noreferrer" className="hover:text-amber">{f.description || f.form}</a></td></tr>))}</tbody>
            </table></div>
          )}
        </Panel>
      </div>
    </>
  );
}
