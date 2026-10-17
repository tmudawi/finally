"use client";

import { useEffect, useRef } from "react";
import {
  AreaSeries,
  ColorType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from "lightweight-charts";
import type { Point } from "@/lib/types";

interface Props {
  points: Point[];
  color: string;
  compact?: boolean;
  testId?: string;
}

const toData = (p: Point) => ({ time: p.time as UTCTimestamp, value: p.value });

/** Area line chart; compact mode hides axes for sparklines. */
export default function LineChart({ points, color, compact = false, testId }: Props) {
  const container = useRef<HTMLDivElement>(null);
  const chart = useRef<IChartApi | null>(null);
  const series = useRef<ISeriesApi<"Area"> | null>(null);
  const drawn = useRef<Point[]>([]);

  useEffect(() => {
    const c = createChart(container.current!, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: "#7d8590",
        fontFamily: "inherit",
        attributionLogo: !compact,
      },
      grid: {
        vertLines: { visible: !compact, color: "#1b2430" },
        horzLines: { visible: !compact, color: "#1b2430" },
      },
      rightPriceScale: { visible: !compact, borderColor: "#243041" },
      timeScale: {
        visible: !compact,
        borderColor: "#243041",
        timeVisible: true,
        secondsVisible: true,
      },
      handleScroll: !compact,
      handleScale: !compact,
      crosshair: { vertLine: { visible: !compact }, horzLine: { visible: !compact } },
    });
    series.current = c.addSeries(AreaSeries, {
      lineWidth: compact ? 1 : 2,
      priceLineVisible: !compact,
      lastValueVisible: !compact,
      crosshairMarkerVisible: !compact,
    });
    chart.current = c;
    drawn.current = [];
    return () => c.remove();
  }, [compact]);

  useEffect(() => {
    series.current!.applyOptions({
      lineColor: color,
      topColor: `${color}55`,
      bottomColor: `${color}00`,
    });
  }, [color]);

  useEffect(() => {
    const s = series.current!;
    const prev = drawn.current;
    const extendsPrev = prev.length > 0 && points.length >= prev.length && points[0] === prev[0];
    if (extendsPrev) points.slice(prev.length - 1).forEach((p) => s.update(toData(p)));
    else s.setData(points.map(toData));
    if (compact) chart.current!.timeScale().fitContent();
    drawn.current = points;
  }, [points, compact]);

  return <div ref={container} data-testid={testId} className="h-full w-full" />;
}
