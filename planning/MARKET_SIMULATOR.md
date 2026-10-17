# Market Simulator — Approach and Code Structure

The simulator is the default market data source (used whenever `MASSIVE_API_KEY` is empty). It implements the `MarketDataSource` interface from `MARKET_INTERFACE.md`, runs in-process as an asyncio task, and writes to the shared `PriceCache` every 500 ms. It has no external dependencies (standard library only).

## 1. Goals

- Realistic-looking prices: random walk with no negative prices, per-ticker volatility.
- Lively enough for a demo: visible movement every tick, occasional dramatic jumps.
- Correlated: tech stocks tend to move together; the whole market drifts together.
- Any 1-5 letter symbol works (seeded with a random price).
- Deterministic under a fixed seed so it can be unit tested.

## 2. The Math

### Geometric Brownian motion

Each tick, for each ticker:

```
S(t+dt) = S(t) * exp( (mu - sigma^2 / 2) * dt + sigma * sqrt(dt) * Z )
```

- `mu` = annualized drift (default 0.05), `sigma` = annualized volatility (per ticker)
- `Z` = standard normal shock (correlated across tickers, see below)
- The exponential keeps prices strictly positive.

### Time scale

`dt` is measured in trading years: `TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600 = 5,896,800`.

At real speed a 500 ms tick moves AAPL (sigma 0.25) by ~0.007% — too slow to see. A `time_scale` factor (default **60**, i.e. one real second ≈ one trading minute) speeds the clock up:

```
dt = tick_seconds * time_scale / TRADING_SECONDS_PER_YEAR    # 0.5 * 60 / 5,896,800 ≈ 5.1e-6
per-tick sd = sigma * sqrt(dt)                                # AAPL: ≈ 0.056%, TSLA: ≈ 0.124%
```

That gives cent-level moves every tick and moves of a few percent over an hour.

### Correlated shocks

Each tick draws one market factor `M`, one factor per sector `F_s`, and one idiosyncratic `e_i` per ticker, all standard normal:

```
Z_i = sqrt(w_m) * M + sqrt(w_s) * F_sector(i) + sqrt(w_i) * e_i      with w_m + w_s + w_i = 1
```

Because the weights sum to 1, each `Z_i` is still standard normal, so `sigma` keeps its meaning. Defaults `w_m = 0.3, w_s = 0.2, w_i = 0.5` give:

- correlation within a sector = `w_m + w_s` = **0.5**
- correlation across sectors = `w_m` = **0.3**

Sectors: `"tech"` and `"other"`. Unknown tickers go in `"other"`.

### Random events

After the GBM step, with probability `event_prob` (default **0.0001** per ticker per tick) the price jumps by a random 2-5% up or down. With 10-11 tickers at 2 ticks/s that is roughly one event every 5 minutes across the watchlist: dramatic but not chaotic.

### Validation (prototype run, seed 42, 7,200 ticks = 1 hour)

| Check | Designed | Measured |
|---|---|---|
| AAPL per-tick sd (events off) | 0.0564% | 0.0569% |
| TSLA per-tick sd (events off) | 0.124% | 0.124% |
| corr AAPL–MSFT (same sector) | 0.50 | 0.51 |
| corr AAPL–JPM (cross sector) | 0.30 | 0.29 |
| events per hour, 11 tickers, p=0.0001 | ~8 | 12 |

## 3. Seed Parameters

| Ticker | Seed price | Volatility | Sector |
|---|---|---|---|
| AAPL | 190 | 0.25 | tech |
| GOOGL | 175 | 0.28 | tech |
| MSFT | 420 | 0.22 | tech |
| AMZN | 185 | 0.30 | tech |
| TSLA | 250 | 0.55 | tech |
| NVDA | 130 | 0.50 | tech |
| META | 560 | 0.35 | tech |
| NFLX | 700 | 0.35 | tech |
| JPM | 210 | 0.20 | other |
| V | 280 | 0.18 | other |
| *unknown* | uniform 50-300 | 0.30 | other |

All tickers use drift 0.05. The seed price is the **reference price** for `change_pct` (PLAN.md §6), so every ticker starts at 0.00%.

Seed prices reset on each app restart (the simulator keeps no state in the database). Positions keep their `avg_cost`, so P&L after a restart reflects the new random walk — acceptable for a simulation.

## 4. Code Structure

Two classes in `backend/app/market/simulator.py`:

- **`GBMEngine`** — pure, synchronous, no asyncio, no cache. Holds per-ticker params and prices; `step()` advances one tick. Everything random comes from one `random.Random(seed)`, so tests are deterministic.
- **`SimulatorSource(MarketDataSource)`** — thin async wrapper: owns a `GBMEngine`, runs the 500 ms loop, writes into the `PriceCache`.

