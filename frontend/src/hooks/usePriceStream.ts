"use client";

import { useEffect, useState } from "react";
import { API_BASE } from "@/lib/api";
import { appendPoint } from "@/lib/portfolio";
import type { ConnectionStatus, Point, PriceMap } from "@/lib/types";

export interface PriceStream {
  prices: PriceMap;
  history: Record<string, Point[]>;
  status: ConnectionStatus;
}

/** Merge a payload, keeping the old tick object when nothing changed (so it does not re-flash). */
export function mergePrices(current: PriceMap, incoming: PriceMap): PriceMap {
  const next = { ...current };
  for (const [ticker, tick] of Object.entries(incoming)) {
    const old = current[ticker];
    const same = old && old.price === tick.price && old.timestamp === tick.timestamp;
    next[ticker] = same ? old : tick;
  }
  return next;
}

/** Fold one SSE payload into the per-ticker price history. */
export function mergeHistory(history: Record<string, Point[]>, prices: PriceMap) {
  const next = { ...history };
  for (const [ticker, tick] of Object.entries(prices)) {
    const time = Math.floor(Date.parse(tick.timestamp) / 1000);
    next[ticker] = appendPoint(next[ticker] ?? [], { time, value: tick.price });
  }
  return next;
}

/** Subscribe to /api/stream/prices and accumulate prices since page load. */
export function usePriceStream(): PriceStream {
  const [stream, setStream] = useState<PriceStream>({
    prices: {},
    history: {},
    status: "reconnecting",
  });

  useEffect(() => {
    const source = new EventSource(`${API_BASE}/api/stream/prices`);
    const setStatus = (status: ConnectionStatus) => setStream((s) => ({ ...s, status }));

    source.onopen = () => setStatus("connected");
    source.onerror = () =>
      setStatus(source.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
    source.onmessage = (event) => {
      const prices: PriceMap = JSON.parse(event.data);
      setStream((s) => ({
        prices: mergePrices(s.prices, prices),
        history: mergeHistory(s.history, prices),
        status: "connected",
      }));
    };
    return () => source.close();
  }, []);

  return stream;
}
