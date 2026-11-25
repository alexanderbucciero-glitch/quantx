"use client";
import { PageHead, StageNotice } from "@/components/ui";

export default function Page() {
  return (
    <>
      <PageHead title="Long / Short Signals" />
      <StageNotice stage={7} title="Long / Short Signals"
        what={['BUY/LONG, SELL/EXIT, SHORT, COVER, HOLD or NO TRADE with the evidence for each', 'Expected return after fees, slippage and borrow cost', 'Position size, stop level and exposure check from the risk manager', 'Every gate that passed or failed']}
        needs={['Validated forecasts (Stage 3)', 'A validated backtest engine and cost models (Stage 4)', 'Event-reaction statistics (Stage 7)', 'At least two independent evidence types agreeing']}
        related={[]} />
    </>
  );
}
