# Change Review

## Review of uncommitted changes (2026-10-13)

**Scope**: `git diff` against HEAD (`6b568a9`). One file changed: `planning/PLAN.md`, which gains a new section "13. Review Feedback" (36 lines, documentation only). No code changes.

### Summary

The new section reviews the plan in three groups: inconsistencies (5), gaps/questions (13), simplifications (6). It is additive and does not change any existing spec text.

### Findings

1. **Item 4 is self-contradictory**: it says "Pick one" charting library, then recommends Lightweight Charts *and* optionally Recharts `Treemap`. Suggest stating a single choice for line charts (Lightweight Charts) and a plain div-based treemap for the heatmap, so only one charting dependency is added.
2. **Item 22 does not commit to a recommendation**: it offers two snapshot strategies. Pick one (keep the 30s cadence as specified) to avoid agents debating it.
3. **Item 15 is unverified**: whether the Massive free tier includes the snapshot endpoint is stated as a question. Someone should check this before the market data agent starts.
4. **Item 24 assumes the user's OS**: fine for this user, but the plan targets course students on any OS. The mac/Windows scripts should probably stay.
5. **Open items are not yet reflected in the spec**: the review section lists decisions, but sections 6-11 are unchanged. Once answered, the decisions should be folded into the relevant sections and section 13 removed or marked resolved, so agents read one source of truth.

### Verdict

Documentation-only change, safe to commit. Resolve findings 1-2 when the plan owner answers the section 13 questions.

## Review of uncommitted changes (2026-10-13, second pass)

**Scope**: `planning/PLAN.md` only (+72 / -20). Section 13 resolutions folded into sections 2 and 4-11; section 13 removed. Documentation only.

### Findings

1. **Massive snapshot endpoint on the free tier is unverified** (Section 6). If the free tier lacks snapshot access, the Massive path fails for free-key users. Verify before the market data agent builds it.
2. **Dev-only CORS weakens the "no CORS" rationale** in the Section 3 table. Acceptable, since it only covers `localhost:3000`, but the table row now holds for production only.
3. **Mock "sell" before any "buy"** returns an error action (no AAPL held). This is fine and useful for testing the error path, but E2E tests must buy first.
4. **Simulator accepts any symbol**, so typos like `APPL` become tracked tickers with random prices. Acceptable for a simulator; noted for awareness.

### Verdict

Consistent with the earlier review recommendations. Safe to commit once finding 1 is answered.

### Resolution (2026-10-13)

- Finding 1 resolved: the simulator is the supported market data path for now. Section 6 marks Massive as secondary, with free-tier snapshot access unverified.
- Dev-mode CORS for `localhost:3000` and the mock keywords "buy", "sell", "watch" confirmed by the plan owner.

## Review of uncommitted changes (2026-10-14)

**Scope**: `git diff` against HEAD (`6b568a9`) plus untracked files. `planning/PLAN.md` (+73 / -20, unchanged since the 2026-10-13 second pass) and three new docs: `planning/MASSIVE_API.md`, `planning/MARKET_INTERFACE.md`, `planning/MARKET_SIMULATOR.md`. Documentation only; no code in the repo.

### Summary

The new docs cover the Massive REST API (verified with live calls), a unified market data interface (`PriceCache`, `MarketDataSource`, `SimulatorSource`, `MassiveSource`, factory, SSE router), and the simulator design (correlated GBM with jump events, validated by a prototype run). They are consistent with PLAN.md sections 6 and 8 on tracked tickers, reference price, SSE payload shape and validation rules.

### Findings

1. **PLAN.md section 6 is now out of date**: it says free-tier snapshot access is "unverified" and gives a 15-second free-tier poll. `MASSIVE_API.md` verified on 2026-10-14 that a free key gets 403 on the snapshot and last-trade endpoints. PLAN.md should say Massive needs the Stocks Starter plan or above, and drop the free-tier poll line.
2. **Simulator seed prices are stale versus the market**: PLAN.md and `MARKET_SIMULATOR.md` seed AAPL at ~$190, while the live previous close on 2026-10-13 was $342.96. This is harmless for a simulator but looks off to anyone who knows current prices. Optional: refresh the seeds.
3. **Simulator restarts break P&L continuity**: prices reset to the seeds on every restart, while `positions.avg_cost` and `portfolio_snapshots` persist. P&L and the P&L chart will jump after a restart. `MARKET_SIMULATOR.md` acknowledges the P&L part; the chart discontinuity should be noted too, or the last cached prices persisted.
4. **Massive polls re-emit unchanged prices**: every poll bumps `PriceCache.version` even when a price did not move, so the SSE stream sends an event with `direction: "flat"`. The frontend must flash only on `up`/`down`, not on every event. Worth stating in PLAN.md section 10.
5. **`stop()` does not await the cancelled task** in either source (`MARKET_INTERFACE.md` section 6, `MARKET_SIMULATOR.md` section 4). Minor: at shutdown a task may log a `CancelledError`. Awaiting the task inside `contextlib.suppress(asyncio.CancelledError)` fixes it.
6. **`SimulatorSource` takes `tick_seconds` and an optional `engine`** that has its own `tick_seconds`; passing an engine built with a different tick makes `dt` disagree with the loop's sleep. Minor: derive the sleep from the engine or drop one parameter.
7. **Most code in the docs is unexecuted**: only the simulator engine was prototyped and run. The `MassiveSource`, cache and SSE snippets are design, so the implementing agent should treat them as a starting point and test them.

