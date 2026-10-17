"use client";

import { useCallback, useEffect, useState } from "react";
import * as api from "@/lib/api";
import { livePortfolio, toPoints } from "@/lib/portfolio";
import type { ChatMessage, Point, Portfolio, Snapshot } from "@/lib/types";
import { usePriceStream } from "@/hooks/usePriceStream";
import ChatPanel from "./ChatPanel";
import Header from "./Header";
import Heatmap from "./Heatmap";
import MainChart from "./MainChart";
import PnlChart from "./PnlChart";
import PositionsTable from "./PositionsTable";
import TradeBar from "./TradeBar";
import Watchlist from "./Watchlist";

const EMPTY: Portfolio = { cash: 0, total_value: 0, unrealized_pnl: 0, positions: [] };
const HISTORY_POLL_MS = 30_000;

const snapshotPoints = (snapshots: Snapshot[]) =>
  toPoints(snapshots.map((s) => ({ time: s.recorded_at, value: s.total_value })));

const message = (e: unknown) => (e instanceof Error ? e.message : String(e));

/** The trading workstation: owns server state and wires the panels together. */
export default function Terminal() {
  const { prices, history, status } = usePriceStream();
  const [portfolio, setPortfolio] = useState<Portfolio>(EMPTY);
  const [valueHistory, setValueHistory] = useState<Point[]>([]);
  const [tickers, setTickers] = useState<string[]>([]);
  const [selected, setSelected] = useState("");
  const [tradeError, setTradeError] = useState("");
  const [watchError, setWatchError] = useState("");
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [chatLoading, setChatLoading] = useState(false);

  const refreshPortfolio = useCallback(async () => {
    const [p, h] = await Promise.all([api.getPortfolio(), api.getHistory()]);
    setPortfolio(p);
    setValueHistory(snapshotPoints(h));
  }, []);

  const refreshWatchlist = useCallback(async () => {
    const list = await api.getWatchlist();
    setTickers(list.map((w) => w.ticker));
    setSelected((s) => s || list[0]?.ticker || "");
  }, []);

  useEffect(() => {
    refreshPortfolio();
    refreshWatchlist();
    const timer = setInterval(
      () => api.getHistory().then((h) => setValueHistory(snapshotPoints(h))),
      HISTORY_POLL_MS,
    );
    return () => clearInterval(timer);
  }, [refreshPortfolio, refreshWatchlist]);

  const onTrade = async (ticker: string, quantity: number, side: "buy" | "sell") => {
    setTradeError("");
    try {
      await api.trade(ticker, quantity, side);
      await refreshPortfolio();
    } catch (e) {
      setTradeError(message(e));
    }
  };

  const changeWatchlist = async (call: Promise<{ ticker: string }[]>) => {
    setWatchError("");
    try {
      setTickers((await call).map((w) => w.ticker));
    } catch (e) {
      setWatchError(message(e));
    }
  };

  const onSend = async (text: string) => {
    setMessages((m) => [...m, { role: "user", content: text }]);
    setChatLoading(true);
    try {
      const reply = await api.sendChat(text);
      setMessages((m) => [...m, { role: "assistant", content: reply.message, actions: reply.actions }]);
      if (reply.actions.length) await Promise.all([refreshPortfolio(), refreshWatchlist()]);
    } catch (e) {
      setMessages((m) => [...m, { role: "assistant", content: message(e), error: true }]);
    } finally {
      setChatLoading(false);
    }
  };

  const live = livePortfolio(portfolio, prices);

  return (
    <div className="flex h-screen flex-col">
      <Header totalValue={live.totalValue} cash={live.cash} status={status} />
      <div className="flex min-h-0 flex-1">
        <main className="grid min-h-0 min-w-0 flex-1 grid-cols-[300px_1fr] grid-rows-[minmax(0,1fr)_auto] gap-1 p-1 max-lg:flex max-lg:flex-col max-lg:overflow-y-auto">
          <div className="row-span-2 min-h-0 max-lg:h-80 max-lg:shrink-0">
            <Watchlist
              tickers={tickers}
              prices={prices}
              history={history}
              selected={selected}
              error={watchError}
              onSelect={setSelected}
              onAdd={(t) => changeWatchlist(api.addWatchlist(t))}
              onRemove={(t) => changeWatchlist(api.removeWatchlist(t))}
            />
          </div>
          <div className="grid min-h-0 min-w-0 grid-rows-[minmax(220px,3fr)_minmax(160px,2fr)_minmax(140px,2fr)] gap-1 max-lg:shrink-0 max-lg:grid-rows-[320px_220px_220px]">
            <MainChart ticker={selected} tick={prices[selected]} points={history[selected] ?? []} />
            <div className="grid min-h-0 grid-cols-2 gap-1">
              <Heatmap positions={live.positions} />
              <PnlChart points={valueHistory} />
            </div>
            <PositionsTable positions={live.positions} />
          </div>
          <TradeBar ticker={selected} error={tradeError} onTrade={onTrade} />
        </main>
        <ChatPanel messages={messages} loading={chatLoading} onSend={onSend} />
      </div>
    </div>
  );
}
