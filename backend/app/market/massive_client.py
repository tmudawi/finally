"""Massive (Polygon.io) API client for real market data."""

from __future__ import annotations

import asyncio
import logging
import time

from massive import RESTClient
from massive.rest.models import SnapshotMarketType, TickerSnapshot

from .cache import PriceCache
from .interface import MarketDataSource, UnknownTickerError, normalize_ticker

logger = logging.getLogger(__name__)

NS_PER_SECOND = 1_000_000_000
PROBE_TICKER = "SPY"  # Used to validate the API key when no tickers are tracked yet


def parse_snapshot(snap: TickerSnapshot) -> tuple[float, float, float | None] | None:
    """Extract (price, timestamp_seconds, prev_close) from a snapshot, or None if unpriced.

    Price fallback: last trade -> latest minute bar -> today's bar -> previous close.
    Massive trade and snapshot timestamps are Unix nanoseconds.
    """
    trade = snap.last_trade
    candidates = (
        trade.price if trade else None,
        snap.min.close if snap.min else None,
        snap.day.close if snap.day else None,
        snap.prev_day.close if snap.prev_day else None,
    )
    price = next((p for p in candidates if p), None)
    if price is None:
        return None
    ts_ns = (trade.sip_timestamp if trade else None) or snap.updated
    timestamp = ts_ns / NS_PER_SECOND if ts_ns else time.time()
    prev_close = snap.prev_day.close if snap.prev_day and snap.prev_day.close else None
    return float(price), timestamp, prev_close


class MassiveDataSource(MarketDataSource):
    """MarketDataSource backed by the Massive (Polygon.io) REST API.

    Polls GET /v2/snapshot/locale/us/markets/stocks/tickers for all watched
    tickers in a single API call, then writes results to the PriceCache.

    Requires the Stocks Starter plan or above (a free key gets HTTP 403 on the
    snapshot endpoint). Paid plans have unlimited calls -> poll every 5s (default).
    change_pct is relative to the previous day's close.
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 5.0,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._client: RESTClient | None = None

    async def start(self, tickers: list[str]) -> None:
        """Poll once (failing fast on a bad key, plan or empty data), then poll on interval."""
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = [normalize_ticker(t) for t in tickers]
        try:
            await self._first_poll()
        except Exception as e:
            self._client = None
            raise RuntimeError(
                f"Massive API first poll failed: {e}. Check MASSIVE_API_KEY and that the plan "
                "includes snapshot access (Stocks Starter or above), or leave it empty to "
                "use the simulator."
            ) from e

        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval",
            len(self._tickers),
            self._interval,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        """Track a ticker. Once started, fetch it now so it is priced immediately."""
        ticker = normalize_ticker(ticker)
        if ticker in self._tickers:
            return
        if self._client:
            snapshots = await asyncio.to_thread(self._fetch_snapshots, [ticker])
            if not self._store(snapshots, allowed={ticker}):
                raise UnknownTickerError(f"No market data for {ticker}")
        self._tickers.append(ticker)
        logger.info("Massive: added ticker %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        logger.info("Massive: removed ticker %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Internal ---

    async def _first_poll(self) -> None:
        """Validate the key and plan; raise if the tracked tickers got no prices."""
        snapshots = await asyncio.to_thread(self._fetch_snapshots, self._tickers or [PROBE_TICKER])
        if self._tickers and not self._store(snapshots, allowed=set(self._tickers)):
            raise RuntimeError("no prices returned for any tracked ticker")

    async def _poll_loop(self) -> None:
        """Poll on interval. First poll already happened in start()."""
        while True:
            await asyncio.sleep(self._interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        """One poll cycle. Errors are logged and retried on the next interval."""
        if not self._tickers or not self._client:
            return
        try:
            snapshots = await asyncio.to_thread(self._fetch_snapshots, list(self._tickers))
        except Exception as e:
            logger.error("Massive poll failed: %s", e)
            return
        # Tickers removed while the request was in flight must not be written back.
        processed = self._store(snapshots, allowed=set(self._tickers))
        logger.debug("Massive poll: updated %d/%d tickers", processed, len(self._tickers))

    def _store(self, snapshots: list[TickerSnapshot], allowed: set[str]) -> int:
        """Write priced snapshots for allowed tickers to the cache. Returns the count."""
        stored = 0
        for snap in snapshots:
            parsed = parse_snapshot(snap)
            if snap.ticker not in allowed or parsed is None:
                continue
            price, timestamp, prev_close = parsed
            self._cache.update(snap.ticker, price, timestamp, reference_price=prev_close)
            stored += 1
        return stored

    def _fetch_snapshots(self, tickers: list[str]) -> list[TickerSnapshot]:
        """Synchronous call to the Massive REST API. Runs in a thread."""
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=tickers,
        )
