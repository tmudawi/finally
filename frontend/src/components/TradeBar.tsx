"use client";

import { useState } from "react";

interface Props {
  ticker: string;
  error: string;
  onTrade: (ticker: string, quantity: number, side: "buy" | "sell") => void;
}

/** Market order entry: symbol, quantity, buy and sell. */
export default function TradeBar({ ticker, error, onTrade }: Props) {
  const [symbol, setSymbol] = useState(ticker);
  const [selected, setSelected] = useState(ticker);
  const [quantity, setQuantity] = useState("1");

  if (ticker !== selected) {
    setSelected(ticker);
    setSymbol(ticker);
  }

  const submit = (side: "buy" | "sell") => onTrade(symbol.trim().toUpperCase(), Number(quantity), side);

  return (
    <section className="flex flex-wrap items-center gap-2 border border-rule bg-panel px-2 py-1.5">
      <h2 className="text-[13px] font-semibold text-amber">Trade</h2>
      <input
        data-testid="trade-ticker"
        aria-label="Symbol"
        value={symbol}
        onChange={(e) => setSymbol(e.target.value)}
        maxLength={5}
        className="field w-24 uppercase"
      />
      <input
        data-testid="trade-quantity"
        aria-label="Quantity"
        type="number"
        min="0"
        step="any"
        value={quantity}
        onChange={(e) => setQuantity(e.target.value)}
        className="field w-24"
      />
      <button data-testid="trade-buy" onClick={() => submit("buy")} className="btn-submit">
        Buy
      </button>
      <button data-testid="trade-sell" onClick={() => submit("sell")} className="btn-sell">
        Sell
      </button>
      <span className="text-xs text-muted">Market order, fills at the last price</span>
      {error && (
        <span data-testid="trade-error" className="text-xs text-down">
          {error}
        </span>
      )}
    </section>
  );
}
