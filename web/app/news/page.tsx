"use client";
import { useApi } from "@/lib/api";
import { ago } from "@/lib/format";
import { Empty, ErrorBox, Loading, PageHead, Panel, Pill } from "@/components/ui";
import { NewsList, SecEvents } from "@/components/widgets";

type Item = { id: string; title: string; url: string; source: string; published_at: string | null; primary_source: boolean; category: string; assets: string[]; relevance: string | null };

function Linked() {
  const d = useApi<{ items: Item[]; linked: number; note: string }>("/api/agent/news", 60_000);
  const rows = (d.data?.items ?? []).filter(i => i.relevance);
  return (
    <Panel title="Headlines linked to scanned assets" right={d.data && <span className="text-2xs text-dim">{d.data.linked} asset-linked · from the AI agent&apos;s news check</span>}>
      {d.error ? <ErrorBox message={d.error} /> : !d.data ? <Loading rows={4} /> : rows.length === 0 ? <Empty>No linked headlines yet. The agent checks news every 5 minutes while it is active.</Empty> : (
        <ul className="divide-y divide-line/60 max-h-[360px] overflow-y-auto">
          {rows.slice(0, 40).map(n => (
            <li key={n.id} className="px-3 py-2">
              <div className="flex gap-2 items-start">
                <span className="num text-2xs text-dim w-14 shrink-0 pt-0.5">{ago(n.published_at)}</span>
                <div className="min-w-0">
                  <a href={n.url} target="_blank" rel="noreferrer" className="text-[13px] hover:text-amber">{n.title}</a>
                  <div className="flex flex-wrap gap-1.5 mt-1 items-center">
                    {n.assets.map(a => <Pill key={a} kind="info">{a}</Pill>)}
                    {n.primary_source ? <Pill kind="up">Primary source</Pill> : <Pill>Unverified</Pill>}
                    <span className="text-2xs text-mute">{n.relevance}</span>
                  </div>
                </div>
              </div>
            </li>))}
        </ul>
      )}
    </Panel>
  );
}

export default function News() {
  return (
    <>
      <PageHead title="Breaking News Terminal" sub="Central-bank releases and SEC filings (primary sources) next to global media headlines from GDELT (unverified). Duplicates are merged, and headlines are linked to the assets the AI agent scans." />
      <div className="grid gap-3">
        <Linked />
        <div className="grid gap-3 xl:grid-cols-[1.2fr_1fr]">
          <NewsList limit={120} />
          <SecEvents limit={60} />
        </div>
      </div>
    </>
  );
}
