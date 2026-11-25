"use client";
import Link from "next/link";
import { useApi } from "@/lib/api";
import { ago, fmtCompact, fmtNum, fmtPct, fmtPrice, tone } from "@/lib/format";
import { Empty, ErrorBox, Loading, Panel, Pill, SourceTag, Sparkline } from "./ui";

/* ----------------------------------------------------------------- types */
export type MarketRow = {
  id: string; name: string; group: string; units: string; ok: boolean; error?: string;
  last?: { date: string; value: number }; change?: number; change_pct?: number; change_1y_pct?: number;
  spark?: number[]; source_url?: string; fetched_at?: number;
};
export type Coin = {
  id: string; symbol: string; name: string; current_price: number; market_cap: number; market_cap_rank: number;
  total_volume: number; price_change_percentage_24h: number; change_1h: number | null; change_7d: number | null;
};
export type NewsItem = { id: string; title: string; url: string; source: string; published_at: string | null; primary_source: boolean; category: string };
export type Filing = { form: string; company: string; cik: string; updated: string; url: string; items: string[];
  events: { item: string; event_type: string; label: string }[]; importance: number; accession: string };
export type Token = {
  symbol: string; name: string; token_address: string; url: string; dex: string; price_usd: number | null;
  liquidity_usd: number | null; market_cap: number | null; fdv: number | null; age_hours: number | null; is_pump_fun: boolean;
  volume: Record<string, number>; txns: Record<string, { buys: number; sells: number }>; price_change: Record<string, number>;
  flags: { flag: string; severity: number; detail: string }[]; probability: null; probability_reason: string;
};

