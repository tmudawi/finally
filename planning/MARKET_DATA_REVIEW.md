# Market Data Backend — Code Review

**Date:** 2026-10-16
**Commit:** `9fdba14` (branch `claude/issue-4-20261017-0203`)
**Scope:** `backend/app/market/` (9 modules, 375 statements) and `backend/tests/market/` (7 test files), reviewed against `PLAN.md` §6, §8 and §12 and the other docs in `planning/`.

## 1. Test Results

| Check | Result |
|---|---|
| `uv run --extra dev pytest` | **87 passed**, 0 failed (2.0 s) |
| Coverage | **98%**. Uncovered: `simulator.py` 149, 268-269; `stream.py` 26-48 (the route handler), 86-87 |
| `ruff check app/ tests/` | All checks passed |
| `ruff format --check` | 5 test files would be reformatted |
| Warnings | 87 × `DeprecationWarning: asyncio.DefaultEventLoopPolicy` from `tests/conftest.py` |

Environment: Python 3.14.8 locally (the Dockerfile plan targets 3.12), fastapi 0.128.7, massive 2.2.0, numpy 2.4.2, pytest 9.0.2, pytest-asyncio 1.3.0.

The suite is green, but it does not exercise the Massive path with real SDK objects. Finding 1 shows the Massive source does not work against the real SDK.

## 2. Overall Assessment

The structure is sound. There is an abstract `MarketDataSource`, two implementations, a single shared `PriceCache` with a version counter, and an SSE generator that sends only when the cache changes. The simulator is the primary path, and it works and matches PLAN.md:
- correlated GBM;
- seed prices;
- random prices for unknown tickers;
- the cache is seeded immediately on `start`/`add_ticker`.

The last commit lined the SSE payload up with PLAN.md §6: `price`, `prev_price`, `change_pct` against a reference price, ISO `timestamp` and `direction`.

There is one high-severity bug on the Massive path and two medium ones. The rest are small.

## 3. Findings

Each finding was reproduced with a probe script against the real code. The probe script is in the session scratchpad, not in the repo. Its outputs are quoted below.

### High

**1. Massive source produces no prices with the real SDK** (`massive_client.py:110-112`)

The code reads `snap.last_trade.timestamp`, but the SDK's `LastTrade` model has no `timestamp` field. Its time is `sip_timestamp`, in **nanoseconds** (see `massive/rest/models/trades.py` and `MASSIVE_API.md` §4.1). Every snapshot raises `AttributeError` and is skipped. Probe with `TickerSnapshot.from_dict(...)` and a real-shaped payload:

```
Skipping snapshot for AAPL: 'LastTrade' object has no attribute 'timestamp'
1.  real TickerSnapshot -> cache AAPL: None
1b. start() with real-shaped data: started = True cache size = 0
```

The fail-fast startup check does not catch this either: the first poll "succeeds", the app starts, and no prices ever appear. The tests miss it because `MagicMock` snapshots accept any attribute.

`MARKET_DATA_DESIGN.md` §1 (item 1) already identified this, and its build-order item 1 (§17) is still unchecked.

Fix:
- Read `last_trade.sip_timestamp / 1e9`, falling back to `snap.updated`, then `time.time()`.
- When `last_trade` is `None`, use the price fallback chain from `MASSIVE_API.md` §4.1: `last_trade.price` → `min.close` → `day.close` → `prev_day.close`.
- Build the test fixtures with `TickerSnapshot.from_dict(...)` instead of `MagicMock`.
- Optionally, make `start()` raise if the first poll put zero tickers in the cache.

### Medium

**2. Removed ticker reappears in the cache after an in-flight Massive poll** (`massive_client.py:106-118`)

`_poll_once` awaits `to_thread(self._fetch_snapshots)`. If `remove_ticker` runs during that await, the stale response writes the removed ticker back into the cache. It is then never updated or removed, and SSE keeps sending it forever. Probe:

```
2. after remove during in-flight poll: tickers = ['AAPL'] cache has MSFT = True
```

Fix: skip snapshots whose ticker is not in `self._tickers` when writing to the cache. The simulator is not affected, because its `step()` is synchronous.

**3. `create_stream_router` registers on a module-level router** (`stream.py:17, 26`)

`router` is a module global, and every call to `create_stream_router` adds another `/prices` route to the same object and returns it. Probe:

```
3. create_stream_router twice: same object = True routes = ['/api/stream/prices', '/api/stream/prices']
```

The first registration wins, so a second app or test that uses a new cache is served from the first cache. This is harmless with one app, but it is a trap for the API-route tests that come next.

Fix: create the `APIRouter` inside the factory.

### Low

**4. Random events dominate the simulated price action** (`simulator.py:54, 105`, `event_probability=0.001`)

Probe, 1 simulated hour with 10 tickers:

```
flat ticks after cent rounding = 19%, jump events = 71
per-tick diffusion sd = 0.008786%; 1h diffusion sd ~ 0.75%
```

Each ticker gets about 7 jumps of 2-5% per hour, while normal GBM drift moves it only about 0.75%. Charts will look like step functions rather than "occasional" drama (PLAN.md §6).

