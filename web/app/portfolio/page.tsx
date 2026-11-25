"use client";
import { PageHead, StageNotice } from "@/components/ui";

export default function Page() {
  return (
    <>
      <PageHead title="Paper Trading Portfolio" />
      <StageNotice stage={9} title="Paper Trading Portfolio"
        what={['Simulated positions, entries, exits and P&L against a benchmark', 'Append-only, hash-chained audit log of every decision', 'Forecasts, news and sentiment exactly as seen at decision time', 'What happened next, recorded automatically']}
        needs={['Signals engine (Stage 7) and a validated simulator (Stage 4)', 'Always-on background workers (needs QUANTX running on a computer or server)', 'Never connected to real money: no broker or wallet integration exists']}
        related={[]} />
    </>
  );
}
