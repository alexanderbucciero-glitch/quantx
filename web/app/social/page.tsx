"use client";
import { PageHead, StageNotice } from "@/components/ui";

export default function Page() {
  return (
    <>
      <PageHead title="Social Media Intelligence" />
      <StageNotice stage={5} title="Social Media Intelligence"
        what={['Mention volume per ticker/token vs its own historical baseline (z-scores)', 'Sentiment from a finance-tuned NLP model, weighted by source credibility', 'Bot-likeness and coordinated-campaign scores, so hype is not read as a buy signal', 'Sudden sentiment shifts linked to the news that caused them']}
        needs={['Reddit Data API access: free for non-commercial use, needs app approval', 'YouTube Data API key (free quota)', 'X: paid pay-per-use only, so it is skipped to keep QUANTX free', 'StockTwits: API closed to new developers', 'Labelled financial-sentiment benchmark to validate the model']}
        related={[{href:"/settings",label:"Integrations"}]} />
    </>
  );
}
