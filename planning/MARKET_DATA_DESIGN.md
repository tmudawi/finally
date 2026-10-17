# Market Data Backend — Detailed Design

**Scope:** everything under `backend/app/market/` — the unified data-source interface, the in-memory price cache, the GBM simulator, the Massive (Polygon.io) REST poller, the source factory, and the SSE endpoint — plus how the rest of the backend (lifespan, watchlist, trades) plugs into it.

**Relationship to existing docs and code.** A first implementation already exists in `backend/app/market/` (summarised in `MARKET_DATA_SUMMARY.md`; earlier drafts in `planning/archive/`). This document is the current authoritative design. It keeps the v1 architecture and fixes the defects and gaps that a re-read of the code against `PLAN.md` and the real `massive` SDK turned up. Every change from v1 is listed in §1 and marked **(v2)** where it appears.

---

## Table of Contents

1. [Changes from v1](#1-changes-from-v1)
2. [Requirements](#2-requirements)
3. [Architecture](#3-architecture)
4. [File Layout](#4-file-layout)
5. [Ticker Normalisation](#5-ticker-normalisation)
6. [Data Model — `PriceUpdate`](#6-data-model--priceupdate)
7. [Price Cache](#7-price-cache)
8. [Unified Interface — `MarketDataSource`](#8-unified-interface--marketdatasource)
9. [Seed Prices & Simulator Parameters](#9-seed-prices--simulator-parameters)
10. [GBM Simulator](#10-gbm-simulator)
11. [Massive API Client](#11-massive-api-client)
12. [Factory & Configuration](#12-factory--configuration)
13. [SSE Streaming Endpoint](#13-sse-streaming-endpoint)
14. [Application Integration](#14-application-integration)
15. [Frontend Consumption (reference)](#15-frontend-consumption-reference)
16. [Testing](#16-testing)
17. [Error Handling & Edge Cases](#17-error-handling--edge-cases)
18. [Implementation Checklist](#18-implementation-checklist)

---

## 1. Changes from v1

| # | Area | v1 behaviour | v2 design | Why |
|---|------|--------------|-----------|-----|
| 1 | **Massive parsing (bug)** | Reads `snap.last_trade.timestamp / 1000` | Reads `snap.last_trade.sip_timestamp` (**nanoseconds**), falling back to `snap.updated`, then `time.time()` | The SDK's `LastTrade` model has **no `timestamp` attribute**. Every snapshot raises `AttributeError` and gets skipped, so a real API key produces **zero prices**. The v1 tests pass only because they mock snapshots with `MagicMock`. |
| 2 | Daily change | `change_percent` is tick-to-tick only | `PriceUpdate` gains `day_ref_price` + `day_change` / `day_change_percent` | PLAN §10 requires "daily change %" in the watchlist. Tick-to-tick % is ~0.01% and useless for display. |
| 3 | Ticker case | Massive upper-cases in `add/remove` but not in `start`; simulator never does | One `normalize_ticker()` used at every entry point | `"aapl"` and `"AAPL"` must never be two different tickers. |
| 4 | Cache removal | `remove()` does not bump `version` | `remove()` bumps `version` | Otherwise the SSE stream keeps showing a removed ticker until the next price tick (up to 15 s with Massive). |
| 5 | Cache `version` | Read without the lock | Read under the lock | Consistency, and safe on free-threaded Python. |
| 6 | SSE router | Module-level `APIRouter`, so a second `create_stream_router()` call registers `/prices` twice | Router created inside the factory | Test isolation. |
| 7 | SSE keep-alive | Nothing is sent while prices are unchanged | `: ping` comment every 15 s while the stream is idle | Massive mode can go quiet for long periods (market closed). Idle connections get dropped by proxies. |
| 8 | Massive config | Poll interval hard-coded to 15 s | `MASSIVE_POLL_INTERVAL` env var (default 15) | PLAN §6 says paid tiers poll every 2–15 s. |
| 9 | Massive errors | Fixed interval, even under HTTP 429 | Exponential backoff on consecutive failures (capped), reset on success | Free tier is 5 req/min. Hammering after a 429 just extends the lockout. |
| 10 | Massive `add_ticker` | New ticker has no price until the next poll (≤15 s) | Immediate best-effort single-ticker fetch | A user who adds a ticker and buys it right away would otherwise get "no price available". |
| 11 | Tracked tickers | Only the watchlist | Watchlist **∪** open positions (`sync_tracked_tickers`) | Removing a held ticker from the watchlist must not stop pricing it, or portfolio valuation breaks. |

Everything else in v1 (strategy pattern, cache as the single source of truth, GBM with Cholesky correlation, shock events, version-based SSE change detection) stays as it is.

---

## 2. Requirements

From `PLAN.md` §6, §8, §10, §12:

- **Two sources, one interface.** Simulator by default. Massive REST poller when `MASSIVE_API_KEY` is non-empty. Downstream code doesn't know which one is running.
- **Simulator:** GBM, ~500 ms ticks, correlated sector moves, occasional 2–5% shock events, realistic seed prices, in-process, no dependencies.
- **Massive:** REST polling (not WebSocket), one call per cycle for the union of tracked tickers, 15 s on free tier, configurable.
- **Price cache:** latest price, previous price and timestamp per ticker. One writer task; many readers (SSE, portfolio valuation, trade execution, LLM context).
- **SSE:** `GET /api/stream/prices`, ~500 ms cadence. Each ticker payload carries ticker, price, previous price, timestamp and direction. Native `EventSource` reconnection.
- **Dynamic tickers:** watchlist add/remove (manual or via LLM) takes effect on the next cycle.
- **Testable:** deterministic seams for unit tests. No network in tests.

---

## 3. Architecture

```
                        ┌────────────────────────────┐
  MASSIVE_API_KEY? ───▶ │ create_market_data_source  │   (factory, at startup)
                        └─────────────┬──────────────┘
                                      │ returns one of
                 ┌────────────────────┴────────────────────┐
                 ▼                                         ▼
     ┌───────────────────────┐                ┌────────────────────────┐
     │ SimulatorDataSource   │                │ MassiveDataSource      │
     │  └─ GBMSimulator      │                │  └─ massive.RESTClient │
     │  asyncio task, 0.5 s  │                │  asyncio task, 15 s    │
     └───────────┬───────────┘                └───────────┬────────────┘
                 │ cache.update(...)                       │ cache.update(...)
                 └────────────────────┬────────────────────┘
                                      ▼
                        ┌────────────────────────────┐
                        │ PriceCache (in-memory)     │  latest PriceUpdate per ticker
                        │  version counter           │  + monotonic version
                        └─────────────┬──────────────┘
          ┌──────────────────┬────────┴─────────┬─────────────────────┐
          ▼                  ▼                  ▼                     ▼
   SSE /api/stream/   GET /api/portfolio  POST /api/portfolio/   POST /api/chat
   prices (per client) (valuation)        trade (fill price)     (LLM context)
```

### Concurrency model

- Everything runs on **one asyncio event loop** (uvicorn's).
- The only other thread is the one created by `asyncio.to_thread()` for the Massive SDK's blocking HTTP call. That thread only does I/O and returns snapshot objects. All `cache.update()` calls happen back on the loop.
- The `PriceCache` lock is therefore defensive. It costs nanoseconds and keeps the cache correct if a future writer runs in a thread, or on free-threaded CPython.
- The simulator's `step()` is pure CPU, about 20 µs for 10 tickers, and runs inline on the loop. That is far below anything that would delay request handling.

### Ownership rules

1. **Exactly one** data source writes to the cache.
2. Nobody reads prices from the data source directly. **Read the cache.**
3. The data source is the only owner of the **set of tracked tickers**. The app changes that set only through `add_ticker` / `remove_ticker` (wrapped by `sync_tracked_tickers`, §14.3).

---

## 4. File Layout

```
backend/
├── app/
│   ├── main.py                 # FastAPI app + lifespan (see §14 — not part of market/)
│   └── market/
│       ├── __init__.py         # public exports
│       ├── tickers.py          # normalize_ticker(), DEFAULT_TICKERS          (v2, new)
│       ├── models.py           # PriceUpdate
│       ├── cache.py            # PriceCache
│       ├── interface.py        # MarketDataSource (ABC)
│       ├── seed_prices.py      # seed prices, GBM params, correlation config
│       ├── simulator.py        # GBMSimulator + SimulatorDataSource
│       ├── massive_client.py   # MassiveDataSource + parse_snapshot()
│       ├── factory.py          # create_market_data_source()
│       └── stream.py           # create_stream_router() — SSE
└── tests/market/
    ├── test_tickers.py
    ├── test_models.py
    ├── test_cache.py
    ├── test_simulator.py
    ├── test_simulator_source.py
    ├── test_massive.py
    ├── test_factory.py
    └── test_stream.py          # (v2, new)
```

Public API (`app/market/__init__.py`):

```python
"""Market data subsystem for FinAlly."""

from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource
from .models import PriceUpdate
from .stream import create_stream_router
from .tickers import DEFAULT_TICKERS, InvalidTickerError, normalize_ticker

__all__ = [
    "DEFAULT_TICKERS",
    "InvalidTickerError",
    "MarketDataSource",
    "PriceCache",
    "PriceUpdate",
    "create_market_data_source",
    "create_stream_router",
    "normalize_ticker",
]
```

---

## 5. Ticker Normalisation

**(v2)** One function decides what a valid ticker looks like. The API layer calls it on user input, and the data sources call it defensively on every entry point.

`app/market/tickers.py`:

```python
"""Ticker symbol normalisation and validation."""

from __future__ import annotations

import re

# US equity symbols: 1-5 letters, optionally a class suffix (BRK.B, BF-B).
# Deliberately permissive — the simulator accepts anything well-formed;
# Massive simply returns no snapshot for symbols it doesn't know.
_TICKER_RE = re.compile(r"^[A-Z]{1,5}([.\-][A-Z]{1,2})?$")

DEFAULT_TICKERS: list[str] = [
    "AAPL", "GOOGL", "MSFT", "AMZN", "TSLA",
    "NVDA", "META", "JPM", "V", "NFLX",
]


class InvalidTickerError(ValueError):
    """Raised when a string is not a plausible ticker symbol."""


def normalize_ticker(raw: str) -> str:
    """Return the canonical (upper-case, trimmed) form of a ticker.

    >>> normalize_ticker(" aapl ")
    'AAPL'
    >>> normalize_ticker("brk.b")
    'BRK.B'
    """
    ticker = raw.strip().upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise InvalidTickerError(f"Invalid ticker symbol: {raw!r}")
    return ticker
```

The watchlist route converts `InvalidTickerError` into HTTP 400 (§14.4). The LLM chat flow catches it per action and reports it back in the response's action results.

---

## 6. Data Model — `PriceUpdate`

`PriceUpdate` is the only type that leaves the market layer. It is immutable, and its derived fields are computed properties, so it can never be internally inconsistent.

**(v2)** It adds `day_ref_price`, the reference for the "daily change %" column:

| Source | `day_ref_price` means |
|---|---|
| Simulator | Price when the ticker started being simulated (the "session open") |
| Massive | Previous trading day's close (`snap.prev_day.close`) |

`app/market/models.py`:

```python
"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time."""

    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds
    day_ref_price: float | None = None  # session open (sim) / previous close (Massive)

    # --- tick-to-tick (drives the green/red flash) ---

    @property
    def change(self) -> float:
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> str:
        """'up', 'down', or 'flat' relative to the previous tick."""
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    # --- daily (drives the watchlist "Chg %" column) ---

    @property
    def day_change(self) -> float:
        ref = self.day_ref_price or self.price
        return round(self.price - ref, 4)

    @property
    def day_change_percent(self) -> float:
        ref = self.day_ref_price
        if not ref:
            return 0.0
        return round((self.price - ref) / ref * 100, 4)

    def to_dict(self) -> dict:
        """Serialize for JSON / SSE transmission."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
            "day_ref_price": self.day_ref_price,
            "day_change": self.day_change,
            "day_change_percent": self.day_change_percent,
        }
```

Example:

```python
>>> u = PriceUpdate("AAPL", price=191.20, previous_price=191.05,
...                 timestamp=1760000000.0, day_ref_price=190.00)
>>> u.direction, u.change, u.day_change_percent
('up', 0.15, 0.6316)
```

---

## 7. Price Cache

Holds the latest `PriceUpdate` per ticker plus a monotonic `version`. Producers call `update()`. Consumers call `get` / `get_price` / `get_all`. The SSE stream compares `version` against what it last sent to decide whether to push.

Rules:

- `previous_price` is filled in by the cache from the prior entry, so producers only report "the price is now X". On the first update for a ticker, `previous_price == price` (direction `flat`).
- `day_ref_price` is **sticky**: if a producer passes `None`, the previous reference is kept. On the very first update with no reference, the first price becomes the reference.
- Prices are rounded to 2 dp at the cache boundary, so every consumer sees cent-precision prices.
- **(v2)** `remove()` bumps `version`, so SSE clients see the removal on the next tick.

`app/market/cache.py`:

```python
"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Latest price per ticker. One writer (the active data source), many readers."""

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version = 0

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        day_ref_price: float | None = None,
    ) -> PriceUpdate:
        """Record a new price and return the resulting PriceUpdate."""
        with self._lock:
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price
            if day_ref_price is None:
                day_ref_price = prev.day_ref_price if prev else price

            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=round(previous_price, 2),
                timestamp=timestamp if timestamp is not None else time.time(),
                day_ref_price=round(day_ref_price, 2),
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        with self._lock:
            return self._prices.get(ticker)

    def get_price(self, ticker: str) -> float | None:
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        """Shallow copy — safe to iterate while producers keep writing."""
        with self._lock:
            return dict(self._prices)

    def remove(self, ticker: str) -> None:
        with self._lock:
            if self._prices.pop(ticker, None) is not None:
                self._version += 1

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: object) -> bool:
        with self._lock:
            return ticker in self._prices
```

Usage:

```python
cache = PriceCache()
cache.update("AAPL", 190.00)              # first tick: prev=190.00, day_ref=190.00, flat
u = cache.update("AAPL", 190.37)          # prev=190.00, day_ref stays 190.00, up
assert (u.direction, u.day_change) == ("up", 0.37)
cache.get_price("AAPL")                   # 190.37
cache.remove("AAPL"); "AAPL" in cache     # False, version bumped
```

---

## 8. Unified Interface — `MarketDataSource`

Sources **push** into the cache on their own schedule. The interface has no `get_price()`, which enforces ownership rule 2 in §3.

`app/market/interface.py`:

```python
"""Abstract interface for market data sources."""

from __future__ import annotations

from abc import ABC, abstractmethod


class MarketDataSource(ABC):
    """Contract for market data providers.

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL", ...])
        await source.add_ticker("TSLA")
        await source.remove_ticker("GOOGL")
        await source.stop()
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing updates for `tickers`. Call exactly once.

        Implementations must seed the cache before returning (simulator:
        seed prices; Massive: one synchronous poll) so the first SSE frame
        and the first trade both have prices.
        """

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task. Idempotent. No cache writes afterwards."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Start tracking `ticker`. No-op if already tracked."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking `ticker` and drop it from the cache. No-op if absent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers (a copy)."""
```

Behaviour each implementation must satisfy (enforced by shared tests, §16.5):

| Call | Postcondition |
|---|---|
| `await start(["AAPL"])` | `"AAPL" in cache` immediately (Massive: if the API returned it) |
| `await add_ticker("aapl")` | `get_tickers()` contains `"AAPL"` exactly once |
| `await remove_ticker("AAPL")` | not in `get_tickers()`, not in cache |
| `await stop(); await stop()` | no exception, background task done |

---

## 9. Seed Prices & Simulator Parameters

Unchanged from v1. `app/market/seed_prices.py`:

```python
"""Seed prices and per-ticker parameters for the market simulator."""

SEED_PRICES: dict[str, float] = {
    "AAPL": 190.00, "GOOGL": 175.00, "MSFT": 420.00, "AMZN": 185.00,
    "TSLA": 250.00, "NVDA": 800.00, "META": 500.00, "JPM": 195.00,
    "V": 280.00, "NFLX": 600.00,
}

# sigma: annualised volatility; mu: annualised drift
TICKER_PARAMS: dict[str, dict[str, float]] = {
    "AAPL": {"sigma": 0.22, "mu": 0.05},
    "GOOGL": {"sigma": 0.25, "mu": 0.05},
    "MSFT": {"sigma": 0.20, "mu": 0.05},
    "AMZN": {"sigma": 0.28, "mu": 0.05},
    "TSLA": {"sigma": 0.50, "mu": 0.03},   # high vol
    "NVDA": {"sigma": 0.40, "mu": 0.08},   # high vol, strong drift
    "META": {"sigma": 0.30, "mu": 0.05},
    "JPM": {"sigma": 0.18, "mu": 0.04},    # bank — low vol
    "V": {"sigma": 0.17, "mu": 0.04},      # payments — low vol
    "NFLX": {"sigma": 0.35, "mu": 0.05},
}

DEFAULT_PARAMS: dict[str, float] = {"sigma": 0.25, "mu": 0.05}

# Unknown tickers start uniformly in this range
RANDOM_SEED_RANGE: tuple[float, float] = (50.0, 300.0)

CORRELATION_GROUPS: dict[str, set[str]] = {
    "tech": {"AAPL", "GOOGL", "MSFT", "AMZN", "META", "NVDA", "NFLX"},
    "finance": {"JPM", "V"},
}

INTRA_TECH_CORR = 0.6
INTRA_FINANCE_CORR = 0.5
CROSS_GROUP_CORR = 0.3   # cross-sector and unknown tickers
TSLA_CORR = 0.3          # TSLA does its own thing
```

---

## 10. GBM Simulator

### 10.1 Maths

Per tick, for each ticker:

```
S(t+dt) = S(t) · exp( (μ − σ²/2)·dt + σ·√dt·Z )
```

- `dt = 0.5 s / (252 days × 6.5 h × 3600 s) = 0.5 / 5,896,800 ≈ 8.48e-8` of a trading year.
- `Z` is a standard normal, **correlated across tickers** through `Z = L·ε`, where `L = cholesky(C)`, `C` is the sector correlation matrix, and `ε ~ N(0, I)`.
- `exp(·) > 0`, so prices can never go negative.

What these numbers mean on screen (AAPL at $190, σ = 0.22):

```
per-tick σ  = 0.22 · √8.48e-8 ≈ 6.4e-5   →  ≈ ±1.2¢ typical tick
per-minute  = 6.4e-5 · √120   ≈ 7.0e-4   →  ≈ ±13¢
per-hour    = 6.4e-5 · √7200  ≈ 5.4e-3   →  ≈ ±$1.03
```

TSLA (σ = 0.50, $250) moves about ±3.6¢ per tick. That is enough for most ticks to change the cent value, so flashes fire continuously.

**Shock events:** each tick, each ticker has probability `p = 0.001` of an extra multiplicative jump of ±U(2%, 5%). With 10 tickers at 2 ticks/s that is `10 × 2 × 0.001 = 0.02` events/s, roughly one somewhere on the board every 50 s.

**Correlation matrix for the default 10 tickers.** It is positive-definite, so Cholesky succeeds. The off-diagonals are 0.3, 0.5 or 0.6, and an equicorrelated block with ρ < 1 is always PD.

|       | AAPL | MSFT | TSLA | JPM | V   |
|-------|------|------|------|-----|-----|
| AAPL  | 1    | 0.6  | 0.3  | 0.3 | 0.3 |
| MSFT  | 0.6  | 1    | 0.3  | 0.3 | 0.3 |
| TSLA  | 0.3  | 0.3  | 1    | 0.3 | 0.3 |
| JPM   | 0.3  | 0.3  | 0.3  | 1   | 0.5 |
| V     | 0.3  | 0.3  | 0.3  | 0.5 | 1   |

### 10.2 `GBMSimulator`: the pure maths engine

No asyncio and no cache. It can be deterministic given an `rng`, so the maths can be unit-tested in isolation.

```python
"""GBM-based market simulator."""

from __future__ import annotations

import asyncio
import logging
import math
import random

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .seed_prices import (
    CORRELATION_GROUPS,
    CROSS_GROUP_CORR,
    DEFAULT_PARAMS,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    RANDOM_SEED_RANGE,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
)
from .tickers import normalize_ticker

logger = logging.getLogger(__name__)


class GBMSimulator:
    """Correlated Geometric Brownian Motion for a dynamic set of tickers."""

    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR  # ≈ 8.48e-8

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = 0.001,
        rng: np.random.Generator | None = None,
    ) -> None:
        self._dt = dt
        self._event_prob = event_probability
        self._rng = rng or np.random.default_rng()
        self._tickers: list[str] = []
        self._prices: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}
        self._cholesky: np.ndarray | None = None

        for t in tickers:
            self._add_internal(t)
        self._rebuild_cholesky()

    # --- public ---

    def step(self) -> dict[str, float]:
        """Advance every ticker one tick. Hot path (every 500 ms)."""
        n = len(self._tickers)
        if n == 0:
            return {}

        eps = self._rng.standard_normal(n)
        z = self._cholesky @ eps if self._cholesky is not None else eps
        shocks = self._rng.random(n)

        out: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            p = self._params[ticker]
            mu, sigma = p["mu"], p["sigma"]
            drift = (mu - 0.5 * sigma * sigma) * self._dt
            diffusion = sigma * math.sqrt(self._dt) * z[i]
            price = self._prices[ticker] * math.exp(drift + diffusion)

            if shocks[i] < self._event_prob:
                magnitude = self._rng.uniform(0.02, 0.05)
                sign = 1 if self._rng.random() < 0.5 else -1
                price *= 1 + sign * magnitude
                logger.debug("Shock on %s: %+.1f%%", ticker, sign * magnitude * 100)

            self._prices[ticker] = price
            out[ticker] = round(price, 2)
        return out

    def add_ticker(self, ticker: str) -> None:
        if ticker in self._prices:
            return
        self._add_internal(ticker)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        return self._prices.get(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- internals ---

    def _add_internal(self, ticker: str) -> None:
        if ticker in self._prices:
            return
        self._tickers.append(ticker)
        seed = SEED_PRICES.get(ticker)
        if seed is None:
            seed = float(self._rng.uniform(*RANDOM_SEED_RANGE))
        self._prices[ticker] = seed
        self._params[ticker] = dict(TICKER_PARAMS.get(ticker, DEFAULT_PARAMS))

    def _rebuild_cholesky(self) -> None:
        """O(n²) build + O(n³) factorisation; n < 50 so this is microseconds."""
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self.pairwise_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = corr[j, i] = rho
        self._cholesky = np.linalg.cholesky(corr)

    @staticmethod
    def pairwise_correlation(t1: str, t2: str) -> float:
        tech, finance = CORRELATION_GROUPS["tech"], CORRELATION_GROUPS["finance"]
        if "TSLA" in (t1, t2):
            return TSLA_CORR
        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in finance and t2 in finance:
            return INTRA_FINANCE_CORR
        return CROSS_GROUP_CORR
```

> **v2 note:** v1 used global `np.random` / `random` state. Passing an injectable `np.random.Generator` lets tests write `GBMSimulator([...], rng=np.random.default_rng(42))` and get a reproducible path without monkey-patching globals.

### 10.3 `SimulatorDataSource`: the async wrapper

```python
class SimulatorDataSource(MarketDataSource):
    """Runs GBMSimulator.step() every `update_interval` s and writes to the cache."""

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        event_probability: float = 0.001,
        rng: np.random.Generator | None = None,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._rng = rng
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        normalized = list(dict.fromkeys(normalize_ticker(t) for t in tickers))  # dedupe, keep order
        self._sim = GBMSimulator(normalized, event_probability=self._event_prob, rng=self._rng)
        for t in normalized:
            self._seed_cache(t)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started with %d tickers", len(normalized))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if self._sim is None or ticker in self._sim.get_tickers():
            return
        self._sim.add_ticker(ticker)
        self._seed_cache(ticker)  # price available immediately, not 500 ms later

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    # --- internals ---

    def _seed_cache(self, ticker: str) -> None:
        price = self._sim.get_price(ticker) if self._sim else None
        if price is not None:
            # The seed price is this ticker's "session open" → day_ref_price.
            self._cache.update(ticker, price, day_ref_price=price)

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim:
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker, price)  # day_ref sticky
            except Exception:
                logger.exception("Simulator step failed")  # never kill the loop
            await asyncio.sleep(self._interval)
```

Timeline for a fresh start:

```
t=0.000  start(["AAPL",...])  → cache: AAPL 190.00 (prev 190.00, day_ref 190.00, flat)
t=0.000  loop iteration 1     → cache: AAPL 190.01 (prev 190.00, up)
t=0.500  loop iteration 2     → cache: AAPL 189.99 (prev 190.01, down)
...
t=12.3   add_ticker("PYPL")   → cache: PYPL 143.27 (random seed, day_ref 143.27)
```

---

## 11. Massive API Client

### 11.1 Endpoint and SDK field mapping

One call per cycle returns every tracked ticker:

```
GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT,...
SDK: RESTClient.get_snapshot_all(market_type=SnapshotMarketType.STOCKS, tickers=[...])
     → list[massive.rest.models.TickerSnapshot]
```

The field mapping below was **verified against the installed `massive` SDK models** (`massive/rest/models/snapshot.py`, `trades.py`, `aggs.py`). Earlier drafts in `planning/archive/` got several of these wrong:

| What we need | Raw JSON | SDK attribute | Notes |
|---|---|---|---|
| Ticker | `ticker` | `snap.ticker` | |
| Last price | `lastTrade.p` | `snap.last_trade.price` | `last_trade` may be `None` |
| Trade time | `lastTrade.t` | `snap.last_trade.sip_timestamp` | **Unix nanoseconds.** There is no `.timestamp` attribute. |
| Snapshot time | `updated` | `snap.updated` | Unix nanoseconds; fallback |
| Latest minute close | `min.c` | `snap.min.close` | fallback price |
| Previous close | `prevDay.c` | `snap.prev_day.close` | → `day_ref_price` |
| Today's change % | `todaysChangePerc` | `snap.todays_change_percent` | Not used; we derive it from `prev_day.close` so both sources compute it the same way |

Example snapshot (abridged):

```json
{
  "ticker": "AAPL",
  "todaysChangePerc": 0.82,
  "updated": 1760630400123456789,
  "day":     {"o": 189.1, "h": 191.9, "l": 188.7, "c": 191.4, "v": 48211932},
  "lastTrade": {"p": 191.42, "s": 100, "x": 4, "t": 1760630400118000000},
  "min":     {"c": 191.40, "t": 1760630340000},
  "prevDay": {"c": 189.86}
}
```

→ `cache.update("AAPL", 191.42, timestamp=1760630400.118, day_ref_price=189.86)`

### 11.2 Parsing: a pure, testable function

Keep the parsing out of the I/O loop so it can be tested against real SDK objects built with `TickerSnapshot.from_dict(...)`, not `MagicMock`. That is the test gap that let bug #1 through.

```python
"""Massive (Polygon.io) REST poller."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from massive import RESTClient
from massive.rest.models import SnapshotMarketType, TickerSnapshot

from .cache import PriceCache
from .interface import MarketDataSource
from .tickers import normalize_ticker

logger = logging.getLogger(__name__)

NS_PER_SECOND = 1_000_000_000


@dataclass(frozen=True, slots=True)
class ParsedQuote:
    ticker: str
    price: float
    timestamp: float  # Unix seconds
    day_ref_price: float | None


def parse_snapshot(snap: TickerSnapshot) -> ParsedQuote | None:
    """Extract a price from a snapshot, or None if it carries no usable price."""
    if not snap.ticker:
        return None

    price = None
    if snap.last_trade is not None and snap.last_trade.price:
        price = snap.last_trade.price
    elif snap.min is not None and snap.min.close:
        price = snap.min.close
    elif snap.prev_day is not None and snap.prev_day.close:
        price = snap.prev_day.close  # pre-market after the midnight reset
    if price is None:
        return None

    ts_ns = None
    if snap.last_trade is not None:
        ts_ns = snap.last_trade.sip_timestamp
    ts_ns = ts_ns or snap.updated
    timestamp = ts_ns / NS_PER_SECOND if ts_ns else time.time()

    day_ref = snap.prev_day.close if snap.prev_day is not None else None
    return ParsedQuote(snap.ticker, float(price), timestamp, day_ref or None)
```

### 11.3 `MassiveDataSource`

```python
class MassiveDataSource(MarketDataSource):
    """Polls the snapshot endpoint for all tracked tickers in one call per cycle.

    Rate limits: free tier 5 req/min → 15 s interval (4 req/min, leaving one
    spare call per minute for add_ticker's immediate fetch). Paid: 2–5 s.
    """

    MAX_BACKOFF = 120.0  # seconds

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
        client: RESTClient | None = None,  # injectable for tests
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._client = client
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._consecutive_failures = 0

    async def start(self, tickers: list[str]) -> None:
        if self._client is None:
            self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(dict.fromkeys(normalize_ticker(t) for t in tickers))
        await self._poll_once()  # seed the cache before the app serves requests
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info("Massive poller: %d tickers every %.1fs", len(self._tickers), self._interval)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker in self._tickers:
            return
        self._tickers.append(ticker)
        # Best effort: price it now instead of waiting up to one interval.
        await self._poll_once([ticker])

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- internals ---

    def _next_delay(self) -> float:
        """Normal interval, doubled per consecutive failure, capped."""
        if self._consecutive_failures == 0:
            return self._interval
        return min(self._interval * 2 ** self._consecutive_failures, self.MAX_BACKOFF)

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._next_delay())
            await self._poll_once()

    async def _poll_once(self, tickers: list[str] | None = None) -> None:
        tickers = list(tickers if tickers is not None else self._tickers)  # copy: list may change
        if not tickers or self._client is None:
            return
        try:
            snapshots = await asyncio.to_thread(self._fetch, tickers)
        except Exception as e:  # 401 bad key, 403 plan, 429 rate limit, network
            self._consecutive_failures += 1
            logger.error(
                "Massive poll failed (%d in a row, next try in %.0fs): %s",
                self._consecutive_failures, self._next_delay(), e,
            )
            return
        self._consecutive_failures = 0

        tracked = set(self._tickers)
        updated = 0
        for snap in snapshots:
            quote = parse_snapshot(snap)
            if quote is None:
                logger.warning("No usable price in snapshot for %s", getattr(snap, "ticker", "?"))
                continue
            if quote.ticker not in tracked:
                continue  # removed while the request was in flight
            self._cache.update(
                quote.ticker, quote.price,
                timestamp=quote.timestamp, day_ref_price=quote.day_ref_price,
            )
            updated += 1
        logger.debug("Massive poll: %d/%d tickers updated", updated, len(tickers))

    def _fetch(self, tickers: list[str]) -> list[TickerSnapshot]:
        """Blocking SDK call — always run via asyncio.to_thread."""
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=tickers,
        )
```

Design notes:

- **Why `to_thread`:** the SDK uses blocking `urllib3`. Calling it directly would freeze every SSE stream for the duration of the HTTP request.
- **Race guard:** a ticker removed while a request is in flight is filtered by the `tracked` check, so it doesn't reappear in the cache.
- **Unknown symbols:** Massive omits them from the response. They stay in `get_tickers()` but never get a cache entry. The watchlist UI shows "—" for them, and a trade on them fails with "no price available" (§17).
- **Market closed:** `last_trade` keeps the last print, so the cache still updates every poll with an unchanged price (`direction == "flat"`), and SSE keeps flowing at 15 s cadence. Flashes only fire on real changes.
- **Retries:** the SDK's `urllib3.Retry` already retries 5xx. Our backoff handles 429 and auth errors at the loop level.

---

## 12. Factory & Configuration

| Env var | Default | Effect |
|---|---|---|
| `MASSIVE_API_KEY` | empty | Non-empty selects Massive; otherwise the simulator |
| `MASSIVE_POLL_INTERVAL` | `15` | Seconds between Massive polls (**(v2)**, clamped to ≥ 1) |

`app/market/factory.py`:

```python
"""Selects the market data source from the environment."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)


def _poll_interval() -> float:
    raw = os.environ.get("MASSIVE_POLL_INTERVAL", "").strip()
    try:
        return max(1.0, float(raw)) if raw else 15.0
    except ValueError:
        logger.warning("Ignoring invalid MASSIVE_POLL_INTERVAL=%r", raw)
        return 15.0


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Return an *unstarted* source. Caller must `await source.start(tickers)`."""
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        interval = _poll_interval()
        logger.info("Market data: Massive API (poll every %.1fs)", interval)
        return MassiveDataSource(api_key=api_key, price_cache=price_cache, poll_interval=interval)
    logger.info("Market data: GBM simulator")
    return SimulatorDataSource(price_cache=price_cache)
```

`.env.example` additions:

```bash
# Optional: real market data. Leave empty for the built-in simulator.
MASSIVE_API_KEY=
# Optional: seconds between Massive polls (free tier: keep at 15)
MASSIVE_POLL_INTERVAL=15
```

---

## 13. SSE Streaming Endpoint

### 13.1 Wire format

`GET /api/stream/prices` → `text/event-stream`. One `data:` frame holds the full current board, keyed by ticker. A frame is sent only when the cache `version` has changed since the last frame for this client:

```
retry: 1000

data: {"AAPL":{"ticker":"AAPL","price":190.37,"previous_price":190.31,"timestamp":1760630400.5,"change":0.06,"change_percent":0.0315,"direction":"up","day_ref_price":190.0,"day_change":0.37,"day_change_percent":0.1947},"MSFT":{...}}

data: {...}

: ping

```

- `retry: 1000` tells `EventSource` to reconnect 1 s after a drop.
- `: ping` is an SSE comment. The browser ignores it, but it keeps proxies and load balancers from closing an idle stream **(v2)**.
- Sending the whole board (≈10 × 250 B = 2.5 KB per 500 ms) instead of diffs keeps the client trivial and self-healing: a reconnecting client is fully up to date after one frame. Removed tickers disappear from the next frame because `remove()` bumps `version`.

### 13.2 Implementation

`app/market/stream.py`:

```python
"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

PUSH_INTERVAL = 0.5       # seconds between version checks
HEARTBEAT_INTERVAL = 15.0  # seconds of silence before a ping comment


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    """Build a fresh router per call (v2) so tests can create many apps."""
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            generate_events(price_cache, request),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router


async def generate_events(
    price_cache: PriceCache,
    request: Request,
    interval: float = PUSH_INTERVAL,
    heartbeat: float = HEARTBEAT_INTERVAL,
) -> AsyncGenerator[str, None]:
    yield "retry: 1000\n\n"

    client = request.client.host if request.client else "unknown"
    logger.info("SSE connected: %s", client)
    last_version = -1
    last_sent = time.monotonic()
    try:
        while not await request.is_disconnected():
            version = price_cache.version
            if version != last_version:
                last_version = version
                board = {t: u.to_dict() for t, u in price_cache.get_all().items()}
                yield f"data: {json.dumps(board)}\n\n"
                last_sent = time.monotonic()
            elif time.monotonic() - last_sent >= heartbeat:
                yield ": ping\n\n"
                last_sent = time.monotonic()
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("SSE disconnected: %s", client)
```

> An empty board is now sent as `data: {}`, where v1 skipped it. That makes "the user removed every ticker" visible to the client instead of leaving stale rows.

---

## 14. Application Integration

### 14.1 Lifespan wiring (`app/main.py`)

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import get_watchlist_tickers, get_position_tickers, init_db  # owned by backend agent
from app.market import PriceCache, create_market_data_source, create_stream_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()  # lazy schema + seed (PLAN §7)

    cache = PriceCache()
    source = create_market_data_source(cache)
    tracked = sorted(set(get_watchlist_tickers()) | set(get_position_tickers()))
    await source.start(tracked)

    app.state.price_cache = cache
    app.state.market_source = source
    try:
        yield
    finally:
        await source.stop()


app = FastAPI(lifespan=lifespan)
```

The stream router needs the cache when the routes are registered. The cache is created inside `lifespan`, so either create the cache at module level, or (preferred) have routes fetch it from `request.app.state`:

```python
# Module-level cache: simplest, and the cache has no I/O so it is safe to build at import.
price_cache = PriceCache()
app.include_router(create_stream_router(price_cache))
# ...and in lifespan use this same `price_cache` instead of constructing a new one.
```

### 14.2 Dependencies for route handlers

```python
from fastapi import Request

from app.market import MarketDataSource, PriceCache


def get_price_cache(request: Request) -> PriceCache:
    return request.app.state.price_cache


def get_market_source(request: Request) -> MarketDataSource:
    return request.app.state.market_source
```

### 14.3 Keeping tracked tickers = watchlist ∪ positions **(v2)**

Portfolio valuation needs a live price for every **held** ticker, even after the user removes it from the watchlist. Make one idempotent helper the only place that reconciles the two sets. Call it after every watchlist change and every trade:

```python
async def sync_tracked_tickers(source: MarketDataSource, db) -> None:
    """Make the source track exactly watchlist ∪ open positions."""
    desired = set(get_watchlist_tickers(db)) | set(get_position_tickers(db))
    current = set(source.get_tickers())
    for t in sorted(desired - current):
        await source.add_ticker(t)
    for t in sorted(current - desired):
        await source.remove_ticker(t)
```

| Scenario | Watchlist | Position | Tracked? |
|---|---|---|---|
| Default ticker, not held | ✓ | – | ✓ |
| Removed from watchlist but still held | – | ✓ | ✓ (still priced, absent from watchlist UI) |
| Sold to zero, not on watchlist | – | – | ✗ removed from source and cache |

The SSE board contains every tracked ticker. The frontend renders watchlist rows from `GET /api/watchlist` and uses the SSE board as a price lookup, so held-but-unwatched tickers stay priced without appearing in the watchlist.

### 14.4 Watchlist routes (market-data touch points)

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.market import InvalidTickerError, normalize_ticker

router = APIRouter(prefix="/api/watchlist")


class AddTicker(BaseModel):
    ticker: str


@router.post("", status_code=201)
async def add_to_watchlist(body: AddTicker, source=Depends(get_market_source),
                           cache=Depends(get_price_cache), db=Depends(get_db)):
    try:
        ticker = normalize_ticker(body.ticker)
    except InvalidTickerError as e:
        raise HTTPException(400, str(e))
    insert_watchlist(db, ticker)          # INSERT OR IGNORE — UNIQUE(user_id, ticker)
    await sync_tracked_tickers(source, db)
    u = cache.get(ticker)
    return {"ticker": ticker, "price": u.price if u else None}


@router.delete("/{ticker}", status_code=204)
async def remove_from_watchlist(ticker: str, source=Depends(get_market_source), db=Depends(get_db)):
    delete_watchlist(db, normalize_ticker(ticker))
    await sync_tracked_tickers(source, db)
```

`GET /api/watchlist` joins the DB rows with `cache.get(t)` and returns `price`, `previous_price`, `day_change_percent` and `direction` (`null` until priced).

### 14.5 Trade execution: price lookup

```python
def current_price_or_400(cache: PriceCache, ticker: str) -> float:
    price = cache.get_price(ticker)
    if price is None:
        raise HTTPException(400, f"No price available for {ticker}. Add it to the watchlist and retry.")
    return price
```

The fill price is whatever is in the cache at the moment of the request (PLAN: "instant fill at current price"). After the trade commits, call `sync_tracked_tickers` so that a new position is priced from then on, even if its ticker isn't watched.

### 14.6 Portfolio valuation

```python
def portfolio_value(cash: float, positions: list[Position], cache: PriceCache) -> float:
    total = cash
    for p in positions:
        price = cache.get_price(p.ticker) or p.avg_cost  # fall back to cost if unpriced
        total += p.quantity * price
    return round(total, 2)
```

The 30 s snapshot task and the post-trade snapshot (PLAN §7) both use this same function.

---

## 15. Frontend Consumption (reference)

This sketch shows what the backend contract has to support. The real component design belongs to the frontend agent.

```ts
type PriceUpdate = {
  ticker: string; price: number; previous_price: number; timestamp: number;
  change: number; change_percent: number; direction: "up" | "down" | "flat";
  day_ref_price: number | null; day_change: number; day_change_percent: number;
};
type Board = Record<string, PriceUpdate>;

export function connectPrices(
  onBoard: (b: Board) => void,
  onStatus: (s: "connected" | "reconnecting" | "disconnected") => void,
): () => void {
  const es = new EventSource("/api/stream/prices");
  es.onopen = () => onStatus("connected");
  es.onmessage = (e) => onBoard(JSON.parse(e.data) as Board);
  es.onerror = () =>
    onStatus(es.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
  return () => es.close();
}
```

- **Flash:** when `direction !== "flat"` and `timestamp` changed, apply `flash-up` / `flash-down` for about 500 ms.
- **Sparklines:** append `price` per ticker on each board, capped at about 300 points (≈2.5 min at 500 ms).
- **Chg % column:** `day_change_percent`.

---

## 16. Testing

All tests run offline. `pytest-asyncio` is in auto mode (already configured in `pyproject.toml`).

### 16.1 Tickers

```python
import pytest
from app.market.tickers import InvalidTickerError, normalize_ticker


@pytest.mark.parametrize("raw,expected", [("aapl", "AAPL"), (" msft ", "MSFT"), ("brk.b", "BRK.B")])
def test_normalize(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize("raw", ["", "TOOLONG", "AA PL", "123", "$AAPL"])
def test_rejects(raw):
    with pytest.raises(InvalidTickerError):
        normalize_ticker(raw)
```

### 16.2 Cache

```python
from app.market.cache import PriceCache


def test_day_ref_sticky_and_direction():
    c = PriceCache()
    c.update("AAPL", 190.0)
    u = c.update("AAPL", 191.9)
    assert u.previous_price == 190.0 and u.day_ref_price == 190.0
    assert u.direction == "up" and u.day_change_percent == 1.0


def test_remove_bumps_version():
    c = PriceCache()
    c.update("AAPL", 190.0)
    v = c.version
    c.remove("AAPL")
    assert c.version == v + 1
    c.remove("AAPL")  # absent → no bump
    assert c.version == v + 1
```

### 16.3 Simulator

```python
import numpy as np
from app.market.simulator import GBMSimulator
from app.market.tickers import DEFAULT_TICKERS


def test_deterministic_with_seeded_rng():
    a = GBMSimulator(["AAPL", "MSFT"], rng=np.random.default_rng(1))
    b = GBMSimulator(["AAPL", "MSFT"], rng=np.random.default_rng(1))
    assert [a.step() for _ in range(50)] == [b.step() for _ in range(50)]


def test_full_default_set_cholesky_and_positive_prices():
    sim = GBMSimulator(DEFAULT_TICKERS, event_probability=0.05)
    for _ in range(5_000):
        assert all(p > 0 for p in sim.step().values())


def test_tech_correlation_is_visible():
    sim = GBMSimulator(["AAPL", "MSFT", "JPM"], event_probability=0.0,
                       rng=np.random.default_rng(7))
    prev = {t: sim.get_price(t) for t in sim.get_tickers()}
    rets = {t: [] for t in prev}
    for _ in range(20_000):
        sim.step()
        for t in prev:
            p = sim.get_price(t)
            rets[t].append(np.log(p / prev[t]))
            prev[t] = p
    rho = np.corrcoef(rets["AAPL"], rets["MSFT"])[0, 1]
    assert 0.5 < rho < 0.7  # target 0.6
```

### 16.4 Massive: real SDK objects, no `MagicMock` snapshots

This test **fails against v1** and catches bug #1:

```python
from unittest.mock import MagicMock

from massive.rest.models import TickerSnapshot

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource, parse_snapshot


def snap(ticker="AAPL", price=191.42, ts_ns=1_760_630_400_118_000_000, prev_close=189.86):
    return TickerSnapshot.from_dict({
        "ticker": ticker,
        "lastTrade": {"p": price, "t": ts_ns},
        "prevDay": {"c": prev_close},
        "updated": ts_ns,
    })


def test_parse_snapshot_uses_sip_timestamp_ns():
    q = parse_snapshot(snap())
    assert q.price == 191.42
    assert q.timestamp == 1_760_630_400.118
    assert q.day_ref_price == 189.86


def test_parse_snapshot_without_trade_falls_back_to_prev_close():
    q = parse_snapshot(TickerSnapshot.from_dict({"ticker": "AAPL", "prevDay": {"c": 189.86}}))
    assert q.price == 189.86


async def test_poll_writes_cache_and_backs_off():
    client = MagicMock()
    client.get_snapshot_all.return_value = [snap()]
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=15, client=client)
    await src.start(["aapl"])
    try:
        assert cache.get("AAPL").day_change_percent == 0.8217

        client.get_snapshot_all.side_effect = RuntimeError("429")
        await src._poll_once()
        await src._poll_once()
        assert src._next_delay() == 60  # 15 · 2²
    finally:
        await src.stop()


async def test_add_ticker_fetches_immediately():
    client = MagicMock()
    client.get_snapshot_all.side_effect = lambda market_type, tickers: [snap(t) for t in tickers]
    cache = PriceCache()
    src = MassiveDataSource("k", cache, client=client)
    await src.start(["AAPL"])
    try:
        await src.add_ticker("pypl")
        assert "PYPL" in cache
    finally:
        await src.stop()
```

### 16.5 Shared contract test (both sources)

```python
import pytest
from unittest.mock import MagicMock

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


@pytest.fixture(params=["sim", "massive"])
def make_source(request):
    def _make(cache):
        if request.param == "sim":
            return SimulatorDataSource(cache, update_interval=0.01)
        client = MagicMock()
        client.get_snapshot_all.side_effect = lambda market_type, tickers: [snap(t) for t in tickers]
        return MassiveDataSource("k", cache, poll_interval=0.01, client=client)
    return _make


async def test_contract(make_source):
    cache = PriceCache()
    src = make_source(cache)
    await src.start(["aapl", "AAPL", "MSFT"])
    assert src.get_tickers() == ["AAPL", "MSFT"] and "AAPL" in cache  # snap() from §16.4
    await src.add_ticker("tsla")
    assert "TSLA" in src.get_tickers() and "TSLA" in cache
    await src.remove_ticker("AAPL")
    assert "AAPL" not in src.get_tickers() and "AAPL" not in cache
    await src.stop()
    await src.stop()
```

### 16.6 SSE generator

Drive the generator directly with a fake request. This avoids fighting an infinite `StreamingResponse` through an HTTP client:

```python
import json

from app.market.cache import PriceCache
from app.market.stream import create_stream_router, generate_events


class FakeRequest:
    client = None

    def __init__(self, disconnect_after: int):
        self._n = disconnect_after

    async def is_disconnected(self) -> bool:
        self._n -= 1
        return self._n < 0


async def test_stream_sends_board_once_per_version_then_pings():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    frames = [f async for f in generate_events(cache, FakeRequest(3), interval=0, heartbeat=0)]
    assert frames[0] == "retry: 1000\n\n"
    assert json.loads(frames[1].removeprefix("data: "))["AAPL"]["price"] == 190.0
    assert frames[2:] == [": ping\n\n", ": ping\n\n"]  # version unchanged


def test_router_factory_is_isolated():
    r1, r2 = create_stream_router(PriceCache()), create_stream_router(PriceCache())
    assert len(r1.routes) == len(r2.routes) == 1
```

### 16.7 Manual check against the real API (optional)

```bash
cd backend
MASSIVE_API_KEY=... uv run python -c "
import asyncio
from app.market import PriceCache, create_market_data_source
async def main():
    c = PriceCache(); s = create_market_data_source(c)
    await s.start(['AAPL','MSFT']); print({t: u.to_dict() for t, u in c.get_all().items()})
    await s.stop()
asyncio.run(main())"
```

---

## 17. Error Handling & Edge Cases

| Situation | Behaviour |
|---|---|
| Empty tracked set at startup | Both sources start fine. The simulator steps nothing; Massive skips the HTTP call. SSE sends `data: {}`. |
| Invalid symbol text (`"AA PL"`) | `normalize_ticker` raises → watchlist route returns 400; the LLM action reports an error. |
| Well-formed but nonexistent symbol (`"ZZZZ"`) | Simulator: priced at a random seed (acceptable for a sim). Massive: no snapshot → no cache entry → UI shows "—", and a trade on it returns 400 "No price available". |
| Trade on a ticker that isn't priced yet | 400 from `current_price_or_400`. Normally impossible: `add_ticker` seeds the cache immediately in both sources. |
| Massive 401 / 403 | Logged as an error on every attempt, with backoff to 120 s. The cache stays empty and the app keeps running. Fix: correct the key or unset it to use the simulator. |
| Massive 429 | Backoff 30 → 60 → 120 s, reset on the first success. |
| Massive network outage | Same as 429. Cached prices stay at their last value (stale but valid) and SSE pings keep the connection alive. |
| Market closed (Massive) | Prices repeat. `direction == "flat"`, so there are no flashes. Day change is still correct against the previous close. |
| Simulator step raises (should never happen) | Logged with traceback; the loop continues on the next tick. |
| Ticker removed mid-poll | Filtered by the `tracked` check in `_poll_once`, so it isn't resurrected. |
| Client disconnects from SSE | `is_disconnected()` ends the generator within one `interval`. |
| App shutdown | `lifespan` → `source.stop()` cancels and awaits the task, so no writes happen after shutdown. |
| Hundreds of SSE clients | Each holds one coroutine and serialises at most one board every 500 ms. That cost is negligible at single-user scale. If needed later, serialise once per version and share the string. |

---

## 18. Implementation Checklist

Work items to bring `backend/app/market/` from v1 to this design, in a sensible order. Each item should land with its tests.

Status as of 2026-10-16 (branch `market-data-review-fixes`, see `MARKET_DATA_REVIEW.md` §7):

1. [x] **Fix Massive timestamp/price parsing**: `parse_snapshot()` reads `sip_timestamp` (ns) with the price fallback chain; fixtures use `TickerSnapshot.from_dict(...)`.
2. [x] `normalize_ticker()` in all `start/add/remove` calls. It lives in `interface.py`, not `tickers.py`, next to `UnknownTickerError`. Format validation (`InvalidTickerError`) is left to the API layer.
3. [ ] ~~`day_ref_price`~~: superseded. `reference_price` + `change_pct` (PLAN.md §6) already cover the daily change.
4. [x] `remove()` bumps `version`. (`version` is still read without the lock; it is an int read, atomic in CPython.)
5. [x] Simulator: seeded `numpy` RNG (`seed=`); `add_ticker` no-op guard. The seed price is the reference via the cache's first-seen rule.
6. [x] Massive: immediate fetch in `add_ticker` (raises `UnknownTickerError`), in-flight removal guard. Not done: injectable client (tests patch `RESTClient`) and backoff (the loop retries every interval).
7. [ ] `MASSIVE_POLL_INTERVAL`: not added. The 5 s default matches PLAN.md, so no new env var is needed.
8. [x] SSE: router created per factory call; keepalive pings and disconnect handling come from FastAPI's `EventSourceResponse`; an empty map is sent after the last removal; public `price_events()`; `test_stream.py`.
9. [x] Shared contract test across both sources (`tests/market/test_contract.py`).
10. [x] `MARKET_DATA_SUMMARY.md` and `backend/CLAUDE.md` updated.
11. [ ] (Backend agent) `lifespan`, `sync_tracked_tickers`, and the watchlist/trade/portfolio touch points in §14.
