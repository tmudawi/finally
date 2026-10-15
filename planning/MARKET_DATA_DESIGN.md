# Market Data Backend — Detailed Design

Implementation guide for everything market-data in the FinAlly backend: the unified source interface, the shared price cache, the GBM simulator, the Massive poller, the factory, the SSE stream, and how the rest of the backend plugs in.

This document supersedes the code in `MARKET_INTERFACE.md` and `MARKET_SIMULATOR.md` where they differ (see §13). Background reading: `MASSIVE_API.md` (endpoints, plans, verified behavior) and `MARKET_SIMULATOR.md` §2 (the math).

**Status of the code below:** the full modules in §4-§7, the minimal `main.py` in §8.1, and the tests in §10 were built as a package and run on 2026-10-14 (Python 3.12, FastAPI 0.143.1, massive 2.8.0, pytest 9.1.1, pytest-asyncio 1.4.0). The 29 tests in §10 pass, and the SSE endpoint was smoke-tested against a live uvicorn server. The Massive source was tested only against a fake client, because no paid Massive key was available. The integration snippets in §8.2-§8.3 show how the rest of the backend uses the package. They depend on database code that does not exist yet, so they have not been run.

---

## 1. Requirements (from PLAN.md)

| # | Requirement | Where it is met |
|---|---|---|
| R1 | Simulator by default; Massive if `MASSIVE_API_KEY` is set and non-empty | `factory.py` |
| R2 | Both sources implement one interface; downstream code is source-agnostic | `base.py`, `cache.py` |
| R3 | Tracked tickers = watchlist ∪ open positions | `tracking` helpers, §8.2 |
| R4 | Cache holds price, previous price, timestamp, reference price per ticker | `models.py`, `cache.py` |
| R5 | Reference price: seed price (simulator), previous close (Massive) | `simulator.py`, `massive_source.py` |
| R6 | Simulator: GBM, ~500 ms ticks, market + sector + idiosyncratic shocks, 2-5% events, any 1-5 letter symbol | `simulator.py` |
| R7 | Massive: one multi-ticker snapshot call per poll; needs Starter plan; unknown ticker → HTTP 400 | `massive_source.py` |
| R8 | `GET /api/stream/prices`: one JSON map of all tracked tickers per event, `direction` + `change_pct` | `stream.py` |
| R9 | Trades execute at the cached price; reject if no cached price | §8.3 |

## 2. Architecture

```
                         FastAPI lifespan
                               │ create_market_source(cache)
               MASSIVE_API_KEY set? ── yes ──► MassiveSource ──┐  asyncio.to_thread(get_snapshot_all)
                               │                               │  every 5 s
                               no                              │
                               ▼                               │
                        SimulatorSource ───────────────────────┤  GBMEngine.step()
                         (GBMEngine)                           │  every 0.5 s
                                                               ▼
                                                         PriceCache  (single writer)
                                                               │ version counter
          ┌──────────────────────┬─────────────────────┬───────┴─────────────┐
          ▼                      ▼                     ▼                     ▼
  GET /api/stream/prices   POST /api/portfolio/trade  GET /api/watchlist   POST /api/chat
  (SSE, on change)         (price at fill)            GET /api/portfolio   (LLM context)
```

Design rules:

- **One writer, many readers.** Only the active source writes to `PriceCache`. Everything runs on one asyncio event loop, so no locks are needed. The only work off the loop is the blocking Massive HTTP call, and its result is written back on the loop.
- **The cache is the contract.** No route ever talks to the simulator or Massive directly, except to add or remove tracked tickers.
- **The source does not know about the database.** The service layer tells it which tickers to track (§8.2).
- **Prices in the cache are rounded to cents**; the simulator keeps full precision internally.

## 3. Package Layout and Dependencies

```
backend/
├── pyproject.toml
├── app/
│   ├── __init__.py
│   ├── main.py                  # FastAPI app + lifespan (market parts shown in §8.1)
│   └── market/
│       ├── __init__.py          # public API of the package
│       ├── models.py            # PriceUpdate
│       ├── cache.py             # PriceCache
│       ├── base.py              # MarketDataSource ABC, UnknownTickerError, normalize_symbol
│       ├── simulator.py         # TickerParams, DEFAULT_PARAMS, GBMEngine, SimulatorSource
│       ├── massive_source.py    # MassiveSource, snapshot_price, snapshot_time
│       ├── factory.py           # create_market_source
│       └── stream.py            # SSE router
└── tests/
    ├── __init__.py
    └── market/
        ├── __init__.py
        ├── fakes.py             # FakeMassiveClient, snap()
        ├── test_cache.py
        ├── test_simulator.py
        ├── test_massive.py
        ├── test_contract.py
        └── test_stream.py
```

```bash
cd backend
uv add fastapi "uvicorn[standard]" massive
uv add --dev pytest pytest-asyncio
```

