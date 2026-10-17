# Market Data Backend — Summary

**Status:** Complete and tested. The 2026-10-16 code review (`MARKET_DATA_REVIEW.md`) is resolved; see its §7.

## What Was Built

A market data subsystem in `backend/app/market/` (9 modules) that provides live simulated prices or real Massive prices behind one interface.

### Architecture

```
MarketDataSource (ABC)
├── SimulatorDataSource  →  GBM simulator (default, no API key needed)
└── MassiveDataSource    →  Massive (Polygon.io) REST poller (when MASSIVE_API_KEY set)
        │
        ▼
   PriceCache (thread-safe, in-memory, version counter)
        │
        ├──→ SSE stream endpoint (/api/stream/prices)
        ├──→ Portfolio valuation
        └──→ Trade execution
```

### Modules

| File | Purpose |
|------|---------|
| `models.py` | `PriceUpdate`: frozen dataclass (ticker, price, previous_price, timestamp, reference_price). It has `change_pct` against the reference price and `to_sse_dict()` in the PLAN.md payload shape. |
| `interface.py` | `MarketDataSource` ABC (`start/stop/add_ticker/remove_ticker/get_tickers`), `UnknownTickerError`, `normalize_ticker()` |
| `cache.py` | `PriceCache`: thread-safe store. `version` is bumped on every update and every removal. |
| `seed_prices.py` | Seed prices, per-ticker drift/volatility, correlation groups |
| `simulator.py` | `GBMSimulator` (Cholesky-correlated GBM, seeded `numpy` RNG) and `SimulatorDataSource` |
| `massive_client.py` | `parse_snapshot()` and `MassiveDataSource`: one snapshot call per poll, every 5 s |
| `factory.py` | `create_market_data_source()`: Massive if `MASSIVE_API_KEY` is set, otherwise the simulator |
| `stream.py` | `price_events()` generator and `create_stream_router()`, using FastAPI's native `EventSourceResponse` |

### Behaviour

- **Tickers** are normalized (stripped, uppercased) by both sources.
- **`change_pct`** is measured from a reference price:
  - the seed price for the simulator;
  - the previous close for Massive, falling back to the first price seen.
- **Simulator:**
  - ~500 ms ticks, using GBM with real-time `dt`.
  - Correlations: tech 0.6, finance 0.5, cross-sector and TSLA 0.3.
  - Jump events of 2-5% with probability 0.0001 per ticker per tick, about 7-12 per hour across 10 tickers.
  - Unknown symbols start at a random $50-300.
- **Massive:**
  - **Price** is read from `last_trade.price`, falling back to the latest minute bar's close, then today's close, then the previous close.
  - **Timestamps** come from `sip_timestamp`, in nanoseconds.
  - **Startup:**
    - `start()` raises if the first poll fails or prices none of the tracked tickers.
    - With no tickers, it checks the key against `SPY`.
  - **Adding a ticker** (`add_ticker()`) fetches its snapshot immediately and raises `UnknownTickerError` when there is no data.
  - **Removed tickers** are filtered out of in-flight poll results.
- **SSE:**
  - One event with the full price map whenever the cache version changes, checked every 500 ms.
  - An empty map is sent after the last ticker is removed.
  - The stream starts with `retry: 1000`, and FastAPI sends keepalive pings.

## Test Suite

**116 tests, all passing; 99% coverage.** `ruff check` and `ruff format` are clean, with no warnings.

| Module | Covers |
|--------|--------|
| `test_models.py`, `test_reference.py` | `PriceUpdate` math, `change_pct`, SSE dict shape |
| `test_cache.py` | Cache updates, direction, reference price, version on update and remove |
| `test_simulator.py` | GBM behaviour, seeded determinism, per-tick volatility, correlation structure, jump size |
| `test_simulator_source.py` | Async loop, add/remove, normalization, exception resilience |
| `test_massive.py` | `parse_snapshot` on real SDK models, polling, removal race, startup fail-fast, `UnknownTickerError` |
| `test_contract.py` | Both sources against the same `MarketDataSource` contract |
| `test_stream.py` | `price_events` change detection and removal, router independence, live uvicorn smoke test |
| `test_factory.py` | Source selection from `MASSIVE_API_KEY` |

The Massive source has not been run against the live API, because no paid key was available. Its tests use real `massive` SDK models built from API-shaped payloads.

## Demo

```bash
cd backend
uv run market_data_demo.py
```

A Rich terminal dashboard with all 10 tickers, sparklines and an event log. It runs for 60 seconds or until Ctrl+C.

## Usage for Downstream Code

```python
from fastapi import HTTPException

from app.market import PriceCache, UnknownTickerError, create_market_data_source, create_stream_router

# Startup (FastAPI lifespan)
cache = PriceCache()
source = create_market_data_source(cache)  # Reads MASSIVE_API_KEY
await source.start(tracked_tickers)         # Raises RuntimeError on a bad Massive key/plan
app.include_router(create_stream_router(cache))

# Read prices
cache.get("AAPL")        # PriceUpdate or None
cache.get_price("AAPL")  # float or None

# Watchlist / positions change the tracked set (watchlist ∪ positions)
try:
    await source.add_ticker("PYPL")  # Priced in the cache when this returns
except UnknownTickerError:
    raise HTTPException(400, "Unknown ticker")
await source.remove_ticker("GOOGL")

# Shutdown
await source.stop()
```

The API layer validates the 1-5 letter format (PLAN.md §8) before calling `add_ticker`.
