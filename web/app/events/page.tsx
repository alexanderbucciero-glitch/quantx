"use client";
import { useState } from "react";
import { PageHead, Panel, Pill } from "@/components/ui";
import { SecEvents } from "@/components/widgets";

export default function Events() {
  const [min, setMin] = useState(0.6);
  return (
    <>
      <PageHead title="Event-Driven Analysis" sub="Company events detected from SEC Form 8-K item codes. Every event links to the filing itself, so it is confirmed at the source." />
      <div className="grid gap-3 xl:grid-cols-[1.4fr_1fr]">
        <div className="grid gap-3 content-start">
          <div className="flex items-center gap-2 text-xs text-mute">
            <label htmlFor="imp">Minimum importance</label>
            <select id="imp" value={min} onChange={e => setMin(Number(e.target.value))} className="bg-panel border border-line rounded-[2px] h-6 px-1.5">
              {[0, 0.3, 0.6, 0.8, 0.9].map(v => <option key={v} value={v}>{v.toFixed(1)}</option>)}
            </select>
          </div>
          <SecEvents limit={80} minImportance={min} title="Detected events" />
        </div>
        <div className="grid gap-3 content-start">
          <Panel title="Event classification (current)">
            <div className="p-3 text-xs leading-relaxed text-mute space-y-2">
              <p><span className="text-text">Affected company:</span> the SEC filer (CIK).</p>
              <p><span className="text-text">Confirmed:</span> yes. The filing is the primary source.</p>
              <p><span className="text-text">Importance:</span> a fixed prior by 8-K item type, e.g. bankruptcy 1.0, restatement 0.95, earnings 0.9, executive change 0.6. It is not yet learned from price reactions.</p>
              <p><span className="text-text">Polarity, surprise, priced-in:</span> <Pill kind="warn">Stage 7</Pill> These need consensus estimates, intraday prices and an event-study database. They are not shown until then.</p>
            </div>
          </Panel>
          <Panel title="Next: event-study engine" right={<Pill kind="warn">Stage 7</Pill>}>
            <ul className="p-3 text-xs text-mute space-y-1 leading-relaxed">
              <li>› Store every 8-K with its acceptance timestamp (point-in-time).</li>
              <li>› Measure abnormal returns vs market and sector at 1d / 5d / 20d.</li>
              <li>› Build historical reaction distributions by event type, size and regime.</li>
              <li>› Earnings surprise = actual vs consensus. Needs an estimates source, which has no good free option.</li>
            </ul>
          </Panel>
        </div>
      </div>
    </>
  );
}
