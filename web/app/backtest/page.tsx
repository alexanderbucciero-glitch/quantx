"use client";
import { PageHead, StageNotice } from "@/components/ui";

export default function Page() {
  return (
    <>
      <PageHead title="Backtesting Laboratory" />
      <StageNotice stage={4} title="Backtesting Laboratory"
        what={['Choose asset, period, strategy, horizon, balance, fees, slippage and risk limits', 'Total and annualised return, max drawdown, Sharpe, Sortino, win rate, profit factor, exposure', 'Walk-forward and out-of-sample splits, with a breakdown by market regime', 'Comparison against a benchmark']}
        needs={['Stored historical bars with corporate actions (Stage 2)', 'Point-in-time data layer so no future information leaks into a test', 'Simulator validated on known strategies (buy and hold, SMA cross)', 'Delisted securities in the universe (needs a source with delisting coverage)']}
        related={[{href:"/history",label:"Historical data coverage"}]} />
    </>
  );
}
