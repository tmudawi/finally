"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterable

from fastapi import APIRouter
from fastapi.sse import EventSourceResponse, ServerSentEvent

from .cache import PriceCache

CHECK_INTERVAL = 0.5  # Seconds between cache version checks
RETRY_MS = 1000  # Browser reconnect delay


async def price_events(
    price_cache: PriceCache, interval: float = CHECK_INTERVAL
) -> AsyncIterable[dict]:
    """Yield the full PLAN.md price map whenever the cache version changes.

    An empty map is sent too, so clients drop tickers that were removed.
    """
    last_version = -1
    while True:
        if price_cache.version != last_version:
            last_version = price_cache.version
            prices = price_cache.get_all()
            yield {ticker: update.to_sse_dict() for ticker, update in prices.items()}
        await asyncio.sleep(interval)


def create_stream_router(price_cache: PriceCache) -> APIRouter:
    """Create a new router serving GET /api/stream/prices from the given cache.

    FastAPI's EventSourceResponse sends keepalive pings and stops the generator
    when the client disconnects.
    """
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices", response_class=EventSourceResponse)
    async def stream_prices() -> AsyncIterable[ServerSentEvent]:
        """Stream all tracked prices: data: {"AAPL": {"price": ..., ...}, ...}."""
        yield ServerSentEvent(retry=RETRY_MS)
        async for payload in price_events(price_cache):
            yield ServerSentEvent(data=payload)

    return router