Keeping the math out of the async wrapper means the engine is tested without event loops or sleeps.

```
backend/app/market/
├── simulator.py         # TickerParams, DEFAULT_PARAMS, GBMEngine, SimulatorSource
└── ...
backend/tests/market/
└── test_simulator.py
```

### `GBMEngine`

```python
import math
import random
from dataclasses import dataclass

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
    """Correlated geometric Brownian motion with random jump events."""

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
        """Start simulating a ticker; returns its current (seed) price."""
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
        """Advance all tickers by one tick and return the new prices."""
        market = self.rng.gauss()
        sector = {s: self.rng.gauss() for s in SECTORS}
        for ticker, p in self.params.items():
            z = (
                self.w_market * market
                + self.w_sector * sector[p.sector]
                + self.w_idio * self.rng.gauss()
            )
            growth = (p.drift - 0.5 * p.volatility**2) * self.dt + p.volatility * math.sqrt(self.dt) * z
            price = self.prices[ticker] * math.exp(growth)
            if self.rng.random() < self.event_prob:
                price *= 1 + self.rng.choice((-1, 1)) * self.rng.uniform(0.02, 0.05)
            self.prices[ticker] = price
        return dict(self.prices)
```

Prices are kept at full precision inside the engine; the `PriceCache` rounds to cents. Rounding inside the engine would distort the walk.

### `SimulatorSource`

```python
import asyncio
import contextlib
from collections.abc import Iterable

from .base import MarketDataSource
from .cache import PriceCache


class SimulatorSource(MarketDataSource):
    """Market data source backed by GBMEngine, ticking every 500 ms."""

    def __init__(self, cache: PriceCache, engine: GBMEngine | None = None) -> None:
        super().__init__(cache)
        self.engine = engine or GBMEngine()
        self._task: asyncio.Task | None = None

    @property
    def tickers(self) -> set[str]:
        return set(self.engine.params)

    async def start(self, tickers: Iterable[str]) -> None:
        for ticker in tickers:
            await self.add_ticker(ticker)
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

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

Notes:

- `add_ticker` writes the seed price straight into the cache, satisfying the interface guarantee that a ticker can be traded as soon as it is added.
- The first `cache.update` for a ticker sets `reference_price` = seed price; later updates carry it over.
- `step()` is pure CPU over ~10-20 tickers (microseconds), so it runs directly on the event loop without `to_thread`.
- The simulator never raises `UnknownTickerError`; symbol-format validation (1-5 uppercase letters) happens in the API layer before `add_ticker` is called.

## 5. Tunables

All are constructor arguments with sensible defaults — no extra environment variables.

| Parameter | Default | Effect |
|---|---|---|
| `tick_seconds` | 0.5 | Real time between ticks |
| `time_scale` | 60 | Simulated trading seconds per real second; higher = more movement |
| `event_prob` | 0.0001 | Jump probability per ticker per tick |
| `weights` | (0.3, 0.2, 0.5) | Market / sector / idiosyncratic variance shares (must sum to 1) |
| `seed` | None | Fix for deterministic tests |

## 6. Tests (`backend/tests/market/test_simulator.py`)

Engine (no asyncio needed):

- `add` returns the seed price for a default ticker; an unknown ticker gets a price in [50, 300]; adding twice keeps the same price.
- `remove` drops the ticker from `params`, `prices` and future `step()` output.
- `step` keeps every price positive over 10,000 ticks.
- Same seed → identical price paths; different seeds → different paths.
- With `event_prob=0`, the sample sd of per-tick log returns over ~20,000 ticks is within 5% of `sigma * sqrt(dt)`.
- With `event_prob=0`, correlation of log returns AAPL–MSFT ≈ 0.5 and AAPL–JPM ≈ 0.3 (tolerance ±0.05).
- With `event_prob=1`, every tick's absolute log return is above 1.5% (a 2% jump minus at most a few tenths of a percent of GBM noise).

Source (async, `pytest-asyncio` or `anyio`):

- `start(["AAPL", "PYPL"])` puts both in the cache immediately, each with `change_pct == 0`.
- After a few ticks (pass `GBMEngine(tick_seconds=0.01)`), cache `version` has advanced and `prev_price` differs from the seed.
- `remove_ticker` removes the ticker from the cache and it does not reappear on later ticks.
- Conformance: the shared `MarketDataSource` contract test from `MARKET_INTERFACE.md` passes.
