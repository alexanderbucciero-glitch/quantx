"use client";
import { PageHead, Panel } from "@/components/ui";
import { MemeTable } from "@/components/widgets";

export default function Memecoins() {
  return (
    <>
      <PageHead title="Memecoin Scanner" sub="Newly profiled Solana tokens (including Pump.fun mints) from DexScreener's public API, with rule-based risk flags. No probabilities are shown: QUANTX has not built the historical comparable-launch database needed to estimate them." />
      <div className="grid gap-3">
        <MemeTable limit={60} />
        <Panel title="How the flags work">
          <div className="p-3 grid md:grid-cols-2 gap-x-8 gap-y-1.5 text-xs text-mute leading-relaxed">
            <p><span className="text-text">Low liquidity</span>: pool liquidity under $50k. Under $10k is critical.</p>
            <p><span className="text-text">Thin vs valuation</span>: market cap over 50× pool liquidity, so the price is easily moved.</p>
            <p><span className="text-text">Possible wash trading</span>: 1h volume over 5× liquidity.</p>
            <p><span className="text-text">One-sided flow</span>: more than 85% of 1h trades in one direction.</p>
            <p><span className="text-text">&lt; 24h old</span>: too new for any statistical estimate.</p>
            <p><span className="text-text">Holder concentration</span>: needs on-chain holder data (free Helius key, Stage 8). Not checked yet.</p>
          </div>
        </Panel>
      </div>
    </>
  );
}