`pyproject.toml` additions:

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
```

The simulator uses only the standard library. `massive` is imported unconditionally (by the factory), so it is a normal dependency even when the simulator runs.

---

## 4. Data Model — `app/market/models.py`

```python
"""Price data model shared by every market data source."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Direction = Literal["up", "down", "flat"]


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Latest known price for one ticker."""

    ticker: str
    price: float
    prev_price: float
    reference_price: float
    timestamp: datetime

    @property
    def direction(self) -> Direction:
        """'up', 'down' or 'flat' versus the previous price."""
        if self.price > self.prev_price:
            return "up"
        if self.price < self.prev_price:
            return "down"
        return "flat"

    @property
    def change_pct(self) -> float:
        """Percent change versus the reference price, rounded to 2 decimals."""
        return round((self.price / self.reference_price - 1) * 100, 2)

    def to_event(self) -> dict:
        """Payload for this ticker inside one SSE event."""
        return {
            "price": self.price,
            "prev_price": self.prev_price,
            "change_pct": self.change_pct,
            "timestamp": self.timestamp.isoformat(timespec="seconds").replace("+00:00", "Z"),
            "direction": self.direction,
        }
```

- `timestamp` is serialized to whole seconds with a `Z` suffix, matching the PLAN.md example `"2026-10-13T14:30:00Z"`.
- `direction` compares against the **previous cache write** for that ticker, not against the previous SSE event (see §7.3).

## 5. Price Cache — `app/market/cache.py`

```python
"""In-memory price cache: one writer (the market source), many readers."""

from datetime import UTC, datetime

from .models import PriceUpdate


class PriceCache:
    """Latest price per tracked ticker, plus a version counter that bumps on every change."""

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
        """Record a new price. prev_price and reference_price carry over from the last update."""
        price = round(price, 2)
        old = self._prices.get(ticker)
        if reference_price is None:
            reference_price = old.reference_price if old else price
        update = PriceUpdate(
            ticker=ticker,
            price=price,
            prev_price=old.price if old else price,
            reference_price=round(reference_price, 2),
            timestamp=timestamp or datetime.now(UTC),
        )
        self._prices[ticker] = update
        self.version += 1
        return update

    def get(self, ticker: str) -> PriceUpdate | None:
        return self._prices.get(ticker)

    def price(self, ticker: str) -> float | None:
        """Latest price, or None if the ticker is not cached."""
        update = self._prices.get(ticker)
        return update.price if update else None

    def remove(self, ticker: str) -> None:
        if self._prices.pop(ticker, None):
            self.version += 1

    def all(self) -> dict[str, PriceUpdate]:
        return dict(self._prices)
```

Behavior:

| Call | `price` | `prev_price` | `reference_price` |
|---|---|---|---|
| first `update("AAPL", 190.123)` | 190.12 | 190.12 | 190.12 (first seen) |
| `update("AAPL", 191.0)` | 191.00 | 190.12 | 190.12 (carried) |
| `update("AAPL", 110, reference_price=100)` | 110.00 | previous | 100.00 (explicit wins) |

`version` increments on every `update` and on every effective `remove`. The SSE stream compares versions to decide whether to send (§7).

## 6. The Interface — `app/market/base.py`

```python
"""The abstract market data source interface."""

import asyncio
import contextlib
import re
from abc import ABC, abstractmethod
from collections.abc import Iterable

from .cache import PriceCache

SYMBOL_RE = re.compile(r"[A-Z]{1,5}")


class UnknownTickerError(Exception):
    """The market source has no data for this ticker."""


def normalize_symbol(raw: str) -> str | None:
    """Uppercase and validate a symbol; None if it is not 1-5 letters."""
    symbol = raw.strip().upper()
    return symbol if SYMBOL_RE.fullmatch(symbol) else None


