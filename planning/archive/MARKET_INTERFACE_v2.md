# Market Data Interface — Unified Python API

Design for the backend's market data layer: one abstract interface, two implementations (simulator and Massive), one shared in-memory price cache, and one SSE stream. All downstream code (SSE, trades, portfolio valuation, chat context) reads only from the cache and never knows which source is running.

See `MASSIVE_API.md` for the Massive endpoints and `MARKET_SIMULATOR.md` for the simulator internals.

## 1. Overview

```
            MASSIVE_API_KEY set?
                 │
     ┌───── yes ─┴─ no ─────┐
     ▼                      ▼
MassiveSource          SimulatorSource        (both implement MarketDataSource)
 polls snapshot          GBM step every
 every ~5 s              500 ms
     │                      │
     └──────► PriceCache ◄──┘                 (single writer, many readers)
                 │
     ┌───────────┼──────────────┬──────────────┐
     ▼           ▼              ▼              ▼
 SSE stream   trade exec    GET /api/portfolio  chat context
```

- Exactly one source runs, as a background asyncio task started in the FastAPI lifespan.
- The source writes; everything else reads. Single event loop → no locks needed.
- **Tracked tickers** = watchlist ∪ open positions (PLAN.md §6). The source prices only tracked tickers.

## 2. Module Layout

```
backend/app/market/
├── __init__.py          # re-exports PriceCache, PriceUpdate, MarketDataSource, create_market_source
├── models.py            # PriceUpdate dataclass
├── cache.py             # PriceCache
├── base.py              # MarketDataSource ABC, UnknownTickerError, is_valid_symbol
├── simulator.py         # SimulatorSource + GBM engine (MARKET_SIMULATOR.md)
├── massive_source.py    # MassiveSource
├── factory.py           # create_market_source()
└── stream.py            # GET /api/stream/prices SSE router
```

## 3. Data Model — `models.py`

```python
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Latest known price for one ticker."""

    ticker: str
    price: float
    prev_price: float
    reference_price: float
    timestamp: datetime

    @property
    def direction(self) -> str:
        """'up', 'down' or 'flat' versus the previous price."""
        if self.price > self.prev_price:
            return "up"
        if self.price < self.prev_price:
            return "down"
        return "flat"

    @property
    def change_pct(self) -> float:
        """Percent change versus the reference price."""
        return round((self.price / self.reference_price - 1) * 100, 2)

    def to_event(self) -> dict:
        """Payload for one ticker in the SSE event."""
        return {
            "price": self.price,
            "prev_price": self.prev_price,
            "change_pct": self.change_pct,
            "timestamp": self.timestamp.isoformat().replace("+00:00", "Z"),
            "direction": self.direction,
        }
```

`reference_price` is the seed / first-seen price for the simulator and the previous day's close for Massive.

## 4. Price Cache — `cache.py`

```python
from datetime import UTC, datetime

from .models import PriceUpdate


class PriceCache:
    """In-memory latest prices, written by one market source, read by everyone."""

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self.version = 0

    def update(
        self,
        ticker: str,
        price: float,
        reference_price: float | None = None,
        timestamp: datetime | None = None,
    ) -> PriceUpdate:
        """Record a new price; prev_price and reference carry over from the last update."""
        old = self._prices.get(ticker)
        update = PriceUpdate(
            ticker=ticker,
            price=round(price, 2),
            prev_price=old.price if old else round(price, 2),
            reference_price=reference_price or (old.reference_price if old else round(price, 2)),
            timestamp=timestamp or datetime.now(UTC),
        )
        self._prices[ticker] = update
        self.version += 1
        return update

    def get(self, ticker: str) -> PriceUpdate | None:
        return self._prices.get(ticker)

    def price(self, ticker: str) -> float | None:
        """Convenience for trade execution and valuation."""
        update = self._prices.get(ticker)
        return update.price if update else None

    def remove(self, ticker: str) -> None:
        if self._prices.pop(ticker, None):
            self.version += 1

    def all(self) -> dict[str, PriceUpdate]:
        return dict(self._prices)
```

`version` increments on every write so the SSE loop can skip sending when nothing changed (important for Massive, which updates every few seconds while SSE ticks every 500 ms).

## 5. The Interface — `base.py`

