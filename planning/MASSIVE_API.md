# Massive API (formerly Polygon.io) — Stock Price Reference

Reference for retrieving real-time/delayed and end-of-day prices for multiple US stock tickers from Massive. Researched 2026-10-14 against the live API, the official docs, and the official Python client source.

Polygon.io rebranded as **Massive** (massive.com). The REST API is unchanged apart from the host: `https://api.massive.com` (the old `https://api.polygon.io` still works for a transition period).

## 1. Plans and What They Unlock

| Plan | Price | Calls | Freshness | Snapshots / last trade |
|---|---|---|---|---|
| Stocks Basic (free) | $0 | 5 / minute | End of day | **No** |
| Stocks Starter | $29/mo | Unlimited | 15-min delayed | Yes |
| Stocks Developer | $79/mo | Unlimited | 15-min delayed | Yes |
| Stocks Advanced | $199/mo | Unlimited | Real-time | Yes |

**Verified on 2026-10-14 with a free (Basic) key:**

| Endpoint | Result |
|---|---|
| `GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=...` | **403 NOT_AUTHORIZED** |
| `GET /v2/last/trade/AAPL` | **403 NOT_AUTHORIZED** |
| `GET /v2/aggs/ticker/AAPL/prev` | 200 OK |
| `GET /v2/aggs/grouped/locale/us/market/stocks/2026-10-13` | 200 OK (12,584 tickers) |

The 403 body is:

```json
{"status": "NOT_AUTHORIZED", "request_id": "...", "message": "You are not entitled to this data. Please upgrade your plan at https://massive.com/pricing"}
```

Conclusion: **live (intraday) multi-ticker prices need Starter or above.** The free tier only supports end-of-day data (previous close, grouped daily bars, historical aggregates). Exceeding the free 5 calls/min returns HTTP 429.

## 2. Authentication

Pass the key either as a query parameter or a bearer header:

```bash
curl "https://api.massive.com/v2/aggs/ticker/AAPL/prev?apiKey=$MASSIVE_API_KEY"
curl -H "Authorization: Bearer $MASSIVE_API_KEY" "https://api.massive.com/v2/aggs/ticker/AAPL/prev"
```

## 3. Official Python Client

- Package: `massive` (latest 2.8.0, May 2026, Python >= 3.9). Install with `uv add massive`.
- Import: `from massive import RESTClient`
- The client is **synchronous** (urllib3). In async code, call it via `asyncio.to_thread`.
- Built-in retries: 3 retries with backoff on 413, 429, 499, 500, 502, 503, 504.
- Non-200 responses raise `massive.exceptions.BadResponse`; a missing key raises `massive.exceptions.AuthError`.
- The constructor's default `api_key` is read from `MASSIVE_API_KEY` **at import time**. Pass the key explicitly to avoid surprises.

```python
import os
from massive import RESTClient

client = RESTClient(api_key=os.environ["MASSIVE_API_KEY"])
```

Model objects use snake_case attributes (the raw JSON uses short keys like `c`, `p`, `prevDay`).

## 4. Real-Time / Delayed Prices (Starter+)

### 4.1 Multi-ticker snapshot — the primary endpoint for FinAlly

`GET /v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,MSFT,NVDA`

One call returns the latest trade, quote, minute bar, today's bar and previous day's bar for every requested ticker. Unknown tickers are simply absent from the result. Snapshot data is cleared at 3:30 AM ET and repopulates from ~4:00 AM ET.

Query parameters: `tickers` (comma-separated, case-sensitive; empty = whole market), `include_otc` (default false).

Response (abridged):

```json
{
  "status": "OK",
  "count": 1,
  "tickers": [
    {
      "ticker": "AAPL",
      "day":     {"o": 119.62, "h": 120.53, "l": 118.81, "c": 120.4229, "v": 28727868, "vw": 119.725},
      "prevDay": {"o": 117.19, "h": 119.63, "l": 116.44, "c": 119.49, "v": 110597265, "vw": 118.4998},
      "min":     {"o": 120.435, "h": 120.468, "l": 120.37, "c": 120.4201, "v": 270796, "t": 1684428720000},
      "lastTrade": {"p": 120.47, "s": 236, "t": 1605195918306274000, "x": 10},
      "lastQuote": {"p": 120.46, "s": 8, "P": 120.47, "S": 4, "t": 1605195918507251700},
      "todaysChange": 0.98,
      "todaysChangePerc": 0.82,
      "updated": 1605195918306274000
    }
  ]
}
```

Timestamps: `lastTrade.t`, `lastQuote.t`, `updated` are **nanoseconds**; bar `t` values are **milliseconds** since epoch.

Python client:

```python
snapshots = client.get_snapshot_all("stocks", tickers=["AAPL", "MSFT", "NVDA"])
for s in snapshots:
    price = s.last_trade.price if s.last_trade else s.day.close
    print(s.ticker, price, s.prev_day.close, s.todays_change_percent)
```

`TickerSnapshot` fields: `ticker`, `day: Agg`, `prev_day: Agg`, `min: MinuteSnapshot`, `last_trade: LastTrade`, `last_quote: LastQuote`, `todays_change`, `todays_change_percent`, `updated`. `Agg` has `open, high, low, close, volume, vwap, timestamp`. `LastTrade` has `price, size, sip_timestamp, exchange, ...`.