class MarketDataSource(ABC):
    """A background producer of prices for a set of tracked tickers."""

    def __init__(self, cache: PriceCache) -> None:
        self.cache = cache
        self._task: asyncio.Task | None = None

    @abstractmethod
    async def start(self, tickers: Iterable[str]) -> None:
        """Track the initial tickers and start the background task.

        Returns once the cache holds a price for every initial ticker.
        """

    async def stop(self) -> None:
        """Cancel the background task and wait for it to finish."""
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def _start_task(self) -> None:
        self._task = asyncio.create_task(self._run())

    @abstractmethod
    async def _run(self) -> None:
        """The background loop: produce prices into the cache forever."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Start tracking a ticker; no-op if already tracked.

        Returns once the cache holds a price for it.
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

The contract every source honors (enforced by `test_contract.py`, §10):

1. `start(tickers)` returns only after **every** initial ticker has a cached price.
2. `add_ticker(t)` returns only after `t` has a cached price, so a trade or watchlist response right after adding can use it. Adding an already tracked ticker does nothing (no cache write).
3. `add_ticker(t)` raises `UnknownTickerError` if the source has no data. The simulator never raises it.
4. `remove_ticker(t)` removes `t` from both the tracked set and the cache, and it does not come back on later ticks or polls.
5. `stop()` cancels the background task and waits for it. It is implemented once in the base class; subclasses provide `_run()` and call `_start_task()` at the end of `start()`.

Symbol validation is **not** the source's job: routes call `normalize_symbol()` first and return HTTP 400 on `None`.

```python
>>> normalize_symbol(" aapl ")
'AAPL'
>>> normalize_symbol("BRK.B") is None, normalize_symbol("TOOLONG") is None
(True, True)
```

### 6.1 Simulator — `app/market/simulator.py`

```python
"""Simulated market: correlated geometric Brownian motion with random jump events."""

import asyncio
import math
import random
from collections.abc import Iterable
from dataclasses import dataclass

from .base import MarketDataSource
from .cache import PriceCache

TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600
SECTORS = ("tech", "other")


@dataclass(frozen=True, slots=True)
class TickerParams:
    """Simulation parameters for one ticker."""

    seed_price: float
    volatility: float
    drift: float = 0.05
    sector: str = "other"


DEFAULT_PARAMS = {
    "AAPL": TickerParams(190.0, 0.25, sector="tech"),
    "GOOGL": TickerParams(175.0, 0.28, sector="tech"),
    "MSFT": TickerParams(420.0, 0.22, sector="tech"),
    "AMZN": TickerParams(185.0, 0.30, sector="tech"),
    "TSLA": TickerParams(250.0, 0.55, sector="tech"),
    "NVDA": TickerParams(130.0, 0.50, sector="tech"),
    "META": TickerParams(560.0, 0.35, sector="tech"),
    "NFLX": TickerParams(700.0, 0.35, sector="tech"),
    "JPM": TickerParams(210.0, 0.20),
    "V": TickerParams(280.0, 0.18),
}


class GBMEngine:
    """Pure, synchronous price engine. All randomness comes from one seeded RNG."""

    def __init__(
        self,
        tick_seconds: float = 0.5,
        time_scale: float = 60.0,
        event_prob: float = 0.0001,
        weights: tuple[float, float, float] = (0.3, 0.2, 0.5),
        seed: int | None = None,
    ) -> None:
        self.tick_seconds = tick_seconds
        self.dt = tick_seconds * time_scale / TRADING_SECONDS_PER_YEAR
        self.event_prob = event_prob
        self.w_market, self.w_sector, self.w_idio = (math.sqrt(w) for w in weights)
        self.rng = random.Random(seed)
        self.params: dict[str, TickerParams] = {}
        self.prices: dict[str, float] = {}

    def add(self, ticker: str) -> float:
        """Start simulating a ticker; returns its current price."""
        if ticker not in self.params:
            self.params[ticker] = DEFAULT_PARAMS.get(ticker) or TickerParams(
                round(self.rng.uniform(50, 300), 2), 0.30
            )
            self.prices[ticker] = self.params[ticker].seed_price
        return self.prices[ticker]

    def remove(self, ticker: str) -> None:
        self.params.pop(ticker, None)
        self.prices.pop(ticker, None)

    def step(self) -> dict[str, float]:
        """Advance every ticker by one tick and return the new prices."""
        market = self.rng.gauss()
        sector = {s: self.rng.gauss() for s in SECTORS}
        for ticker, p in self.params.items():
            z = (
                self.w_market * market
                + self.w_sector * sector[p.sector]
                + self.w_idio * self.rng.gauss()
            )
            drift = (p.drift - 0.5 * p.volatility**2) * self.dt
            price = self.prices[ticker] * math.exp(drift + p.volatility * math.sqrt(self.dt) * z)
            if self.rng.random() < self.event_prob:
                price *= 1 + self.rng.choice((-1, 1)) * self.rng.uniform(0.02, 0.05)
            self.prices[ticker] = price
        return dict(self.prices)


class SimulatorSource(MarketDataSource):
    """Market data source backed by GBMEngine."""

    def __init__(self, cache: PriceCache, engine: GBMEngine | None = None) -> None:
        super().__init__(cache)
        self.engine = engine or GBMEngine()

    @property
    def tickers(self) -> set[str]:
        return set(self.engine.params)

    async def start(self, tickers: Iterable[str]) -> None:
        for ticker in tickers:
            await self.add_ticker(ticker)
        self._start_task()

    async def add_ticker(self, ticker: str) -> None:
        if ticker not in self.engine.params:
            self.cache.update(ticker, self.engine.add(ticker))

    async def remove_ticker(self, ticker: str) -> None:
        self.engine.remove(ticker)
        self.cache.remove(ticker)

    async def _run(self) -> None:
        while True:
            await asyncio.sleep(self.engine.tick_seconds)
            for ticker, price in self.engine.step().items():
                self.cache.update(ticker, price)
```

Key numbers (derivation and prototype validation in `MARKET_SIMULATOR.md` §2):

| Quantity | Value |
|---|---|
| `dt` per tick | 0.5 × 60 / 5,896,800 ≈ 5.1e-6 trading years |
| Per-tick sd, AAPL (σ 0.25) / TSLA (σ 0.55) | ≈ 0.056% / ≈ 0.124% |
| Correlation within a sector / across sectors | 0.5 / 0.3 |
| Jump events, 10 tickers, p = 0.0001 per tick | ≈ 7 per hour |

Notes:

- `GBMEngine` is pure and synchronous: it has no asyncio and no cache, and all its randomness comes from one seeded `random.Random`. That makes it fully deterministic in tests.
- `SimulatorSource.add_ticker` writes the seed price straight to the cache, which makes the seed the reference price, so every ticker starts at `change_pct == 0`.
- `step()` costs microseconds for 10-20 tickers, so it runs directly on the event loop.
- Prices reset on restart (accepted for this practice project).

### 6.2 Massive — `app/market/massive_source.py`

```python
"""Market data from the Massive (formerly Polygon.io) multi-ticker snapshot endpoint."""

import asyncio
import logging
from collections.abc import Iterable
from datetime import UTC, datetime

from massive import RESTClient
from massive.exceptions import BadResponse
from massive.rest.models import TickerSnapshot

from .base import MarketDataSource, UnknownTickerError
from .cache import PriceCache

log = logging.getLogger(__name__)


def snapshot_price(snap: TickerSnapshot) -> float | None:
    """Best available price: last trade, then minute bar, today's bar, previous close."""
    candidates = (
        snap.last_trade and snap.last_trade.price,
        snap.min and snap.min.close,
        snap.day and snap.day.close,
        snap.prev_day and snap.prev_day.close,
    )
    return next((c for c in candidates if c), None)