Fix: lower `event_probability` to about 0.0001, which gives about 7 events per hour across the watchlist. Optionally, add a time-scale factor to `dt` for livelier diffusion, as `MARKET_SIMULATOR.md` §2 does with `time_scale=60`.

**5. Ticker normalization is inconsistent between sources**

`MassiveDataSource.add_ticker` uppercases and strips the symbol. `SimulatorDataSource` does not, so `add_ticker("aapl")` tracks a separate `aapl` at a random price instead of AAPL's seed. PLAN.md §8 puts uppercasing in the API layer, so this is fine if the routes do it. Making both sources behave the same (or neither) would remove the surprise.

**6. Massive cannot reject unknown tickers or price new ones immediately**

PLAN.md §6/§8 says a Massive watchlist add returns HTTP 400 when the ticker has no data, and trades need a cached price. The interface has no validation hook, and `add_ticker` only appends: no price appears until the next poll (up to 5 s), and an unknown ticker is silently never priced.

Fix: have `MassiveDataSource.add_ticker` fetch that one ticker's snapshot immediately, write it to the cache, and raise a domain error (e.g. `UnknownTickerError`) when the snapshot is empty. The route maps that error to 400.

**7. Fail-fast is skipped when the startup ticker list is empty** (`massive_client.py:100`)

`_poll_once` returns early with no tickers, so a bad key is not detected at startup. The default seed has 10 tickers, so this only matters if the user empties the watchlist and restarts.

**8. SSE generator swallows `CancelledError`** (`stream.py:86-87`)

Catching cancellation without re-raising it ends the generator normally. Under anyio/Starlette this works today, but re-raising (or dropping the `except`) is the correct pattern.

**9. Test gaps**
- There are no statistical tests of the GBM math. PLAN.md §12 asks for "GBM math is correct", but no test checks per-tick volatility or the correlation structure. The simulator uses the global `np.random`/`random`, so it cannot be seeded per instance for deterministic tests.
- Several tests assert little:
  - `test_exception_resilience` never injects an exception;
  - `test_custom_event_probability` asserts nothing;
  - `test_prices_rounded_to_two_decimals` is a string check.
- Both sources are never run through one shared contract test.
- The SSE route handler (`stream.py` 26-48) is not covered. Use a short live uvicorn smoke test: Starlette's `TestClient` hangs on infinite streams.

**10. Housekeeping**
- `tests/conftest.py`: the `event_loop_policy` fixture is unnecessary under pytest-asyncio 1.x and causes all 87 deprecation warnings. Delete it.
- Run `ruff format` on the 5 test files.
- `PriceUpdate.to_dict()` and `change_percent` are now used only by the demo and tests, since SSE uses `to_sse_dict()`. Keep them or remove them, but the two serializers should not drift.
- The installed FastAPI (0.128.7) has no `fastapi.sse` module. The hand-written `StreamingResponse` SSE is fine. Upgrading FastAPI later would allow its native `EventSourceResponse` with built-in keepalive pings.

## 4. Documentation Consistency

| Doc | Issue |
|---|---|
| `PLAN.md` §6 | Links to `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md` and `MASSIVE_API.md` as the "detailed design". The first two describe a different code shape (`SimulatorSource`/`GBMEngine`, sector-factor shocks, `time_scale`) from what was built (`SimulatorDataSource`/`GBMSimulator`, Cholesky). |
| `MARKET_DATA_SUMMARY.md` | Says "73 tests, all passing", "all issues resolved", and a Massive coverage of 56%. Now there are 87 tests, coverage is 100%, and finding 1 is open. |
| `MARKET_DATA_DESIGN.md` | Matches the code and correctly lists finding 1, but its build-order checklist (§17) is not done. |
| `MARKET_DATA_DESIGN_GREENFIELD.md` | An alternative design not used by the code; it is reference only. |
| `backend/CLAUDE.md` | Accurate for the current API. |

Recommendation: point PLAN.md §6 at `MARKET_DATA_SUMMARY.md` + `MARKET_DATA_DESIGN.md` as the source of truth, and move `MARKET_INTERFACE.md`, `MARKET_SIMULATOR.md` and `MARKET_DATA_DESIGN_GREENFIELD.md` to `planning/archive/`. Update the summary's test count and status.

## 5. PLAN.md Conformance

| Requirement | Status |
|---|---|
| One interface, two sources, env-var selection | Met |
| Simulator: GBM, ~500 ms, correlated, events, seed prices, unknown tickers $50-300 | Met (events too frequent, finding 4) |
| Shared cache with latest/previous price, timestamp, reference price | Met |
| `change_pct` vs seed (simulator) / previous close (Massive) | Met |
| SSE `GET /api/stream/prices`, send on change, PLAN payload shape, `retry` | Met |
| Massive: one snapshot call per poll, ~5 s, fail fast at startup | Partly met: polling works, but parsing fails (finding 1), and fail-fast does not catch it |
| Massive: reject unknown ticker on watchlist add (400) | Not met (finding 6) |
| Tracked tickers = watchlist ∪ positions | Not in scope here; it belongs to the route/portfolio layer, which is not built yet |

