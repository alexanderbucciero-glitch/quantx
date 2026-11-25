"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

export const NAV: { href: string; label: string; code: string; group: string; stage?: number }[] = [
  { href: "/", label: "Dashboard", code: "DASH", group: "Overview" },
  { href: "/markets", label: "Global Markets", code: "MKTS", group: "Overview" },
  { href: "/stocks", label: "Stock Intelligence", code: "EQTY", group: "Markets" },
  { href: "/crypto", label: "Crypto Intelligence", code: "CRYP", group: "Markets" },
  { href: "/memecoins", label: "Memecoin Scanner", code: "MEME", group: "Markets" },
  { href: "/social", label: "Social Intelligence", code: "SOCL", group: "Intelligence", stage: 5 },
  { href: "/news", label: "Breaking News", code: "NEWS", group: "Intelligence" },
  { href: "/events", label: "Event-Driven Analysis", code: "EVNT", group: "Intelligence" },
  { href: "/predictions", label: "AI Predictions", code: "PRED", group: "Research", stage: 3 },
  { href: "/signals", label: "Long / Short Signals", code: "SIGS", group: "Research", stage: 7 },
  { href: "/backtest", label: "Backtesting Lab", code: "BTST", group: "Research", stage: 4 },
  { href: "/portfolio", label: "Paper Portfolio", code: "PAPR", group: "Research", stage: 9 },
  { href: "/history", label: "Historical Database", code: "HIST", group: "System" },
  { href: "/models", label: "Model Performance", code: "MODL", group: "System", stage: 10 },
  { href: "/settings", label: "Integrations", code: "INTG", group: "System" },
];

function Clock() {
  const [now, setNow] = useState<Date | null>(null);
  useEffect(() => { setNow(new Date()); const t = setInterval(() => setNow(new Date()), 1000); return () => clearInterval(t); }, []);
  if (!now) return <span className="num text-mute">--:--:--</span>;
  const f = (tz: string) => now.toLocaleTimeString("en-GB", { timeZone: tz, hour: "2-digit", minute: "2-digit" });
  return (
    <span className="num text-2xs text-mute flex gap-3">
      <span>NY <span className="text-text">{f("America/New_York")}</span></span>
      <span className="hidden sm:inline">LDN <span className="text-text">{f("Europe/London")}</span></span>
      <span className="hidden md:inline">FRA <span className="text-text">{f("Europe/Berlin")}</span></span>
      <span>UTC <span className="text-text">{now.toISOString().slice(11, 19)}</span></span>
    </span>
  );
}

export default function Shell({ children }: { children: ReactNode }) {
  const path = usePathname();
  const [open, setOpen] = useState(false);
  useEffect(() => setOpen(false), [path]);
  const groups = [...new Set(NAV.map(n => n.group))];
  return (
    <div className="min-h-screen flex">
      <aside className={`fixed z-30 inset-y-0 left-0 w-[212px] bg-panel border-r border-line flex flex-col transition-transform lg:translate-x-0 ${open ? "translate-x-0" : "-translate-x-full"}`}>
        <Link href="/" className="h-11 flex items-center gap-2 px-4 border-b border-line">
          <span className="num font-semibold tracking-[0.2em] text-amber">QUANTX</span>
          <span className="text-2xs text-dim">research</span>
        </Link>
        <nav className="flex-1 overflow-y-auto py-2">
          {groups.map(g => (
            <div key={g} className="mb-2">
              <div className="label px-4 py-1 !text-dim">{g}</div>
              {NAV.filter(n => n.group === g).map(n => {
                const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
                return (
                  <Link key={n.href} href={n.href}
                    className={`flex items-center gap-2 h-7 px-4 text-[12.5px] border-l-2 ${active ? "border-amber bg-raised text-text" : "border-transparent text-mute hover:text-text hover:bg-raised/60"}`}>
                    <span className={`num text-2xs w-9 ${active ? "text-amber" : "text-dim"}`}>{n.code}</span>
                    <span className="truncate flex-1">{n.label}</span>
                    {n.stage && <span className="num text-2xs text-dim" title={`Stage ${n.stage}, not built yet`}>S{n.stage}</span>}
                  </Link>
                );
              })}
            </div>
          ))}
        </nav>
        <div className="p-3 border-t border-line text-2xs text-dim leading-snug">
          Research and paper simulation only. No brokerage or wallet connections.
        </div>
      </aside>
      {open && <button aria-label="Close menu" className="fixed inset-0 z-20 bg-black/50 lg:hidden" onClick={() => setOpen(false)} />}
      <div className="flex-1 min-w-0 lg:ml-[212px] flex flex-col">
        <header className="sticky top-0 z-10 h-11 bg-ink/95 backdrop-blur border-b border-line flex items-center gap-3 px-4">
          <button className="lg:hidden text-mute hover:text-text" onClick={() => setOpen(true)} aria-label="Open menu">☰</button>
          <span className="text-2xs text-dim hidden sm:inline">Free data sources · personal research</span>
          <div className="ml-auto"><Clock /></div>
        </header>
        <main className="flex-1 p-3 sm:p-4 min-w-0">{children}</main>
      </div>
    </div>
  );
}