def snapshot_time(snap: TickerSnapshot) -> datetime | None:
    """Snapshot 'updated' is nanoseconds since epoch; 0 or missing means unknown."""
    return datetime.fromtimestamp(snap.updated / 1e9, UTC) if snap.updated else None


class MassiveSource(MarketDataSource):
    """Polls snapshots for all tracked tickers. Needs the Stocks Starter plan or above."""

    def __init__(self, cache: PriceCache, client: RESTClient, poll_seconds: float = 5.0) -> None:
        super().__init__(cache)
        self.client = client
        self.poll_seconds = poll_seconds
        self._tickers: set[str] = set()

    @property
    def tickers(self) -> set[str]:
        return set(self._tickers)

    async def start(self, tickers: Iterable[str]) -> None:
        self._tickers = set(tickers)
        try:
            await self._poll()
        except BadResponse as e:
            raise RuntimeError(
                f"Massive API rejected the snapshot request: {e}. Snapshots need the Stocks "
                "Starter plan or above; leave MASSIVE_API_KEY empty to use the simulator."
            ) from e
        self._start_task()

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
            await asyncio.sleep(self.poll_seconds)
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
        """The massive client is synchronous; run it off the event loop."""
        return await asyncio.to_thread(self.client.get_snapshot_all, "stocks", tickers=tickers)

    def _store(self, snap: TickerSnapshot) -> None:
        price = snapshot_price(snap)
        if price is None:
            return
        reference = snap.prev_day.close if snap.prev_day and snap.prev_day.close else None
        self.cache.update(snap.ticker, price, reference_price=reference, timestamp=snapshot_time(snap))
```

Notes:

- **The client is injected**, not built inside the class. The factory passes a real `RESTClient`, and tests pass a fake with the same `get_snapshot_all` method. No monkeypatching is needed.
- **The client is synchronous** (urllib3), so every call goes through `asyncio.to_thread`. Its built-in retries (3, with backoff, on 429/5xx) apply inside that thread.
- **Fail fast at startup.** The first poll in `start()` is not caught by the loop: a bad key or a free-tier key (403 on snapshots, verified 2026-10-14) becomes a `RuntimeError` with an actionable message, and the app does not start.
- **Survive at runtime.** Errors in later polls are logged and retried on the next interval.
- **Price fallback chain:** last trade → current minute bar close → today's bar close → previous close. A snapshot where all of them are missing or zero counts as "no data".
- **Reference price** = `prev_day.close`, so `change_pct` is the normal daily change.
- **Removal race:** a poll that was in flight when a ticker was removed skips that ticker, so it does not reappear in the cache.
- Starter/Developer data is 15-minute delayed and only moves during market hours. Outside them every poll rewrites the same prices (see §7.3).
- **Verify with a paid key before relying on it:** the snapshot endpoint is documented to omit unknown tickers (`MASSIVE_API.md` §4.1). The code also handles an entry that comes back with no price.

### 6.3 Factory — `app/market/factory.py`

```python
"""Pick the market data source from the environment."""

import os

from massive import RESTClient

from .base import MarketDataSource
from .cache import PriceCache
from .massive_source import MassiveSource
from .simulator import SimulatorSource


def create_market_source(cache: PriceCache) -> MarketDataSource:
    """Massive if MASSIVE_API_KEY is set and non-empty, otherwise the simulator."""
    api_key = os.getenv("MASSIVE_API_KEY", "").strip()
    if api_key:
        return MassiveSource(cache, RESTClient(api_key=api_key))
    return SimulatorSource(cache)
```

The key is passed to `RESTClient` explicitly, because the client's own default reads `MASSIVE_API_KEY` at import time, before `.env` may have been loaded.

### 6.4 Package API — `app/market/__init__.py`

```python
"""Market data: one price cache, one active source (simulator or Massive), one SSE stream."""

from .base import MarketDataSource, UnknownTickerError, normalize_symbol
from .cache import PriceCache
from .factory import create_market_source
from .models import PriceUpdate
from .stream import router as stream_router

__all__ = [
    "MarketDataSource",
    "PriceCache",
    "PriceUpdate",
    "UnknownTickerError",
    "create_market_source",
    "normalize_symbol",
    "stream_router",
]
```

Other backend modules import only from `app.market`, never from its submodules.

---

## 7. SSE Stream — `app/market/stream.py`

```python
"""GET /api/stream/prices: Server-Sent Events from the price cache."""

import asyncio
from collections.abc import AsyncIterable

from fastapi import APIRouter, Request
from fastapi.sse import EventSourceResponse, ServerSentEvent

from .cache import PriceCache

router = APIRouter()

CHECK_SECONDS = 0.5


async def price_events(cache: PriceCache, check_seconds: float = CHECK_SECONDS) -> AsyncIterable[dict]:
    """Yield the full price map whenever the cache version changes."""
    last_version = -1
    while True:
        if cache.version != last_version:
            last_version = cache.version
            yield {ticker: update.to_event() for ticker, update in cache.all().items()}
        await asyncio.sleep(check_seconds)


