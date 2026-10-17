"""Watchlist routes."""

DEFAULT = ["AAPL", "GOOGL", "MSFT", "AMZN", "TSLA", "NVDA", "META", "JPM", "V", "NFLX"]


def tickers(rows):
    return [r["ticker"] for r in rows]


def test_default_watchlist_is_priced(client):
    rows = client.get("/api/watchlist").json()
    assert tickers(rows) == DEFAULT
    assert all(r["price"] > 0 for r in rows)
    assert set(rows[0]) == {"ticker", "price", "change_pct", "direction"}


def test_add_ticker_uppercases_and_prices(client):
    rows = client.post("/api/watchlist", json={"ticker": " pypl "}).json()
    assert tickers(rows)[-1] == "PYPL"
    assert rows[-1]["price"] > 0


def test_add_existing_is_noop(client):
    rows = client.post("/api/watchlist", json={"ticker": "AAPL"}).json()
    assert tickers(rows) == DEFAULT


def test_add_invalid_symbol_400(client):
    for bad in ["TOOLONG", "AB1", ""]:
        resp = client.post("/api/watchlist", json={"ticker": bad})
        assert resp.status_code == 400
        assert "Invalid ticker" in resp.json()["detail"]


def test_remove_ticker_stops_tracking(client):
    rows = client.delete("/api/watchlist/TSLA").json()
    assert "TSLA" not in tickers(rows)
    assert client.app.state.cache.get("TSLA") is None


def test_remove_held_ticker_keeps_price(client):
    client.post("/api/portfolio/trade", json={"ticker": "TSLA", "quantity": 1, "side": "buy"})
    client.delete("/api/watchlist/TSLA")
    assert client.app.state.cache.get("TSLA") is not None
    position = client.get("/api/portfolio").json()["positions"][0]
    assert position["ticker"] == "TSLA"
