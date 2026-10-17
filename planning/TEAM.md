# FinAlly Agent Team — Shared Contract

All agents share one working tree. Each agent owns specific paths and edits only those.
Need a change in someone else's area? Send that owner a message (SendMessage, by name).
Do not commit to git; the lead commits at the end.

## Team and Ownership

| Name | Role | Owns |
|------|------|------|
| `database` | Database Engineer | `backend/app/db/`, `backend/tests/db/` |
| `backend` | Backend API Engineer | `backend/app/main.py`, `backend/app/services.py`, `backend/app/api/`, `backend/tests/api/`, `backend/pyproject.toml` |
| `llm` | LLM Engineer | `backend/app/llm/`, `backend/tests/llm/` |
| `frontend` | Frontend Engineer | `frontend/` |
| `devops` | DevOps Engineer | `Dockerfile`, `.dockerignore`, `docker-compose.yml`, `scripts/`, `.env.example`, root `README.md` |
| `tester` | Integration Tester | `test/` |

`backend/app/market/` is complete. Treat it as read-only (see `MARKET_DATA_SUMMARY.md`).
Dependencies: only `backend` edits `pyproject.toml`. Ask `backend` to `uv add` anything you need.

## Module Contracts (Python)

### `app.db` (database)
Stdlib `sqlite3`. Default `user_id="default"` on every function. Timestamps are ISO-8601 UTC strings.

```python
get_db_path() -> Path                      # DB_PATH env or <project_root>/db/finally.db
init_db() -> None                          # create schema + seed if missing; idempotent
get_cash() -> float
set_cash(amount: float) -> None
list_watchlist() -> list[str]              # ordered by added_at
add_watchlist(ticker: str) -> bool         # False if already present
remove_watchlist(ticker: str) -> bool      # False if absent
list_positions() -> list[dict]             # {ticker, quantity, avg_cost, updated_at}
get_position(ticker: str) -> dict | None
save_position(ticker: str, quantity: float, avg_cost: float) -> None  # deletes row when quantity == 0
record_trade(ticker: str, side: str, quantity: float, price: float) -> dict
record_snapshot(total_value: float) -> None
list_snapshots() -> list[dict]             # {total_value, recorded_at}, oldest first
add_chat_message(role: str, content: str, actions: list | None) -> None
recent_chat_messages(limit: int = 20) -> list[dict]  # {role, content, actions, created_at}, oldest first
```
Also `transaction()`, a context manager, so a trade's cash, position and trade row commit together.

### `app.services` (backend)
Business logic shared by the REST routes and the LLM. Raises `ValueError(message)` on any validation failure.

```python
class AppState: cache: PriceCache; source: MarketDataSource
async execute_trade(state, ticker: str, side: str, quantity: float) -> dict   # {ticker, side, quantity, price}
async add_ticker(state, ticker: str) -> None        # validates 1-5 letters, starts tracking
async remove_ticker(state, ticker: str) -> None     # stops tracking unless a position is held
portfolio_summary(state) -> dict              # same shape as GET /api/portfolio
watchlist_with_prices(state) -> list[dict]    # same shape as GET /api/watchlist
```

### `app.llm` (llm)
```python
async def run_chat(state, message: str) -> dict   # {"message": str, "actions": [...]}, PLAN.md section 9
```
It uses `app.services` to execute actions and `app.db` for chat history. `backend` mounts it at `POST /api/chat`.

## REST Response Shapes

`GET /api/portfolio`
```json
{"cash": 10000.0, "total_value": 10000.0, "unrealized_pnl": 0.0,
 "positions": [{"ticker": "AAPL", "quantity": 10, "avg_cost": 190.0, "current_price": 191.2,
                "market_value": 1912.0, "unrealized_pnl": 12.0, "pnl_pct": 0.63}]}
```
`POST /api/portfolio/trade` `{ticker, quantity, side}` returns `{"trade": {...}, "portfolio": {...}}`. Errors return 400 `{"detail": "..."}`.
`GET /api/portfolio/history` returns `[{"total_value": 10000.0, "recorded_at": "..."}]`.
`GET /api/watchlist` returns `[{"ticker": "AAPL", "price": 190.1, "change_pct": 0.4, "direction": "up"}]` (price is null if not yet priced).
`POST /api/watchlist` `{ticker}` returns the updated watchlist list. `DELETE /api/watchlist/{ticker}` does the same.
`POST /api/chat` `{message}` returns `{message, actions}`.
`GET /api/health` returns `{"status": "ok"}`.
SSE: `GET /api/stream/prices`, already built.

## Static Serving and Build
- Frontend: Next.js static export (`output: 'export'`) to `frontend/out/`. API base is `process.env.NEXT_PUBLIC_API_BASE ?? ""`.
- Backend serves static files from the `STATIC_DIR` env var (default `backend/static`) when that directory exists, mounted last at `/`.
- Docker: frontend build output is copied to `/app/static`, with `STATIC_DIR=/app/static` and `DB_PATH=/app/db/finally.db`.
- Dev CORS: allow `http://localhost:3000` only.

## Stable Test Selectors (frontend owns, tester relies on)
`data-testid`: `header-total-value`, `header-cash`, `connection-status` (attribute `data-status` = connected|reconnecting|disconnected),
`watchlist-row-{TICKER}`, `price-{TICKER}`, `watchlist-add-input`, `watchlist-add-button`, `watchlist-remove-{TICKER}`,
`trade-ticker`, `trade-quantity`, `trade-buy`, `trade-sell`, `trade-error`,
`positions-table`, `position-row-{TICKER}`, `heatmap`, `heatmap-cell-{TICKER}`, `pnl-chart`, `main-chart`,
`chat-input`, `chat-send`, `chat-message-user`, `chat-message-assistant`, `chat-action`, `chat-loading`.

## Status Log
Each agent appends one line here when a milestone is ready, e.g. `- [backend] REST API ready on uv run uvicorn app.main:app`.
- [database] app.db ready: `from app import db`; `db.init_db()` in lifespan; `with db.transaction():` groups repo calls atomically (22 tests in backend/tests/db)
- [backend] REST API + SSE ready: `cd backend && uv run uvicorn app.main:app --port 8000` (dev: add `LLM_MOCK=true`; serves STATIC_DIR, default backend/static, at /). 27 API tests pass.
- [llm] app.llm.run_chat ready (await run_chat(state, message)); mock + real Cerebras smoke test pass; tests in backend/tests/llm
- [frontend] UI ready: `cd frontend && npm ci && npm run build` writes the static export to `frontend/out/` (copy to STATIC_DIR). `npm test` runs 16 Vitest tests. All TEAM.md data-testids present; verified end to end against the backend on :8001 with STATIC_DIR=frontend/out.
- [devops] Docker image `finally` builds and runs: `scripts/start_windows.ps1` / `start_mac.sh` (or `docker compose up --build`); verified /api/health, / UI, /api/stream/prices, DB in finally-data volume. Startup takes ~7s.
- [tester] E2E suite passes 9/9 in Docker: `docker compose -f test/docker-compose.test.yml up --build --abort-on-container-exit --exit-code-from playwright` (Playwright 1.64.1, LLM_MOCK=true, fresh tmpfs DB). Covers fresh start, watchlist add/remove, buy/sell, heatmap/P&L chart, mocked chat, SSE reconnect.