@router.get("/api/stream/prices", response_class=EventSourceResponse)
async def stream_prices(request: Request) -> AsyncIterable[ServerSentEvent]:
    yield ServerSentEvent(retry=1000)
    async for payload in price_events(request.app.state.prices):
        yield ServerSentEvent(data=payload)
```

### 7.1 Why FastAPI's native SSE

FastAPI 0.143 has `fastapi.sse.EventSourceResponse` / `ServerSentEvent` (no extra package needed):

- A path operation that `yield`s with `response_class=EventSourceResponse` is streamed as `text/event-stream`. FastAPI also sets `Cache-Control: no-cache` (observed in the smoke test).
- `ServerSentEvent(data=dict)` JSON-encodes the payload into a single `data:` line.
- `ServerSentEvent(retry=1000)` tells `EventSource` to reconnect 1 s after a drop.
- When the generator is idle (no change for 15 s, e.g. Massive outside market hours), FastAPI sends a `: ping` comment automatically, so proxies keep the connection open.
- When the client disconnects, the generator is cancelled. There is no `request.is_disconnected()` polling loop.

### 7.2 Wire format (captured from the smoke test)

```
retry: 1000

data: {"AAPL": {"price": 189.85, "prev_price": 189.79, "change_pct": -0.08, "timestamp": "2026-10-15T02:16:12Z", "direction": "up"}, "GOOGL": {...}, ...}

data: {...}
```

That is one `data:` line per event, containing every tracked ticker, so the client does one `JSON.parse` per event.

### 7.3 Send-on-change semantics

`price_events` checks `cache.version` every 0.5 s and sends the full map only when something was written since the last send:

- **Simulator:** writes every 0.5 s, so it sends one event per tick, as PLAN.md requires.
- **Massive:** writes every 5 s, so it sends one event per poll, and nothing (except pings) in between.
- `direction` is relative to the previous **cache write**. When a Massive poll returns an unchanged price, that ticker's direction is `"flat"`. When an `add_ticker` causes an extra event, the other tickers repeat their last direction. **Frontend rule (PLAN.md §6): flash only on `"up"`/`"down"`, and only when the price differs from the last price displayed for that ticker.** This makes the flash correct in every case above.
- The check and the tick both run every 0.5 s and are not synchronized, so one SSE event can occasionally cover two simulator ticks. `prev_price` then refers to the unseen intermediate price. This is invisible in practice and not worth synchronizing.

### 7.4 Frontend usage (for reference)

```ts
const es = new EventSource(`${process.env.NEXT_PUBLIC_API_BASE ?? ""}/api/stream/prices`);
es.onopen = () => setStatus("connected");
es.onerror = () => setStatus(es.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
es.onmessage = (e) => {
  const prices: Record<string, { price: number; prev_price: number; change_pct: number;
                                 timestamp: string; direction: "up" | "down" | "flat" }> = JSON.parse(e.data);
  applyPrices(prices);  // update watchlist rows, sparklines, main chart, live portfolio values
};
```

---

## 8. Integration With the Rest of the Backend

### 8.1 Lifespan — `app/main.py` (market parts)

Minimal runnable version (used for the smoke test):

```python
"""Minimal app wiring for the market data layer."""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.market import PriceCache, create_market_source, stream_router

DEFAULT_TICKERS = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]


@asynccontextmanager
async def lifespan(app: FastAPI):
    cache = PriceCache()
    market = create_market_source(cache)
    await market.start(DEFAULT_TICKERS)  # real app: watchlist tickers | position tickers from the DB
    app.state.prices = cache
    app.state.market = market
    yield
    await market.stop()


app = FastAPI(lifespan=lifespan)
app.include_router(stream_router)
```

In the real app, replace `DEFAULT_TICKERS` with the database query, after DB initialization:

```python
await market.start(db.watchlist_tickers() | db.position_tickers())
```

### 8.2 Keeping the tracked set in sync

Three events change the tracked set. The service layer handles each one; the market package needs no database access.

```python
from app.market import MarketDataSource, UnknownTickerError, normalize_symbol


async def untrack_if_unused(market: MarketDataSource, ticker: str) -> None:
    """Stop pricing a ticker that is neither watched nor held."""
    if ticker not in db.watchlist_tickers() and ticker not in db.position_tickers():
        await market.remove_ticker(ticker)
```

| Event | Order of operations |
|---|---|
| `POST /api/watchlist {ticker}` | `normalize_symbol` → 400 if `None` → `await market.add_ticker(t)` → 400 on `UnknownTickerError` → insert row (no-op if present) |
| `DELETE /api/watchlist/{ticker}` | delete row → `await untrack_if_unused(market, t)` |
| Sell that closes a position | delete position row → `await untrack_if_unused(market, t)` |

A buy never needs to start tracking: buys require a cached price (R9), and a cached price means the ticker is already tracked.

Example watchlist route:

```python
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

router = APIRouter()


class AddTicker(BaseModel):
    ticker: str


@router.post("/api/watchlist")
async def add_to_watchlist(body: AddTicker, request: Request) -> dict:
    ticker = normalize_symbol(body.ticker)
    if ticker is None:
        raise HTTPException(400, "Ticker must be 1-5 letters")
    try:
        await request.app.state.market.add_ticker(ticker)
    except UnknownTickerError:
        raise HTTPException(400, f"Unknown ticker {ticker}")
    db.add_watchlist(ticker)
    return {"ticker": ticker, **request.app.state.prices.get(ticker).to_event()}
