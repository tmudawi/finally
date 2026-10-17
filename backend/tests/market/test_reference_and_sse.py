"""Tests for reference price / change_pct and the SSE payload shape."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource
from app.market.models import PriceUpdate
from app.market.stream import _generate_events


class TestChangePct:
    def test_uses_reference_price(self):
        update = PriceUpdate(ticker="AAPL", price=110.0, previous_price=109.0, reference_price=100.0)
        assert update.change_pct == 10.0

    def test_falls_back_to_previous_price(self):
        update = PriceUpdate(ticker="AAPL", price=110.0, previous_price=100.0)
        assert update.change_pct == 10.0

    def test_zero_reference(self):
        update = PriceUpdate(ticker="AAPL", price=1.0, previous_price=1.0, reference_price=0.0)
        assert update.change_pct == 0.0

    def test_to_sse_dict_shape(self):
        update = PriceUpdate(
            ticker="AAPL",
            price=190.5,
            previous_price=190.0,
            timestamp=1707580800.0,
            reference_price=189.0,
        )
        assert update.to_sse_dict() == {
            "price": 190.5,
            "prev_price": 190.0,
            "change_pct": 0.7937,
            "timestamp": "2024-02-10T16:00:00Z",
            "direction": "up",
        }


class TestCacheReference:
    def test_first_seen_price_is_reference(self):
        cache = PriceCache()
        cache.update("AAPL", 100.0)
        update = cache.update("AAPL", 105.0)
        assert update.reference_price == 100.0
        assert update.change_pct == 5.0

    def test_explicit_reference_wins_and_persists(self):
        cache = PriceCache()
        cache.update("AAPL", 105.0, reference_price=100.0)
        update = cache.update("AAPL", 110.0)
        assert update.reference_price == 100.0
        assert update.change_pct == 10.0

    def test_reference_reset_after_remove(self):
        cache = PriceCache()
        cache.update("AAPL", 100.0)
        cache.remove("AAPL")
        assert cache.update("AAPL", 50.0).reference_price == 50.0


@pytest.mark.asyncio
async def test_stream_emits_plan_payload():
    cache = PriceCache()
    cache.update("AAPL", 100.0)
    cache.update("AAPL", 101.0)

    request = MagicMock()
    request.client.host = "test"
    disconnected = iter([False, True])

    async def is_disconnected() -> bool:
        return next(disconnected)

    request.is_disconnected = is_disconnected

    chunks = [c async for c in _generate_events(cache, request, interval=0.01)]
    assert chunks[0].startswith("retry:")
    payload = json.loads(chunks[1].removeprefix("data: "))
    assert set(payload["AAPL"]) == {"price", "prev_price", "change_pct", "timestamp", "direction"}
    assert payload["AAPL"]["change_pct"] == 1.0
    assert payload["AAPL"]["direction"] == "up"


def _snap(ticker: str, price: float, prev_close) -> MagicMock:
    snap = MagicMock()
    snap.ticker = ticker
    snap.last_trade.price = price
    snap.last_trade.timestamp = 1707580800000
    snap.prev_day.close = prev_close
    return snap


@pytest.mark.asyncio
class TestMassiveReferenceAndStartup:
    async def test_prev_close_is_reference_price(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="k", price_cache=cache)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        with patch.object(source, "_fetch_snapshots", return_value=[_snap("AAPL", 110.0, 100.0)]):
            await source._poll_once()

        assert cache.get("AAPL").change_pct == 10.0

    async def test_missing_prev_close_falls_back_to_first_seen(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="k", price_cache=cache)
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        with patch.object(source, "_fetch_snapshots", return_value=[_snap("AAPL", 110.0, None)]):
            await source._poll_once()

        assert cache.get("AAPL").reference_price == 110.0

    async def test_first_poll_failure_raises_on_start(self):
        source = MassiveDataSource(api_key="bad", price_cache=PriceCache(), poll_interval=60.0)

        with patch("app.market.massive_client.RESTClient"):
            with patch.object(source, "_fetch_snapshots", side_effect=Exception("403 Forbidden")):
                with pytest.raises(RuntimeError, match="first poll failed"):
                    await source.start(["AAPL"])

        assert source._task is None

    async def test_later_poll_failure_is_swallowed(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache())
        source._tickers = ["AAPL"]
        source._client = MagicMock()

        with patch.object(source, "_fetch_snapshots", side_effect=Exception("429")):
            await source._poll_once()

    async def test_fetch_snapshots_passes_tickers(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache())
        source._tickers = ["AAPL", "MSFT"]
        source._client = MagicMock()
        source._client.get_snapshot_all.return_value = ["x"]

        assert source._fetch_snapshots() == ["x"]
        assert source._client.get_snapshot_all.call_args.kwargs["tickers"] == ["AAPL", "MSFT"]

    async def test_poll_loop_polls_repeatedly(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache(), poll_interval=0.01)
        calls = 0

        async def fake_poll(raise_on_error: bool = False) -> None:
            nonlocal calls
            calls += 1
            if calls >= 2:
                raise asyncio.CancelledError

        with patch.object(source, "_poll_once", side_effect=fake_poll):
            with pytest.raises(asyncio.CancelledError):
                await source._poll_loop()
        assert calls == 2