```python
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable

from .cache import PriceCache

SYMBOL_RE = re.compile(r"^[A-Z]{1,5}$")


class UnknownTickerError(Exception):
    """The market source has no data for this ticker."""


def is_valid_symbol(ticker: str) -> bool:
    return bool(SYMBOL_RE.fullmatch(ticker))


class MarketDataSource(ABC):
    """A background producer of prices for a set of tracked tickers."""

    def __init__(self, cache: PriceCache) -> None:
        self.cache = cache

    @abstractmethod
    async def start(self, tickers: Iterable[str]) -> None:
        """Begin tracking the initial tickers and start the background task.

        Returns after the cache holds a price for every initial ticker.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Start tracking a ticker. No-op if already tracked.

        Returns after the cache holds a price for it, so it can be traded immediately.
        Raises UnknownTickerError if the source has no data for it.
        """

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking a ticker and drop it from the cache."""

    @property
    @abstractmethod
    def tickers(self) -> set[str]:
        """Currently tracked tickers."""
```

Contract details:

- Symbols are uppercased and checked with `is_valid_symbol` by the API layer **before** calling the source (HTTP 400 on failure).
- `add_ticker` guarantees a cached price on return. The simulator always succeeds; Massive fetches a one-ticker snapshot and raises `UnknownTickerError` if it is empty.
- `remove_ticker` is called by the caller only when the ticker is neither watched nor held (see §8).

## 6. Massive Implementation — `massive_source.py`

```python
import asyncio
import contextlib
import logging
from collections.abc import Iterable
from datetime import UTC, datetime

from massive import RESTClient
from massive.rest.models import TickerSnapshot

from .base import MarketDataSource, UnknownTickerError
from .cache import PriceCache

log = logging.getLogger(__name__)


def snapshot_price(s: TickerSnapshot) -> float | None:
    """Best available current price: last trade, then minute bar, today's bar, previous close."""
    for value in (
        s.last_trade and s.last_trade.price,
        s.min and s.min.close,
        s.day and s.day.close,
        s.prev_day and s.prev_day.close,
    ):
        if value:
            return value
    return None


class MassiveSource(MarketDataSource):
    """Polls the Massive multi-ticker snapshot endpoint (Starter plan or above)."""

    def __init__(self, cache: PriceCache, api_key: str, poll_seconds: float = 5.0) -> None:
        super().__init__(cache)
        self._client = RESTClient(api_key=api_key)
        self._poll_seconds = poll_seconds
        self._tickers: set[str] = set()
        self._task: asyncio.Task | None = None

    @property
    def tickers(self) -> set[str]:
        return set(self._tickers)

    async def start(self, tickers: Iterable[str]) -> None:
        self._tickers = set(tickers)
        await self._poll()  # fail fast on a bad key or a plan without snapshot access
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    async def add_ticker(self, ticker: str) -> None:
        if ticker in self._tickers:
            return
        snaps = await self._fetch([ticker])
        if not snaps or snapshot_price(snaps[0]) is None:
            raise UnknownTickerError(ticker)
        self._store(snaps[0])
        self._tickers.add(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        self._tickers.discard(ticker)
        self.cache.remove(ticker)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self._poll_seconds)
            try:
                await self._poll()
            except Exception:
                log.exception("Massive poll failed; retrying next interval")

    async def _poll(self) -> None:
        if not self._tickers:
            return
        for snap in await self._fetch(sorted(self._tickers)):
            if snap.ticker in self._tickers:  # skip tickers removed while the request was in flight
                self._store(snap)

    async def _fetch(self, tickers: list[str]) -> list[TickerSnapshot]:
        return await asyncio.to_thread(self._client.get_snapshot_all, "stocks", tickers=tickers)

    def _store(self, snap: TickerSnapshot) -> None:
        price = snapshot_price(snap)
        if price is None:
            return
        reference = snap.prev_day.close if snap.prev_day else None
        timestamp = datetime.fromtimestamp(snap.updated / 1e9, UTC) if snap.updated else None
        self.cache.update(snap.ticker, price, reference_price=reference, timestamp=timestamp)
```

Notes:

- The `massive` client is synchronous, so every call goes through `asyncio.to_thread`.
- The poll loop logs and continues on transient errors (network, 429 after the client's own retries); the **first** poll in `start` is not caught, so a bad key or a free-tier key (403 on snapshots, verified) stops the app at startup with a clear error instead of silently streaming nothing.
- Data on Starter/Developer is 15-minute delayed; prices only change while the market is open. Outside market hours the cache simply holds the last price and the SSE stream sends nothing new.

### Free-tier keys

A free (Basic) key cannot call the snapshot endpoint. Rather than adding an end-of-day mode (static prices all day would make a poor demo), the startup error message tells the user to either upgrade or leave `MASSIVE_API_KEY` empty to use the simulator:

```python
from massive.exceptions import BadResponse

try:
    await source.start(tickers)
except BadResponse as e:
    raise RuntimeError(
        f"Massive API rejected the snapshot request ({e}). Snapshots need the Stocks "
        "Starter plan or above; leave MASSIVE_API_KEY empty to use the simulator."
    ) from e
```

## 7. Factory — `factory.py`

```python
import os

from .base import MarketDataSource
from .cache import PriceCache
from .massive_source import MassiveSource
from .simulator import SimulatorSource


def create_market_source(cache: PriceCache) -> MarketDataSource:
    """Massive if MASSIVE_API_KEY is set and non-empty, otherwise the simulator."""
    api_key = os.getenv("MASSIVE_API_KEY", "").strip()
    if api_key:
        return MassiveSource(cache, api_key)
    return SimulatorSource(cache)
```

## 8. Integration

### Lifespan

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.market import PriceCache, create_market_source


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    cache = PriceCache()
    source = create_market_source(cache)
    await source.start(db.watchlist_tickers() | db.position_tickers())
    app.state.prices = cache
    app.state.market = source
    yield
    await source.stop()
```

### Keeping tracked tickers in sync

The interface does not know about the database. The service layer calls it at the three points where the tracked set can change:

```python
async def track(market: MarketDataSource, ticker: str) -> None:
    """Called before adding to the watchlist or buying."""
    await market.add_ticker(ticker)  # raises UnknownTickerError -> HTTP 400


async def untrack_if_unused(market: MarketDataSource, ticker: str) -> None:
    """Called after removing from the watchlist or selling down to zero."""
    if ticker not in db.watchlist_tickers() and ticker not in db.position_tickers():
        await market.remove_ticker(ticker)
```

- `POST /api/watchlist` → validate symbol → `track` → insert row.
- `DELETE /api/watchlist/{ticker}` → delete row → `untrack_if_unused`.
- Sell that closes a position → delete position → `untrack_if_unused`.

Buys only happen on tickers with a cached price (validation rule in PLAN.md §8), which means they are already tracked.

### Trade execution

```python
price = request.app.state.prices.price(ticker)
if price is None:
    raise HTTPException(400, f"No price for {ticker}")
```

## 9. SSE Stream — `stream.py`

```python
import asyncio
import json

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()


@router.get("/api/stream/prices")
async def stream_prices(request: Request) -> StreamingResponse:
    cache = request.app.state.prices

    async def events():
        last_version = -1
        while not await request.is_disconnected():
            if cache.version != last_version:
                last_version = cache.version
                payload = {t: u.to_event() for t, u in cache.all().items()}
                yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(0.5)

    return StreamingResponse(
        events(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- One event per 500 ms tick containing all tracked tickers, matching the payload in PLAN.md §6.
- Events are only sent when the cache changed, so the frontend does not re-flash unchanged Massive prices.
- `EventSource` reconnects on its own; the first event after reconnecting contains the full current state.

## 10. Testing

- `PriceCache`: first update sets `prev_price == price` and reference = first price; later updates carry prev/reference; `direction` and `change_pct`; `version` increments; `remove`.
- `snapshot_price`: each fallback level, all-`None` returns `None`.
- `MassiveSource`: patch `RESTClient.get_snapshot_all` to return hand-built `TickerSnapshot` objects; assert cache contents, `UnknownTickerError` on an empty result, reference = `prev_day.close`.
- Interface conformance: one parametrized test runs the same add/remove/start contract against both `SimulatorSource` and a mocked `MassiveSource`.
- SSE: with `httpx.AsyncClient` + `ASGITransport`, read the first `data:` line and check its shape.