```

The chat flow's watchlist actions call the same service function and turn the 400 cases into `{"status": "error", "error": ...}` action results instead of HTTP errors.

### 8.3 Reading prices

```python
prices: PriceCache = request.app.state.prices

# Trade execution (PLAN.md §8 validation)
price = prices.price(ticker)
if price is None:
    raise HTTPException(400, f"No price for {ticker}")

# GET /api/watchlist: tickers with latest prices
[{"ticker": t, **u.to_event()} for t in db.watchlist_tickers() if (u := prices.get(t))]

# Portfolio valuation and LLM context
total = cash + sum(p.quantity * prices.price(p.ticker) for p in positions)
```

Every position's ticker is tracked, so `prices.price(p.ticker)` is never `None` for a held ticker.

### 8.4 Portfolio snapshots

The 30-second `portfolio_snapshots` task (PLAN.md §7) is not part of the market package. It reads the cache the same way as the valuation code above.

---

## 9. Configuration

| Setting | Where | Default | Notes |
|---|---|---|---|
| `MASSIVE_API_KEY` | env / `.env` | empty | Non-empty selects Massive; needs Stocks Starter+ |
| Simulator tick | `GBMEngine(tick_seconds=)` | 0.5 s | |
| Simulator speed | `GBMEngine(time_scale=)` | 60 | 1 real second ≈ 1 trading minute |
| Jump probability | `GBMEngine(event_prob=)` | 0.0001 | per ticker per tick |
| Shock weights | `GBMEngine(weights=)` | (0.3, 0.2, 0.5) | must sum to 1 |
| Massive poll interval | `MassiveSource(poll_seconds=)` | 5 s | Paid plans have unlimited calls |
| SSE check interval | `stream.CHECK_SECONDS` | 0.5 s | |

No new environment variables beyond PLAN.md §5.

---

## 10. Tests — `backend/tests/market/`

Run with `uv run pytest`. Result on 2026-10-14: **29 passed in 1.2 s.**

### `fakes.py`

```python
from massive.exceptions import BadResponse
from massive.rest.models import Agg, LastTrade, TickerSnapshot


def snap(ticker: str, price: float | None, prev_close: float = 100.0) -> TickerSnapshot:
    return TickerSnapshot(
        ticker=ticker,
        last_trade=LastTrade(price=price) if price else None,
        prev_day=Agg(close=prev_close),
        updated=1_792_008_000_000_000_000,
    )


class FakeMassiveClient:
    """Stands in for massive.RESTClient.get_snapshot_all; knows a fixed set of prices."""

    def __init__(self, prices: dict[str, float], forbidden: bool = False) -> None:
        self.prices = prices
        self.forbidden = forbidden
        self.calls: list[list[str]] = []

    def get_snapshot_all(self, market_type: str, tickers: list[str]) -> list[TickerSnapshot]:
        self.calls.append(tickers)
        if self.forbidden:
            raise BadResponse('{"status":"NOT_AUTHORIZED"}')
        return [snap(t, self.prices[t]) for t in tickers if t in self.prices]
```

### `test_cache.py`

```python
from datetime import UTC, datetime

from app.market import PriceCache


def test_first_update_sets_prev_and_reference_to_price():
    cache = PriceCache()
    u = cache.update("AAPL", 190.123)
    assert (u.price, u.prev_price, u.reference_price) == (190.12, 190.12, 190.12)
    assert u.direction == "flat"
    assert u.change_pct == 0.0


def test_later_updates_carry_prev_and_reference():
    cache = PriceCache()
    cache.update("AAPL", 100.0)
    u = cache.update("AAPL", 101.5)
    assert u.prev_price == 100.0
    assert u.reference_price == 100.0
    assert u.direction == "up"
    assert u.change_pct == 1.5
    assert cache.update("AAPL", 99.0).direction == "down"


def test_explicit_reference_price_wins():
    cache = PriceCache()
    u = cache.update("AAPL", 110.0, reference_price=100.0)
    assert u.change_pct == 10.0


def test_version_bumps_on_update_and_remove():
    cache = PriceCache()
    cache.update("AAPL", 1.0)
    cache.remove("AAPL")
    cache.remove("AAPL")  # already gone: no bump
    assert cache.version == 2
    assert cache.price("AAPL") is None


def test_to_event_shape():
    cache = PriceCache()
    ts = datetime(2026, 10, 13, 14, 30, tzinfo=UTC)
    cache.update("AAPL", 190.05, timestamp=ts)
    event = cache.update("AAPL", 190.12, timestamp=ts).to_event()
    assert event == {
        "price": 190.12,
        "prev_price": 190.05,
        "change_pct": 0.04,
        "timestamp": "2026-10-13T14:30:00Z",
        "direction": "up",
    }
```

### `test_simulator.py`

```python
import asyncio
import math
import statistics

from app.market import PriceCache
from app.market.simulator import DEFAULT_PARAMS, GBMEngine, SimulatorSource


def log_returns(engine: GBMEngine, ticks: int) -> dict[str, list[float]]:
    path = [engine.step() for _ in range(ticks)]
    return {t: [math.log(b[t] / a[t]) for a, b in zip(path, path[1:])] for t in path[0]}


def test_add_uses_seed_price_and_is_idempotent():
    engine = GBMEngine(seed=1)
    assert engine.add("AAPL") == 190.0
    engine.step()
    assert engine.add("AAPL") == engine.prices["AAPL"]