### Verdict

Documentation only, safe to commit. Resolve finding 1 in PLAN.md before the market data agent starts. Findings 4-6 are small and can be fixed during implementation.

### Resolution (2026-10-14)

- Findings 5 and 6 fixed in the docs: both sources' `stop()` now awaits the cancelled task under `contextlib.suppress(asyncio.CancelledError)`, and `SimulatorSource` takes only an optional `engine` and sleeps for `engine.tick_seconds`.
- Finding 1 (PLAN.md section 6) left for the plan owner.
- Finding 1 resolved: PLAN.md section 6 now states Massive needs the Stocks Starter plan or above (free key verified 403), drops the free-tier poll interval, and links the three market docs. Section 6 SSE text updated to send only on cache change, with flashes on `up`/`down` only (finding 4). Findings 2-3 accepted as-is (practice project).

## Review of uncommitted changes (2026-10-14, MARKET_DATA_DESIGN.md)

**Scope**: new file `planning/MARKET_DATA_DESIGN.md` (1,183 lines). Other files unchanged since the previous entry. Documentation only; no code in the repo.

### Summary

A consolidated implementation guide for the market data backend: full module code (models, cache, interface, simulator, Massive source, factory, SSE router), integration with routes and lifespan, configuration, 29 tests, build order. The module and test code was built and run in a scratch project: 29 tests pass, and the SSE endpoint was smoke-tested on live uvicorn. The embedded code was copied mechanically from the tested files.

### Findings

1. **Supersedes code in `MARKET_INTERFACE.md` / `MARKET_SIMULATOR.md`**: it uses FastAPI's native `EventSourceResponse`, a base-class `stop()`, an injected Massive client and `normalize_symbol()`. §13 lists every difference. Agents could be confused by two versions of the same code. Suggest making `MARKET_DATA_DESIGN.md` the single implementation reference, keeping the older two for background (math, rationale), and saying so in PLAN.md §6.
2. **Integration snippets (§8.2-§8.3) are untested**: they reference a `db` module that does not exist yet. The doc says so.
3. **Massive is untested against the real API**: no paid key was available. The startup-403 path is tested only with a fake client.
4. **The SSE endpoint cannot be unit-tested with `TestClient`**: reproduced as a hang (an infinite stream never completes). The doc directs testing to `price_events()` plus a live smoke test and the E2E tests.

### Verdict

Documentation only, safe to commit. Consider finding 1 before the backend agent starts.

### Resolution (2026-10-16)

- PR #3 merged a different `planning/MARKET_DATA_DESIGN.md` into `main`, written against the existing `backend/app/market/` code. That version is kept at that path. The design reviewed above was moved to `planning/MARKET_DATA_DESIGN_GREENFIELD.md`.

## Review of branch `market-data-review-fixes` (2026-10-16)

**Scope**:
- Code: `backend/app/market/` (`massive_client.py`, `stream.py`, `simulator.py`, `cache.py`, `interface.py`, `__init__.py`) and `backend/tests/market/`.
- Dependencies: FastAPI upgraded to 0.143.3.
- Docs: three planning docs archived; `PLAN.md`, `MASSIVE_API.md`, `MARKET_DATA_SUMMARY.md`, `MARKET_DATA_DESIGN.md`, `MARKET_DATA_REVIEW.md` and `backend/CLAUDE.md` updated.

### Findings

1. **Every fix is tied to a reproduced problem.** Each finding in `MARKET_DATA_REVIEW.md` was reproduced first, then covered by a test; see its §7.
2. **Behaviour change for consumers:**
   - SSE now sends `{}` when the cache empties;
   - the first event can be `{}` if the cache is empty at connect.
   The frontend must treat each event as the full board.
3. **Behaviour change for routes:** after `start`, Massive `add_ticker` makes a network call and can raise `UnknownTickerError`. The watchlist route must map that to HTTP 400.
4. **FastAPI upgrade:** it pulls in `opentelemetry-api` as a new transitive dependency. It is harmless, but it makes the image slightly larger.
5. **Live tests need a socket:** the live SSE test binds `127.0.0.1` on a random port. It is fine locally and in CI, but it will fail in a sandbox with no networking.

### Verdict

Ready to merge. The Massive source is still untested against the live API.
