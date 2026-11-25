"use client";
import { PageHead } from "@/components/ui";
import { NewsList, SecEvents } from "@/components/widgets";

export default function News() {
  return (
    <>
      <PageHead title="Breaking News Terminal" sub="Central-bank releases (primary sources), SEC filings (primary source) and global media headlines from GDELT (unverified). Duplicate headlines are merged." />
      <div className="grid gap-3 xl:grid-cols-[1.2fr_1fr]">
        <NewsList limit={120} />
        <SecEvents limit={60} />
      </div>
    </>
  );
}
