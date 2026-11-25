"use client";
import { PageHead, StageNotice } from "@/components/ui";

export default function Page() {
  return (
    <>
      <PageHead title="AI Predictions" />
      <StageNotice stage={3} title="AI Predictions"
        what={['P(up) and P(down), calibrated, per asset and horizon (1d / 1w / 1m; intraday for crypto)', 'Expected return and expected downside (5% quantile), with confidence intervals', 'Historical sample size and calibration error behind each number', 'Top factors driving each forecast']}
        needs={['Stored point-in-time price history (Stage 2)', 'Feature pipeline: momentum, volatility, volume, regime, macro', 'Walk-forward-trained logistic regression / random forest / XGBoost, compared against naive baselines', 'Probability calibration checked on out-of-sample data']}
        related={[{href:"/crypto",label:"Current descriptive indicators"}]} />
    </>
  );
}
