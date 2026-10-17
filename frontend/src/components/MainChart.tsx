"use client";

import { formatPct } from "@/lib/portfolio";
import type { Point, PriceTick } from "@/lib/types";
import LineChart from "./LineChart";

interface Props {
  ticker: string;
  tick?: PriceTick;
  points: Point[];
}

/** Large chart of the selected ticker, accumulated since page load. */
export default function MainChart({ ticker, tick, points }: Props) {
  const change = tick?.change_pct ?? 0;
  return (
    <section data-testid="main-chart" className="flex min-h-0 min-w-0 flex-col border border-rule bg-panel">
      <header className="flex items-baseline gap-4 border-b border-rule px-3 py-1.5">
        <h2 className="text-2xl font-bold text-amber">{ticker || "No symbol"}</h2>
        {tick && (
          <>
            <span className="text-2xl font-semibold tabular-nums">{tick.price.toFixed(2)}</span>
            <span className={`tabular-nums ${change >= 0 ? "text-up" : "text-down"}`}>{formatPct(change)}</span>
          </>
        )}
        <span className="ml-auto text-xs text-muted">Since page load</span>
      </header>
      <div className="relative min-h-0 flex-1">
        <LineChart key={ticker} points={points} color="#209dd7" />
        {points.length < 2 && (
          <p className="pointer-events-none absolute inset-0 grid place-items-center text-sm text-muted">
            Collecting prices for {ticker}...
          </p>
        )}
      </div>
    </section>
  );
}
