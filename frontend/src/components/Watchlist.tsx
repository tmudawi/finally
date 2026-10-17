"use client";

import { useState, type FormEvent } from "react";
import type { Point, PriceMap } from "@/lib/types";
import Panel from "./Panel";
import WatchlistRow from "./WatchlistRow";

interface Props {
  tickers: string[];
  prices: PriceMap;
  history: Record<string, Point[]>;
  selected: string;
  error: string;
  onSelect: (ticker: string) => void;
  onAdd: (ticker: string) => void;
  onRemove: (ticker: string) => void;
}

/** Watchlist panel with an add-ticker form. */
export default function Watchlist({ tickers, prices, history, selected, error, onSelect, onAdd, onRemove }: Props) {
  const [input, setInput] = useState("");

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (!input.trim()) return;
    onAdd(input.trim().toUpperCase());
    setInput("");
  };

  return (
    <Panel title="Watchlist" aside={<span className="text-xs text-muted">{tickers.length} symbols</span>}>
      <div className="flex h-full flex-col">
        <ul className="min-h-0 flex-1 overflow-y-auto">
          {tickers.map((t) => (
            <WatchlistRow
              key={t}
              ticker={t}
              tick={prices[t]}
              points={history[t] ?? []}
              selected={t === selected}
              onSelect={onSelect}
              onRemove={onRemove}
            />
          ))}
        </ul>
        <form onSubmit={submit} className="flex gap-1 border-t border-rule p-2">
          <input
            data-testid="watchlist-add-input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            placeholder="Add symbol"
            maxLength={5}
            className="field min-w-0 flex-1 uppercase placeholder:normal-case"
          />
          <button data-testid="watchlist-add-button" type="submit" className="btn-submit">
            Add
          </button>
        </form>
        {error && <p className="px-2 pb-2 text-xs text-down">{error}</p>}
      </div>
    </Panel>
  );
}
