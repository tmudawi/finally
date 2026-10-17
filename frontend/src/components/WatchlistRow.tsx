"use client";

import { useFlash } from "@/hooks/useFlash";
import { formatPct } from "@/lib/portfolio";
import type { Point, PriceTick } from "@/lib/types";
import LineChart from "./LineChart";

interface Props {
  ticker: string;
  tick?: PriceTick;
  points: Point[];
  selected: boolean;
  onSelect: (ticker: string) => void;
  onRemove: (ticker: string) => void;
}

/** One watchlist line: symbol, flashing price, change % and sparkline. */
export default function WatchlistRow({ ticker, tick, points, selected, onSelect, onRemove }: Props) {
  const flash = useFlash(tick);
  const change = tick?.change_pct ?? 0;
  const tone = change >= 0 ? "text-up" : "text-down";

  return (
    <li
      data-testid={`watchlist-row-${ticker}`}
      onClick={() => onSelect(ticker)}
      className={`group grid h-10 cursor-pointer grid-cols-[56px_1fr_64px_72px_16px] items-center gap-1 border-b border-rule/60 pl-2 pr-1 hover:bg-white/[0.03] ${
        selected ? "bg-amber/10 shadow-[inset_2px_0_0_#ecad0a]" : ""
      }`}
    >
      <span className={`font-semibold ${selected ? "text-amber" : "text-ink"}`}>{ticker}</span>
      <span
        data-testid={`price-${ticker}`}
        className={`price-cell justify-self-end rounded-sm px-1 tabular-nums ${flash}`}
      >
        {tick ? tick.price.toFixed(2) : "--"}
      </span>
      <span className={`justify-self-end text-xs tabular-nums ${tone}`}>
        {tick ? formatPct(change) : ""}
      </span>
      <div className="h-7">
        <LineChart points={points} color={change >= 0 ? "#3fb950" : "#f85149"} compact />
      </div>
      <button
        data-testid={`watchlist-remove-${ticker}`}
        aria-label={`Remove ${ticker}`}
        onClick={(e) => {
          e.stopPropagation();
          onRemove(ticker);
        }}
        className="text-muted opacity-0 hover:text-down focus:opacity-100 group-hover:opacity-100"
      >
        ×
      </button>
    </li>
  );
}