def test_unknown_ticker_gets_random_seed_in_range():
    price = GBMEngine(seed=1).add("ZZZZ")
    assert 50 <= price <= 300


def test_remove_drops_ticker_from_steps():
    engine = GBMEngine(seed=1)
    engine.add("AAPL")
    engine.add("MSFT")
    engine.remove("AAPL")
    assert set(engine.step()) == {"MSFT"}


def test_prices_stay_positive():
    engine = GBMEngine(seed=1, event_prob=0.01)
    engine.add("TSLA")
    assert all(engine.step()["TSLA"] > 0 for _ in range(10_000))


def test_same_seed_same_path():
    a, b = GBMEngine(seed=7), GBMEngine(seed=7)
    for e in (a, b):
        e.add("AAPL")
    assert [a.step() for _ in range(50)] == [b.step() for _ in range(50)]


def test_volatility_and_correlation_match_design():
    engine = GBMEngine(seed=3, event_prob=0)
    for t in DEFAULT_PARAMS:
        engine.add(t)
    rets = log_returns(engine, 20_000)
    expected_sd = 0.25 * math.sqrt(engine.dt)
    assert abs(statistics.stdev(rets["AAPL"]) / expected_sd - 1) < 0.05
    assert abs(statistics.correlation(rets["AAPL"], rets["MSFT"]) - 0.5) < 0.05
    assert abs(statistics.correlation(rets["AAPL"], rets["JPM"]) - 0.3) < 0.05


def test_events_jump_at_least_two_percent():
    engine = GBMEngine(seed=3, event_prob=1)
    engine.add("AAPL")
    assert all(abs(r) > 0.015 for r in log_returns(engine, 200)["AAPL"])


async def test_source_seeds_cache_on_start_and_ticks():
    cache = PriceCache()
    source = SimulatorSource(cache, GBMEngine(tick_seconds=0.01, seed=1))
    await source.start(["AAPL", "PYPL"])
    assert cache.get("AAPL").price == 190.0
    assert cache.get("PYPL").change_pct == 0.0
    version = cache.version
    await asyncio.sleep(0.05)
    await source.stop()
    assert cache.version > version
    assert cache.get("AAPL").reference_price == 190.0


async def test_removed_ticker_does_not_reappear():
    cache = PriceCache()
    source = SimulatorSource(cache, GBMEngine(tick_seconds=0.01, seed=1))
    await source.start(["AAPL", "MSFT"])
    await source.remove_ticker("AAPL")
    await asyncio.sleep(0.03)
    await source.stop()
    assert cache.get("AAPL") is None
    assert source.tickers == {"MSFT"}
```

### `test_massive.py`

```python
import asyncio

import pytest

from app.market import PriceCache, UnknownTickerError
from app.market.massive_source import MassiveSource, snapshot_price
from massive.rest.models import Agg, MinuteSnapshot, TickerSnapshot

from .fakes import FakeMassiveClient, snap


def test_snapshot_price_fallback_chain():
    assert snapshot_price(snap("A", 10.0)) == 10.0
    assert snapshot_price(TickerSnapshot(min=MinuteSnapshot(close=9.0), day=Agg(close=8.0))) == 9.0
    assert snapshot_price(TickerSnapshot(day=Agg(close=8.0))) == 8.0
    assert snapshot_price(TickerSnapshot(prev_day=Agg(close=7.0))) == 7.0
    assert snapshot_price(TickerSnapshot()) is None


async def test_start_polls_once_and_uses_prev_close_as_reference():
    cache = PriceCache()
    client = FakeMassiveClient({"AAPL": 110.0})
    await MassiveSource(cache, client).start(["AAPL"])
    u = cache.get("AAPL")
    assert (u.price, u.reference_price, u.change_pct) == (110.0, 100.0, 10.0)
    assert u.timestamp.year == 2026


async def test_start_with_forbidden_plan_raises_clear_error():
    source = MassiveSource(PriceCache(), FakeMassiveClient({}, forbidden=True))
    with pytest.raises(RuntimeError, match="Starter plan"):
        await source.start(["AAPL"])


async def test_add_unknown_ticker_raises():
    source = MassiveSource(PriceCache(), FakeMassiveClient({"AAPL": 1.0}))
    await source.start([])
    with pytest.raises(UnknownTickerError):
        await source.add_ticker("ZZZZ")
    assert source.tickers == set()


async def test_poll_loop_refreshes_all_tracked_tickers():
    cache = PriceCache()
    client = FakeMassiveClient({"AAPL": 1.0, "MSFT": 2.0})
    source = MassiveSource(cache, client, poll_seconds=0.01)
    await source.start(["AAPL", "MSFT"])
    client.prices["AAPL"] = 1.5
    await asyncio.sleep(0.05)
    await source.stop()
    assert client.calls[-1] == ["AAPL", "MSFT"]
    assert cache.get("AAPL").price == 1.5
    assert cache.get("AAPL").prev_price in (1.0, 1.5)


async def test_poll_errors_are_logged_not_fatal(caplog):
    client = FakeMassiveClient({"AAPL": 1.0})
    source = MassiveSource(PriceCache(), client, poll_seconds=0.01)
    await source.start(["AAPL"])
    client.forbidden = True
    await asyncio.sleep(0.05)
    assert not source._task.done()
    await source.stop()
    assert "Massive poll failed" in caplog.text
