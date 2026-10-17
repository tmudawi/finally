"""Tests for the SSE price stream."""

import asyncio
import json

import pytest
import uvicorn
from fastapi import FastAPI

from app.market.cache import PriceCache
from app.market.stream import create_stream_router, price_events

SSE_KEYS = {"price", "prev_price", "change_pct", "timestamp", "direction"}


@pytest.mark.asyncio
class TestPriceEvents:
    async def test_emits_plan_payload(self):
        cache = PriceCache()
        cache.update("AAPL", 100.0)
        cache.update("AAPL", 101.0)
        payload = await anext(price_events(cache, interval=0.01))
        assert set(payload["AAPL"]) == SSE_KEYS
        assert payload["AAPL"]["change_pct"] == 1.0
        assert payload["AAPL"]["direction"] == "up"

    async def test_emits_only_on_change(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        events = price_events(cache, interval=0.01)
        await anext(events)
        pending = asyncio.ensure_future(anext(events))
        await asyncio.sleep(0.05)
        assert not pending.done()
        cache.update("AAPL", 191.0)
        second = await asyncio.wait_for(pending, 1)
        assert second["AAPL"]["price"] == 191.0

    async def test_removal_is_sent(self):
        cache = PriceCache()
        cache.update("AAPL", 190.0)
        events = price_events(cache, interval=0.01)
        await anext(events)
        cache.remove("AAPL")
        assert await asyncio.wait_for(anext(events), 1) == {}


def test_each_router_is_independent():
    r1 = create_stream_router(PriceCache())
    r2 = create_stream_router(PriceCache())
    assert r1 is not r2
    assert [r.path for r in r2.routes] == ["/api/stream/prices"]


async def test_live_endpoint_streams_prices():
    """Smoke test over a real socket (TestClient hangs on infinite streams)."""
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    app = FastAPI()
    app.include_router(create_stream_router(cache))
    config = uvicorn.Config(app, host="127.0.0.1", port=0, ws="none", log_level="warning")
    server = uvicorn.Server(config)
    serve = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.01)
    port = server.servers[0].sockets[0].getsockname()[1]

    reader, writer = await asyncio.open_connection("127.0.0.1", port)
    writer.write(b"GET /api/stream/prices HTTP/1.1\r\nHost: test\r\n\r\n")
    received = b""
    while b"data: " not in received or b"\n\n" not in received.partition(b"data: ")[2]:
        received += await asyncio.wait_for(reader.read(4096), 5)
    writer.close()
    server.should_exit = True
    await serve

    text = received.decode()
    assert "content-type: text/event-stream" in text.lower()
    assert "retry: 1000" in text
    data_line = next(line for line in text.splitlines() if line.startswith("data: "))
    assert json.loads(data_line.removeprefix("data: "))["AAPL"]["price"] == 190.0
