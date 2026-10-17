"""Portfolio and trade routes."""

import pytest

from tests.api.conftest import price_of


def trade(client, ticker, quantity, side):
    return client.post(
        "/api/portfolio/trade", json={"ticker": ticker, "quantity": quantity, "side": side}
    )


def test_initial_portfolio(client):
    assert client.get("/api/portfolio").json() == {
        "cash": 10000.0,
        "total_value": 10000.0,
        "unrealized_pnl": 0.0,
        "positions": [],
    }


def test_buy_creates_position(client):
    body = trade(client, "aapl", 2, "buy").json()
    price = body["trade"]["price"]
    assert body["trade"] == {"ticker": "AAPL", "side": "buy", "quantity": 2, "price": price}
    portfolio = body["portfolio"]
    assert portfolio["cash"] == pytest.approx(10000 - 2 * price, abs=0.01)
    position = portfolio["positions"][0]
    assert position["ticker"] == "AAPL"
    assert position["quantity"] == 2
    assert set(position) == {
        "ticker",
        "quantity",
        "avg_cost",
        "current_price",
        "market_value",
        "unrealized_pnl",
        "pnl_pct",
    }


def test_weighted_average_cost(client):
    cache = client.app.state.cache
    cache.update("AAPL", 100.0)
    trade(client, "AAPL", 1, "buy")
    cache.update("AAPL", 200.0)
    body = trade(client, "AAPL", 3, "buy").json()
    position = body["portfolio"]["positions"][0]
    assert position["avg_cost"] == 175.0
    assert position["unrealized_pnl"] == 100.0


def test_sell_all_removes_position(client):
    trade(client, "MSFT", 0.1, "buy")
    trade(client, "MSFT", 0.2, "buy")
    body = trade(client, "MSFT", 0.3, "sell").json()
    assert body["portfolio"]["positions"] == []


def test_partial_sell(client):
    trade(client, "MSFT", 3, "buy")
    body = trade(client, "MSFT", 1, "sell").json()
    assert body["portfolio"]["positions"][0]["quantity"] == 2


def test_selling_unwatched_position_stops_tracking(client):
    client.post("/api/watchlist", json={"ticker": "PYPL"})
    trade(client, "PYPL", 1, "buy")
    client.delete("/api/watchlist/PYPL")
    trade(client, "PYPL", 1, "sell")
    assert client.app.state.cache.get("PYPL") is None


@pytest.mark.parametrize(
    ("ticker", "quantity", "side", "detail"),
    [
        ("AAPL", 0, "buy", "Quantity must be positive"),
        ("AAPL", -1, "buy", "Quantity must be positive"),
        ("ZZZZ", 1, "buy", "No price"),
        ("AAPL", 1000, "buy", "Insufficient cash"),
        ("AAPL", 1, "sell", "Insufficient shares"),
        ("AAPL", 1, "hold", "Invalid side"),
    ],
)
def test_trade_rejections(client, ticker, quantity, side, detail):
    resp = trade(client, ticker, quantity, side)
    assert resp.status_code == 400
    assert detail in resp.json()["detail"]
    assert client.get("/api/portfolio").json()["cash"] == 10000.0


def test_trade_records_snapshot(client):
    before = len(client.get("/api/portfolio/history").json())
    trade(client, "AAPL", 1, "buy")
    history = client.get("/api/portfolio/history").json()
    assert len(history) == before + 1
    assert set(history[-1]) == {"total_value", "recorded_at"}


def test_total_value_uses_live_price(client):
    trade(client, "NVDA", 1, "buy")
    client.app.state.cache.update("NVDA", price_of(client, "NVDA") + 10)
    portfolio = client.get("/api/portfolio").json()
    assert portfolio["unrealized_pnl"] == pytest.approx(10, abs=0.02)