Any of these sub-objects can be `None` (e.g. `last_trade` before the first trade of the day, or when the plan excludes trades), so pick the price with a fallback chain: `last_trade.price` → `min.close` → `day.close` → `prev_day.close`.

Raw HTTP equivalent with `httpx`:

```python
import httpx

resp = httpx.get(
    "https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers",
    params={"tickers": "AAPL,MSFT,NVDA", "apiKey": api_key},
    timeout=10,
)
resp.raise_for_status()
for t in resp.json()["tickers"]:
    print(t["ticker"], t["lastTrade"]["p"], t["prevDay"]["c"])
```

### 4.2 Single-ticker snapshot

`GET /v2/snapshot/locale/us/markets/stocks/tickers/{ticker}` — same shape, wrapped in `{"ticker": {...}}`. Returns 404 for an unknown ticker.

```python
s = client.get_snapshot_ticker("stocks", "AAPL")
```

### 4.3 Unified snapshot (v3)

`GET /v3/snapshot?ticker.any_of=AAPL,MSFT&limit=250` — up to 250 tickers across asset classes. Each result has `session` (`open, high, low, close, previous_close, change, change_percent`), `last_trade`, `last_quote`, `market_status`. Unknown tickers come back as entries with `"error": "NOT_FOUND"`.

```python
for s in client.list_universal_snapshots(ticker_any_of=["AAPL", "MSFT"]):
    print(s.ticker, s.session.close, s.session.previous_close)
```

Useful if we ever add crypto/FX; for US stocks the v2 snapshot is simpler.

### 4.4 Last trade

`GET /v2/last/trade/{ticker}` — one ticker per call; `results.p` is the price. Not useful for polling many tickers.

```python
trade = client.get_last_trade("AAPL")
print(trade.price, trade.sip_timestamp)
```

## 5. End-of-Day Prices (all plans, including free)

### 5.1 Previous close — one ticker

`GET /v2/aggs/ticker/{ticker}/prev?adjusted=true`

Live response (2026-10-14, free key):

```json
{"ticker": "AAPL", "results": [{"T": "AAPL", "o": 344.605, "h": 347.09, "l": 341.72, "c": 342.96, "v": 50060849, "vw": 343.9921, "t": 1792008000000, "n": 896600}], "status": "OK"}
```

```python
prev = client.get_previous_close_agg("AAPL")
print(prev[0].close)  # client returns a list of PreviousCloseAgg
```

### 5.2 Grouped daily — every ticker for one date, one call

`GET /v2/aggs/grouped/locale/us/market/stocks/{YYYY-MM-DD}?adjusted=true`

The most efficient multi-ticker EOD call: one request returns OHLCV for the whole US market (~12,500 rows). Filter client-side.

```python
bars = client.get_grouped_daily_aggs("2026-10-13")
wanted = {"AAPL", "MSFT", "NVDA"}
closes = {b.ticker: b.close for b in bars if b.ticker in wanted}
```

Non-trading days return an empty result set — step back to the previous weekday.

### 5.3 Daily open/close — one ticker, one date

`GET /v1/open-close/{ticker}/{date}` — adds `preMarket` and `afterHours` prices.

```python
oc = client.get_daily_open_close_agg("AAPL", "2026-10-13")
print(oc.open, oc.close, oc.after_hours)
```

### 5.4 Historical aggregates (bars)

`GET /v2/aggs/ticker/{ticker}/range/{multiplier}/{timespan}/{from}/{to}`

```python
bars = client.get_aggs("AAPL", 1, "day", "2026-09-01", "2026-10-13")
for b in bars:
    print(b.timestamp, b.close)
```

`list_aggs(...)` is the paginating iterator variant. FinAlly does not need history (charts accumulate from SSE).

## 6. Errors and Limits

| Situation | Response | Client behavior |
|---|---|---|
| Endpoint not in plan | 403 `NOT_AUTHORIZED` | raises `BadResponse` |
| Bad / missing key | 401 | raises `BadResponse` / `AuthError` |
| Rate limited (free: 5/min) | 429 | retried, then `BadResponse` |
| Unknown ticker (multi snapshot) | omitted from `tickers` | — |
| Unknown ticker (single snapshot) | 404 | raises `BadResponse` |

## 7. Recommendations for FinAlly

1. **Polling:** one `get_snapshot_all("stocks", tickers=[...tracked])` per poll covers every tracked ticker. Paid plans have unlimited calls; poll every 2-5 s (data is 15-min delayed on Starter/Developer anyway).
2. **Price:** `last_trade.price`, with the fallback chain in 4.1.
3. **Reference price for `change_pct`:** `prev_day.close`.
4. **Ticker validation on watchlist add:** request a snapshot for just that ticker; empty result means unknown → HTTP 400.
5. **Free keys:** the snapshot endpoint returns 403, so live prices are impossible. The simulator is the right default; a free key could at most provide static EOD prices via grouped daily (one call per day). See `MARKET_INTERFACE.md` for how this is handled.
6. Wrap the synchronous client in `asyncio.to_thread` so polling never blocks the event loop.

## Sources

- Massive REST docs: https://massive.com/docs/rest/stocks
- Pricing: https://massive.com/pricing
- Python client: https://github.com/massive-com/client-python (PyPI `massive` 2.8.0)
- Live calls made through the Massive MCP server on 2026-10-14
