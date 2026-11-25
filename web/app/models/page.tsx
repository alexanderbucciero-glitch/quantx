"use client";
import { PageHead, StageNotice } from "@/components/ui";

export default function Page() {
  return (
    <>
      <PageHead title="Model Performance" />
      <StageNotice stage={10} title="Model Performance"
        what={['Live Brier score, log-loss and hit rate per model and horizon', 'Reliability diagrams: predicted vs actual frequencies', 'Drift detection, and champion vs challenger models', 'Backtest expectations vs live results']}
        needs={['Models in production (Stage 3)', 'Forecasts that have reached their horizon, so they can be scored', 'At least several weeks of live forecasts before any accuracy claim']}
        related={[]} />
    </>
  );
}