```

### `test_contract.py` — the same contract for both sources

```python
"""Every MarketDataSource must honour the same contract."""

import pytest

from app.market import PriceCache
from app.market.massive_source import MassiveSource
from app.market.simulator import GBMEngine, SimulatorSource

from .fakes import FakeMassiveClient


def simulator(cache):
    return SimulatorSource(cache, GBMEngine(seed=1))


def massive(cache):
    return MassiveSource(cache, FakeMassiveClient({"AAPL": 190.0, "MSFT": 420.0, "PYPL": 70.0}))


@pytest.fixture(params=[simulator, massive], ids=["simulator", "massive"])
async def source(request):
    cache = PriceCache()
    src = request.param(cache)
    await src.start(["AAPL", "MSFT"])
    yield src
    await src.stop()


async def test_start_prices_initial_tickers(source):
    assert source.tickers == {"AAPL", "MSFT"}
    assert source.cache.price("AAPL") and source.cache.price("MSFT")


async def test_add_ticker_prices_it_immediately(source):
    await source.add_ticker("PYPL")
    assert "PYPL" in source.tickers
    assert source.cache.price("PYPL") is not None


async def test_add_existing_ticker_is_noop(source):
    version = source.cache.version
    await source.add_ticker("AAPL")
    assert source.cache.version == version


async def test_remove_ticker_drops_it(source):
    await source.remove_ticker("MSFT")
    assert source.tickers == {"AAPL"}
    assert source.cache.get("MSFT") is None
```

### `test_stream.py`

```python
import asyncio

from app.market import PriceCache
from app.market.stream import price_events


async def test_price_events_only_emit_on_change():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    events = price_events(cache, check_seconds=0.01)
    first = await anext(events)
    assert first["AAPL"]["price"] == 190.0
    pending = asyncio.ensure_future(anext(events))
    await asyncio.sleep(0.05)
    assert not pending.done()  # nothing changed, nothing sent
    cache.update("AAPL", 191.0)
    second = await asyncio.wait_for(pending, 1)
    assert second["AAPL"]["direction"] == "up"
```

**Do not test the SSE endpoint with `TestClient` or `httpx.ASGITransport`.** Both run the app until the response body completes, and an SSE stream never completes, so the test hangs (reproduced 2026-10-14). Test the `price_events` generator directly (above), and check the endpoint with a live server (§11) and the Playwright E2E tests.

---

## 11. Build Order

Each step is small and has its own check. Do not move on until the check passes.

1. **Models + cache.** `models.py`, `cache.py`, `test_cache.py` → `uv run pytest tests/market/test_cache.py`.
2. **Interface.** `base.py` → `uv run python -c "from app.market.base import normalize_symbol; print(normalize_symbol(' aapl '))"` prints `AAPL`.
3. **Simulator engine.** `GBMEngine` + engine tests → volatility/correlation test passes.
4. **Simulator source.** `SimulatorSource` + async tests.
5. **SSE.** `stream.py`, `factory.py` (simulator branch), `main.py` → `test_stream.py`, then the live smoke test:
   ```bash
   uv run uvicorn app.main:app --port 8000
   curl -N http://localhost:8000/api/stream/prices     # retry: 1000, then a data: line every ~0.5 s
   ```
6. **Contract test** with the simulator only.
7. **Massive source** + `fakes.py` + `test_massive.py`, then add the `massive` parameter to the contract test.
8. **Integration** (§8): lifespan reads tracked tickers from the DB; watchlist/trade/sell routes call `add_ticker` / `untrack_if_unused`.
9. **Optional, needs a Stocks Starter+ key:** run with `MASSIVE_API_KEY` set, and confirm real prices stream and that an unknown symbol is rejected with 400. With a free key, confirm the app refuses to start with the "Starter plan" message.

## 12. Known Limitations (accepted)

- Simulator prices reset on restart, so P&L and the P&L chart jump. This is acceptable for a practice project.
- Simulator accepts any 1-5 letter symbol (typos become tickers with random prices).
- Massive needs a paid plan, and its data is 15-minute delayed below the Advanced plan.
- Single process, single event loop: the cache is not shared across multiple uvicorn workers. Run one worker (the default).

## 13. Changes Versus the Earlier Design Docs

| Area | `MARKET_INTERFACE.md` / `MARKET_SIMULATOR.md` | This document | Why |
|---|---|---|---|
| SSE | Hand-rolled `StreamingResponse` + `is_disconnected()` loop | FastAPI-native `EventSourceResponse`; `retry: 1000`; automatic keep-alive pings | Latest FastAPI API; less code; idle connections survive proxies |
| `stop()` | Duplicated in each source | Implemented once in the base class; subclasses implement `_run()` | Removes duplication |
| Massive client | Built inside `MassiveSource` | Injected by the factory | Tests use a fake, no patching |
| 403 handling | `try/except` in the lifespan | Inside `MassiveSource.start()` | The source owns its error message |
| Symbol check | `is_valid_symbol()` | `normalize_symbol()` (strip + uppercase + validate) | PLAN.md says input is uppercased first |
| Timestamp format | Full ISO with microseconds | Whole seconds + `Z` | Matches the PLAN.md payload example |
| `PriceCache.update` | `reference_price or ...` (0 treated as missing) | Explicit `is None` check; reference rounded to cents | Correctness |
