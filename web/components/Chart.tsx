"use client";
import { useEffect, useRef } from "react";
import {
  CandlestickSeries, ColorType, createChart, createSeriesMarkers, HistogramSeries, LineSeries, LineStyle,
  type IChartApi, type SeriesMarker, type Time,
} from "lightweight-charts";

export type ChartMarker = { time: number | string; position: "aboveBar" | "belowBar" | "inBar"; color: string; shape: "arrowUp" | "arrowDown" | "circle" | "square"; text?: string };
export type PriceLine = { price: number; color: string; title: string; style?: "solid" | "dashed" | "dotted"; width?: number };

type Bar = { time: number | string; open: number; high: number; low: number; close: number; volume?: number | null };
type Pt = { time: number | string; value: number };

const css = (name: string) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function baseChart(el: HTMLDivElement, height: number): IChartApi {
  return createChart(el, {
    height,
    autoSize: true,
    layout: { background: { type: ColorType.Solid, color: css("--panel") }, textColor: css("--mute"),
      fontFamily: "'IBM Plex Mono', monospace", fontSize: 11, attributionLogo: true },
    grid: { vertLines: { color: css("--line") + "55" }, horzLines: { color: css("--line") + "55" } },
    rightPriceScale: { borderColor: css("--line") },
    timeScale: { borderColor: css("--line"), timeVisible: true },
    crosshair: { mode: 0 },
  });
}

/** TradingView Lightweight Charts (Apache-2.0). Candles + volume + optional overlay lines. */
export function CandleChart({ bars, overlays = [], height = 380, markers = [], lines = [] }:
  { bars: Bar[]; overlays?: { name: string; color: string; data: (number | null)[] }[]; height?: number;
    markers?: ChartMarker[]; lines?: PriceLine[] }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || bars.length === 0) return;
    const chart = baseChart(ref.current, height);
    const c = chart.addSeries(CandlestickSeries, {
      upColor: css("--up"), downColor: css("--down"), borderVisible: false,
      wickUpColor: css("--up"), wickDownColor: css("--down"),
    });
    c.setData(bars.map(b => ({ time: b.time as Time, open: b.open, high: b.high, low: b.low, close: b.close })));
    if (bars.some(b => b.volume != null)) {
      const v = chart.addSeries(HistogramSeries, { priceScaleId: "vol", priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false });
      chart.priceScale("vol").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
      v.setData(bars.map(b => ({ time: b.time as Time, value: b.volume ?? 0,
        color: (b.close >= b.open ? css("--up") : css("--down")) + "55" })));
    }
    for (const o of overlays) {
      const s = chart.addSeries(LineSeries, { color: o.color, lineWidth: 1, lastValueVisible: false, priceLineVisible: false, crosshairMarkerVisible: false });
      s.setData(bars.flatMap((b, i) => (o.data[i] == null ? [] : [{ time: b.time as Time, value: o.data[i] as number }])));
    }
    if (markers.length) {
      const sorted = [...markers].sort((a, b) => (a.time as number) - (b.time as number));
      createSeriesMarkers(c, sorted as SeriesMarker<Time>[]);
    }
    const st = { solid: LineStyle.Solid, dashed: LineStyle.Dashed, dotted: LineStyle.Dotted };
    for (const l of lines) {
      c.createPriceLine({ price: l.price, color: l.color, lineWidth: (l.width ?? 1) as 1, lineStyle: st[l.style ?? "dashed"], axisLabelVisible: true, title: l.title });
    }
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [bars, overlays, height, markers, lines]);
  return <div ref={ref} style={{ height }} className="w-full" />;
}

export function LineChart({ data, height = 300, color }: { data: Pt[]; height?: number; color?: string }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!ref.current || data.length === 0) return;
    const chart = baseChart(ref.current, height);
    chart.applyOptions({ timeScale: { timeVisible: typeof data[0]?.time === "number" } });
    const s = chart.addSeries(LineSeries, { color: color || css("--amber"), lineWidth: 2 });
    s.setData(data.map(p => ({ time: p.time as Time, value: p.value })));
    chart.timeScale().fitContent();
    return () => chart.remove();
  }, [data, height, color]);
  return <div ref={ref} style={{ height }} className="w-full" />;
}
