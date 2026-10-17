"""Watchlist routes."""

from fastapi import APIRouter
from pydantic import BaseModel

from app import services
from app.api.deps import State

router = APIRouter(prefix="/api/watchlist", tags=["watchlist"])


class WatchlistAdd(BaseModel):
    """A ticker to add."""

    ticker: str


@router.get("")
def get_watchlist(state: State) -> list[dict]:
    """Watched tickers with their latest prices."""
    return services.watchlist_with_prices(state)


@router.post("")
async def add(body: WatchlistAdd, state: State) -> list[dict]:
    """Add a ticker and return the updated watchlist."""
    await services.add_ticker(state, body.ticker)
    return services.watchlist_with_prices(state)


@router.delete("/{ticker}")
async def remove(ticker: str, state: State) -> list[dict]:
    """Remove a ticker and return the updated watchlist."""
    await services.remove_ticker(state, ticker)
    return services.watchlist_with_prices(state)