/* ----------------------------------------------------------------- markets */
export function MarketsTable({ groups, compact = false }: { groups?: string[]; compact?: boolean }) {
  const { data, error, loading, reload } = useApi<{ rows: MarketRow[] }>("/api/markets/overview", 15 * 60_000);
  const rows = (data?.rows || []).filter(r => !groups || groups.includes(r.group));
  return (
    <Panel title="Indices, rates, FX & commodities" right={<SourceTag source="FRED daily close" at={rows[0]?.fetched_at} />}>
      {error ? <ErrorBox message={error} onRetry={reload} /> : loading && !data ? <Loading rows={8} /> : (
        <div className="overflow-x-auto">
          <table className="grid-t">
            <thead><tr><th>Instrument</th><th className="r">Last</th><th className="r">Chg</th>{!compact && <th className="r">1Y</th>}<th>90 obs</th>{!compact && <th className="r">As of</th>}</tr></thead>
            <tbody>
              {rows.map(r => r.ok ? (
                <tr key={r.id}>
                  <td><a href={r.source_url} target="_blank" rel="noreferrer" className="hover:text-amber">{r.name}</a> <span className="num text-2xs text-dim">{r.id}</span></td>
                  <td className="r num">{fmtNum(r.last?.value, r.units === "%" || r.units === "pp" ? 2 : r.last && r.last.value < 10 ? 4 : 2)}{(r.units === "%" || r.units === "pp") && <span className="text-dim">{r.units === "%" ? "%" : "pp"}</span>}</td>
                  <td className={`r num ${tone(r.change)}`}>{r.units === "%" || r.units === "pp" ? `${(r.change ?? 0) >= 0 ? "+" : ""}${fmtNum((r.change ?? 0) * 100, 0)}bp` : fmtPct(r.change_pct)}</td>
                  {!compact && <td className={`r num ${tone(r.change_1y_pct)}`}>{r.units === "%" || r.units === "pp" ? "" : fmtPct(r.change_1y_pct, 1)}</td>}
                  <td><Sparkline values={r.spark || []} /></td>
                  {!compact && <td className="r num text-mute">{r.last?.date}</td>}
                </tr>
              ) : (
                <tr key={r.id}><td>{r.name}</td><td colSpan={compact ? 3 : 5} className="text-down text-2xs">{r.error}</td></tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/* ----------------------------------------------------------------- crypto */
export function CryptoTable({ limit = 50, title = "Crypto market (by market cap)" }: { limit?: number; title?: string }) {
  const { data, error, loading, reload } = useApi<{ coins: Coin[]; fetched_at: number }>(`/api/crypto/markets?per_page=${limit}`, 120_000);
  return (
    <Panel title={title} right={<SourceTag source="CoinGecko" at={data?.fetched_at} />}>
      {error ? <ErrorBox message={error} onRetry={reload} /> : loading && !data ? <Loading rows={8} /> : (
        <div className="overflow-x-auto">
          <table className="grid-t">
            <thead><tr><th>#</th><th>Asset</th><th className="r">Price</th><th className="r">1h</th><th className="r">24h</th><th className="r">7d</th><th className="r">Mkt cap</th><th className="r">Volume 24h</th></tr></thead>
            <tbody>{data!.coins.map(c => (
              <tr key={c.id}>
                <td className="num text-dim">{c.market_cap_rank}</td>
                <td><span className="text-text">{c.name}</span> <span className="num text-2xs text-mute uppercase">{c.symbol}</span></td>
                <td className="r num">{fmtPrice(c.current_price)}</td>
                <td className={`r num ${tone(c.change_1h)}`}>{fmtPct(c.change_1h, 2, false)}</td>
                <td className={`r num ${tone(c.price_change_percentage_24h)}`}>{fmtPct(c.price_change_percentage_24h, 2, false)}</td>
                <td className={`r num ${tone(c.change_7d)}`}>{fmtPct(c.change_7d, 2, false)}</td>
                <td className="r num">{fmtCompact(c.market_cap)}</td>
                <td className="r num text-mute">{fmtCompact(c.total_volume)}</td>
              </tr>))}
            </tbody>
          </table>
        </div>
      )}
    </Panel>
  );
}

/* ----------------------------------------------------------------- news */
export function NewsList({ limit = 60, title = "News wire" }: { limit?: number; title?: string }) {
  const { data, error, loading, reload } = useApi<{ items: NewsItem[]; sources: { name: string; ok: boolean; error?: string; count: number }[] }>("/api/news/feed", 5 * 60_000);
  return (
    <Panel title={title} right={data && <span className="flex gap-1.5">{data.sources.map(s =>
      <Pill key={s.name} kind={s.ok ? "neutral" : "down"}>{s.name}{s.ok ? ` ${s.count}` : " down"}</Pill>)}</span>}>
      {error ? <ErrorBox message={error} onRetry={reload} /> : loading && !data ? <Loading rows={8} /> :
        data!.items.length === 0 ? <Empty>No items returned by the sources right now.</Empty> : (
          <ul className="divide-y divide-line/60">
            {data!.items.slice(0, limit).map(n => (
              <li key={n.id} className="px-3 py-2 flex gap-3 hover:bg-raised">
                <span className="num text-2xs text-dim w-14 shrink-0 pt-0.5">{ago(n.published_at)}</span>
                <div className="min-w-0">
                  <a href={n.url} target="_blank" rel="noreferrer" className="text-[13px] text-text hover:text-amber leading-snug">{n.title}</a>
                  <div className="flex gap-2 items-center mt-0.5">
                    <span className="text-2xs text-mute">{n.source}</span>
                    {n.primary_source ? <Pill kind="up">Primary source</Pill> : <Pill>Unverified</Pill>}
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
    </Panel>
  );
}

/* ----------------------------------------------------------------- SEC events */
const EVENT_KIND: Record<string, "down" | "up" | "warn" | "info" | "neutral" | "amber"> = {
  bankruptcy: "down", restatement: "down", delisting_notice: "down", impairment: "down", debt_acceleration: "down",
  cybersecurity_incident: "down", earnings_release: "amber", acquisition_completed: "info", control_change: "info",
  material_agreement: "info", executive_change: "warn", auditor_change: "warn", restructuring: "warn",
};

export function SecEvents({ limit = 40, minImportance = 0, title = "SEC 8-K event stream" }: { limit?: number; minImportance?: number; title?: string }) {
  const { data, error, loading, reload } = useApi<{ filings: Filing[]; fetched_at: number }>("/api/events/sec?count=100", 60_000);
  const rows = (data?.filings || []).filter(f => f.importance >= minImportance).slice(0, limit);
  return (
    <Panel title={title} right={<SourceTag source="SEC EDGAR · primary source" at={data?.fetched_at} />}>
      {error ? <ErrorBox message={error} onRetry={reload} /> : loading && !data ? <Loading rows={8} /> :
        rows.length === 0 ? <Empty>No filings above the importance threshold in the latest batch.</Empty> : (
          <ul className="divide-y divide-line/60">
            {rows.map(f => (
              <li key={f.accession + f.cik} className="px-3 py-2 hover:bg-raised">
                <div className="flex items-start gap-3">
                  <span className="num text-2xs text-dim w-14 shrink-0 pt-0.5">{ago(f.updated)}</span>
                  <div className="min-w-0 flex-1">
                    <a href={f.url} target="_blank" rel="noreferrer" className="text-[13px] hover:text-amber">{f.company}</a>
                    <span className="num text-2xs text-dim ml-2">{f.form}</span>
                    <div className="flex flex-wrap gap-1.5 mt-1">
                      {f.events.filter(e => e.item !== "9.01").map(e => <Pill key={e.item} kind={EVENT_KIND[e.event_type] || "neutral"}>{e.item} {e.label}</Pill>)}
                    </div>
                  </div>
                  <span className="num text-2xs text-mute shrink-0" title="Rule-based prior importance from the 8-K item type">{f.importance.toFixed(1)}</span>
                </div>
              </li>
            ))}
          </ul>
        )}
    </Panel>
  );
}

/* ----------------------------------------------------------------- memecoins */
const FLAG_LABEL: Record<string, string> = {
  low_liquidity: "Low liquidity", very_new: "< 24h old", unreliable_data: "Unreliable data", unknown_age: "Unknown age",
  thin_liquidity_vs_valuation: "Thin vs valuation", possible_wash_trading: "Possible wash trading",
  one_sided_flow: "One-sided flow", holder_data_unavailable: "Holders n/a",
};

export function MemeTable({ limit = 30, title = "New Solana tokens" }: { limit?: number; title?: string }) {
  const { data, error, loading, reload } = useApi<{ tokens: Token[]; fetched_at: number; without_pair_data: string[] }>(`/api/memecoins/latest?limit=${limit}`, 90_000);
  return (
    <Panel title={title} right={<SourceTag source="DexScreener" at={data?.fetched_at} />}>
      {error ? <ErrorBox message={error} onRetry={reload} /> : loading && !data ? <Loading rows={8} /> :
        data!.tokens.length === 0 ? <Empty>No new Solana token profiles returned right now.</Empty> : (
          <div className="overflow-x-auto">
            <table className="grid-t">
              <thead><tr><th>Token</th><th className="r">Age</th><th className="r">Price</th><th className="r">Liquidity</th><th className="r">Mkt cap</th><th className="r">Vol 1h</th><th className="r">Buys/Sells 1h</th><th className="r">Δ 1h</th><th>Risk flags</th><th>Probability</th></tr></thead>
              <tbody>{data!.tokens.map(t => {
                const tx = t.txns?.h1;
                return (
                  <tr key={t.token_address}>
                    <td><a href={t.url} target="_blank" rel="noreferrer" className="hover:text-amber">{t.symbol}</a>
                      {t.is_pump_fun && <span className="ml-1.5"><Pill kind="amber">pump</Pill></span>}
                      <div className="num text-2xs text-dim">{t.token_address.slice(0, 4)}…{t.token_address.slice(-4)}</div></td>
                    <td className="r num">{t.age_hours == null ? "—" : t.age_hours < 1 ? `${Math.round(t.age_hours * 60)}m` : `${t.age_hours.toFixed(1)}h`}</td>
                    <td className="r num">{fmtPrice(t.price_usd)}</td>
                    <td className={`r num ${t.liquidity_usd == null || t.liquidity_usd < 50000 ? "text-warn" : ""}`}>{fmtCompact(t.liquidity_usd)}</td>
                    <td className="r num">{fmtCompact(t.market_cap ?? t.fdv)}</td>
                    <td className="r num text-mute">{fmtCompact(t.volume?.h1)}</td>
                    <td className="r num text-mute">{tx ? `${tx.buys}/${tx.sells}` : "—"}</td>
                    <td className={`r num ${tone(t.price_change?.h1)}`}>{fmtPct(t.price_change?.h1, 1, false)}</td>
                    <td><div className="flex flex-wrap gap-1 max-w-[340px] whitespace-normal">{t.flags.filter(f => f.flag !== "holder_data_unavailable").map(f =>
                      <span key={f.flag} title={f.detail}><Pill kind={f.severity >= 3 ? "down" : f.severity === 2 ? "warn" : "neutral"}>{FLAG_LABEL[f.flag] || f.flag}</Pill></span>)}</div></td>
                    <td><span title={t.probability_reason} className="text-2xs text-dim">Insufficient samples</span></td>
                  </tr>);
              })}</tbody>
            </table>
          </div>
        )}
    </Panel>
  );
}

export function StageStrip() {
  const { data } = useApi<{ stages: { stage: number; name: string; status: string }[] }>("/api/stages");
  if (!data) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {data.stages.map(s => (
        <span key={s.stage} title={s.name}>
          <Pill kind={s.status === "in_progress" ? "amber" : s.status === "partial" ? "info" : "neutral"}>
            S{s.stage} {s.status === "not_started" ? "·" : s.status === "partial" ? "partial" : "building"}
          </Pill>
        </span>
      ))}
    </div>
  );
}

export const linkCls = "text-amber hover:underline";
export { Link };
