"""Business logic shared by the REST routes and the LLM chat."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app import db
from app.market import MarketDataSource, PriceCache, UnknownTickerError
from app.market.interface import normalize_ticker

TICKER_RE = re.compile(r"^[A-Z]{1,5}$")
EPSILON = 1e-9  # Float tolerance for share quantities


@dataclass
class AppState:
    """Shared runtime state: the price cache and the market data source."""

    cache: PriceCache
    source: MarketDataSource


def tracked_tickers() -> list[str]:
    """Watchlist tickers plus tickers with an open position, watchlist order first."""
    tickers = db.list_watchlist()
    held = [p["ticker"] for p in db.list_positions() if p["ticker"] not in tickers]
    return tickers + held


async def execute_trade(state: AppState, ticker: str, side: str, quantity: float) -> dict:
    """Execute a market order at the cached price and record a snapshot.

    Raises ValueError on invalid side, quantity, missing price, or insufficient cash/shares.
    """
    ticker = normalize_ticker(ticker)
    if side not in ("buy", "sell"):
        raise ValueError(f"Invalid side: {side}")
    if quantity <= 0:
        raise ValueError("Quantity must be positive")
    price = state.cache.get_price(ticker)
    if price is None:
        raise ValueError(f"No price available for {ticker}")

    with db.transaction():
        if side == "buy":
            _apply_buy(ticker, quantity, price)
        else:
            _apply_sell(ticker, quantity, price)
        trade = db.record_trade(ticker, side, quantity, price)

    if db.get_position(ticker) is None and ticker not in db.list_watchlist():
        await state.source.remove_ticker(ticker)
    record_snapshot(state)
    return {"ticker": ticker, "side": side, "quantity": quantity, "price": trade["price"]}


def _apply_buy(ticker: str, quantity: float, price: float) -> None:
    """Debit cash and grow the position at a weighted average cost."""
    cash = db.get_cash()
    cost = quantity * price
    if cost > cash:
        raise ValueError(f"Insufficient cash: need ${cost:.2f}, have ${cash:.2f}")
    position = db.get_position(ticker)
    held = position["quantity"] if position else 0.0
    old_cost = position["avg_cost"] * held if position else 0.0
    new_qty = held + quantity
    db.set_cash(cash - cost)
    db.save_position(ticker, new_qty, (old_cost + cost) / new_qty)


def _apply_sell(ticker: str, quantity: float, price: float) -> None:
    """Credit cash and shrink the position; the row is deleted at zero."""
    position = db.get_position(ticker)
    held = position["quantity"] if position else 0.0
    remaining = held - quantity
    if remaining < -EPSILON:
        raise ValueError(f"Insufficient shares: have {held:g} {ticker}")
    db.set_cash(db.get_cash() + quantity * price)
    db.save_position(ticker, 0.0 if remaining < EPSILON else remaining, position["avg_cost"])


async def add_ticker(state: AppState, ticker: str) -> None:
    """Validate the symbol, start tracking it and add it to the watchlist."""
    ticker = normalize_ticker(ticker)
    if not TICKER_RE.match(ticker):
        raise ValueError(f"Invalid ticker: {ticker!r} (1-5 letters)")
    try:
        await state.source.add_ticker(ticker)
    except UnknownTickerError as e:
        raise ValueError(f"Unknown ticker: {ticker}") from e
    db.add_watchlist(ticker)


async def remove_ticker(state: AppState, ticker: str) -> None:
    """Remove from the watchlist; stop tracking unless a position is held."""
    ticker = normalize_ticker(ticker)
    db.remove_watchlist(ticker)
    if db.get_position(ticker) is None:
        await state.source.remove_ticker(ticker)


def portfolio_summary(state: AppState) -> dict:
    """Cash, positions valued at cached prices, total value and unrealized P&L."""
    cash = db.get_cash()
    positions = [_value_position(state, p) for p in db.list_positions()]
    market_value = sum(p["market_value"] for p in positions)
    return {
        "cash": round(cash, 2),
        "total_value": round(cash + market_value, 2),
        "unrealized_pnl": round(sum(p["unrealized_pnl"] for p in positions), 2),
        "positions": positions,
    }


def _value_position(state: AppState, position: dict) -> dict:
    """Value one position at the cached price, falling back to its average cost."""
    qty, avg_cost = position["quantity"], position["avg_cost"]
    price = state.cache.get_price(position["ticker"]) or avg_cost
    pnl = (price - avg_cost) * qty
    return {
        "ticker": position["ticker"],
        "quantity": qty,
        "avg_cost": round(avg_cost, 4),
        "current_price": price,
        "market_value": round(price * qty, 2),
        "unrealized_pnl": round(pnl, 2),
        "pnl_pct": round((price / avg_cost - 1) * 100, 2),
    }


def record_snapshot(state: AppState) -> None:
    """Store the current total portfolio value."""
    db.record_snapshot(portfolio_summary(state)["total_value"])


def watchlist_with_prices(state: AppState) -> list[dict]:
    """Watchlist tickers with their latest cached price, or nulls if not yet priced."""
    return [_ticker_quote(state, t) for t in db.list_watchlist()]


def _ticker_quote(state: AppState, ticker: str) -> dict:
    """One watchlist row from the price cache."""
    update = state.cache.get(ticker)
    if update is None:
        return {"ticker": ticker, "price": None, "change_pct": None, "direction": None}
    return {
        "ticker": ticker,
        "price": update.price,
        "change_pct": update.change_pct,
        "direction": update.direction,
    }