## 6. Recommended Order of Fixes

1. Finding 1: Massive parsing, plus real-model test fixtures.
2. Finding 3: router inside the factory. This is a one-line move, and it must happen before the API routes are built.
3. Finding 2: filter stale snapshots.
4. Finding 4: lower `event_probability`.
5. Finding 10: housekeeping (conftest, `ruff format`). Then the doc updates in §4.
6. Findings 5-9 as the API layer is built.

The simulator path, which is the default, is usable now. Findings 1, 2 and 6 matter only when `MASSIVE_API_KEY` is set.

## 7. Resolution (2026-10-16, branch `market-data-review-fixes`)

All findings are fixed. Result: **116 tests pass** (5 repeated runs, all green), **99% coverage**, `ruff check` and `ruff format --check` clean, **0 warnings**.

| # | Fix | Evidence |
|---|---|---|
| 1 | New `parse_snapshot()`:<br>• timestamp from `last_trade.sip_timestamp / 1e9`, falling back to `updated`, then `time.time()`<br>• price from `last_trade.price`, falling back to `min.close`, `day.close`, `prev_day.close`<br>`start()` also raises if the first poll prices none of the tracked tickers. | `test_massive.py` builds real `TickerSnapshot.from_dict(...)` objects: `TestParseSnapshot`, `test_timestamp_converted_from_nanoseconds`, `test_start_with_no_prices_raises` |
| 2 | `_store()` writes only snapshots whose ticker is still tracked. `PriceCache.remove()` now bumps `version` (gap found in `MARKET_DATA_DESIGN.md` §1 item 4; proven: version stayed 1 → 1 on remove). SSE sends an empty map after the last removal. | `test_ticker_removed_during_poll_is_not_written_back`, `test_remove_bumps_version_only_when_present`, `test_removal_is_sent` |
| 3 | `create_stream_router` builds a new `APIRouter` per call | `test_each_router_is_independent` |
| 4 | `event_probability` default 0.001 → 0.0001 (`GBMSimulator` and `SimulatorDataSource`). Re-measured: 10, 8 and 12 jumps per simulated hour across 10 tickers (seeds 1-3), down from 71. No time-scale change; the diffusion stays real-time. | `test_events_move_price_2_to_5_percent` |
| 5 | `normalize_ticker()` in `interface.py`, used by both sources in `start/add/remove` | `test_tickers_are_normalized`, contract tests with lowercase input |
| 6 | `UnknownTickerError` in `interface.py`, exported from `app.market`. After `start`, `MassiveDataSource.add_ticker` fetches the ticker's snapshot at once, caches it, and raises when it has no data. The simulator now treats re-adding a ticker as a no-op too: the contract test caught it rewriting the cache. | `test_add_after_start_prices_immediately`, `test_add_unknown_ticker_raises`, `test_add_existing_ticker_is_noop[simulator]` |
| 7 | With no tickers, the first poll checks the key against `SPY`. The probe ticker is not cached. | `test_start_with_no_tickers_still_validates_key` |
| 8 | FastAPI upgraded 0.128.7 → 0.143.3. The SSE route uses native `EventSourceResponse`/`ServerSentEvent`, which handles keepalive pings and disconnects; the hand-written `CancelledError` handling is gone. | `test_live_endpoint_streams_prices` (real uvicorn socket) |
| 9 | Simulator uses a seeded `numpy` `Generator`. New tests:<br>• GBM statistics: per-tick sd within 5% of `sigma*sqrt(dt)` for 5 tickers; correlations 0.6/0.5/0.3/0.3 within ±0.05; jump size 2-5%<br>• seeded determinism<br>• `test_contract.py`: both sources<br>Strengthened tests:<br>• `test_exception_resilience` now injects a failing step<br>• `test_custom_event_probability` checks the jump size<br>• the rounding test now compares values instead of strings | `test_simulator.py::TestGBMStatistics`, `test_contract.py` |
| 10 | `tests/conftest.py` deleted (87 warnings gone). `ruff format` applied. The uvicorn smoke test runs with `ws="none"`, removing 2 websockets deprecation warnings. `to_dict()` is kept for the demo. | `pytest` output: no warnings |

Docs (§4):
- PLAN.md §6 now points to `MARKET_DATA_SUMMARY.md`, `MARKET_DATA_DESIGN.md` and `MASSIVE_API.md`.
- Moved to `archive/`:
  - `MARKET_INTERFACE.md`, renamed `MARKET_INTERFACE_v2.md`;
  - `MARKET_SIMULATOR.md`, renamed `MARKET_SIMULATOR_v2.md`;
  - `MARKET_DATA_DESIGN_GREENFIELD.md`.
- Updated:
  - `MARKET_DATA_SUMMARY.md`;
  - `backend/CLAUDE.md`;
  - the `MARKET_DATA_DESIGN.md` §18 checklist.

Still open:
- The Massive source has not been run against the live API, because no paid key is available.
- Wiring into the app (lifespan, routes, tracked tickers = watchlist ∪ positions) belongs to the backend API work.
