"""Tests for MassiveDataSource, using real massive SDK snapshot models."""

import asyncio
import threading
from unittest.mock import MagicMock, patch

import pytest
from massive.rest.models import TickerSnapshot

from app.market.cache import PriceCache
from app.market.interface import UnknownTickerError
from app.market.massive_client import MassiveDataSource, parse_snapshot

TS_NS = 1_760_630_400_118_000_000  # 2025-10-16T16:00:00.118Z in nanoseconds


def make_snapshot(ticker: str, price: float | None = 190.5, prev_close: float | None = 189.0):
    """Build a real TickerSnapshot from an API-shaped payload."""
    raw = {"ticker": ticker, "updated": TS_NS}
    if price is not None:
        raw["lastTrade"] = {"p": price, "s": 100, "t": TS_NS, "x": 4}
    if prev_close is not None:
        raw["prevDay"] = {"o": prev_close, "h": prev_close, "l": prev_close, "c": prev_close}
    return TickerSnapshot.from_dict(raw)


def make_source(tickers: list[str], snapshots=None, poll_interval: float = 60.0):
    """A source with a fake client whose get_snapshot_all returns the given snapshots."""
    cache = PriceCache()
    source = MassiveDataSource(api_key="test-key", price_cache=cache, poll_interval=poll_interval)
    source._tickers = list(tickers)
    source._client = MagicMock()
    source._client.get_snapshot_all.return_value = snapshots or []
    return source, cache


def fake_rest_client(snapshots=None, error: Exception | None = None):
    """Patch RESTClient so start() builds a fake client."""
    client = MagicMock()
    client.get_snapshot_all.return_value = snapshots or []
    client.get_snapshot_all.side_effect = error
    return patch("app.market.massive_client.RESTClient", return_value=client)


class TestParseSnapshot:
    def test_last_trade_price_and_nanosecond_timestamp(self):
        assert parse_snapshot(make_snapshot("AAPL", 191.42, 189.86)) == (
            191.42,
            1_760_630_400.118,
            189.86,
        )

    def test_falls_back_to_day_close_without_trade(self):
        snap = TickerSnapshot.from_dict({"ticker": "AAPL", "day": {"c": 191.0}, "updated": TS_NS})
        price, timestamp, prev_close = parse_snapshot(snap)
        assert (price, timestamp, prev_close) == (191.0, 1_760_630_400.118, None)

    def test_falls_back_to_prev_close(self):
        assert parse_snapshot(make_snapshot("AAPL", None, 189.0))[0] == 189.0

    def test_no_price_returns_none(self):
        assert parse_snapshot(make_snapshot("AAPL", None, None)) is None


@pytest.mark.asyncio
class TestPolling:
    async def test_poll_updates_cache(self):
        source, cache = make_source(
            ["AAPL", "GOOGL"], [make_snapshot("AAPL", 190.5), make_snapshot("GOOGL", 175.25)]
        )
        await source._poll_once()
        assert cache.get_price("AAPL") == 190.5
        assert cache.get_price("GOOGL") == 175.25
        assert source._client.get_snapshot_all.call_args.kwargs["tickers"] == ["AAPL", "GOOGL"]

    async def test_timestamp_converted_from_nanoseconds(self):
        source, cache = make_source(["AAPL"], [make_snapshot("AAPL")])
        await source._poll_once()
        assert cache.get("AAPL").timestamp == 1_760_630_400.118

    async def test_prev_close_is_reference_price(self):
        source, cache = make_source(["AAPL"], [make_snapshot("AAPL", 110.0, 100.0)])
        await source._poll_once()
        assert cache.get("AAPL").change_pct == 10.0

    async def test_missing_prev_close_falls_back_to_first_seen(self):
        source, cache = make_source(["AAPL"], [make_snapshot("AAPL", 110.0, None)])
        await source._poll_once()
        assert cache.get("AAPL").reference_price == 110.0

    async def test_unpriced_snapshot_skipped(self):
        source, cache = make_source(
            ["AAPL", "BAD"], [make_snapshot("AAPL"), make_snapshot("BAD", None, None)]
        )
        await source._poll_once()
        assert cache.get_price("AAPL") == 190.5
        assert "BAD" not in cache

    async def test_api_error_does_not_crash(self):
        source, cache = make_source(["AAPL"])
        source._client.get_snapshot_all.side_effect = Exception("network error")
        await source._poll_once()
        assert len(cache) == 0

    async def test_empty_tickers_skips_poll(self):
        source, _ = make_source([])
        await source._poll_once()
        source._client.get_snapshot_all.assert_not_called()

    async def test_ticker_removed_during_poll_is_not_written_back(self):
        source, cache = make_source(["AAPL", "MSFT"])
        release = threading.Event()

        def slow_fetch(**kwargs):
            release.wait()
            return [make_snapshot("AAPL"), make_snapshot("MSFT")]

        source._client.get_snapshot_all.side_effect = slow_fetch
        poll = asyncio.create_task(source._poll_once())
        await asyncio.sleep(0.05)
        await source.remove_ticker("MSFT")
        release.set()
        await poll

        assert "AAPL" in cache
        assert "MSFT" not in cache

    async def test_poll_loop_polls_repeatedly(self):
        source, _ = make_source(["AAPL"], poll_interval=0.01)
        calls = 0

        async def fake_poll() -> None:
            nonlocal calls
            calls += 1
            if calls >= 2:
                raise asyncio.CancelledError

        with patch.object(source, "_poll_once", side_effect=fake_poll):
            with pytest.raises(asyncio.CancelledError):
                await source._poll_loop()
        assert calls == 2


@pytest.mark.asyncio
class TestLifecycle:
    async def test_start_polls_immediately(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="k", price_cache=cache)
        with fake_rest_client([make_snapshot("AAPL", 190.5)]):
            await source.start(["aapl"])
        assert cache.get_price("AAPL") == 190.5
        assert source.get_tickers() == ["AAPL"]
        await source.stop()

    async def test_start_failure_raises(self):
        source = MassiveDataSource(api_key="bad", price_cache=PriceCache())
        with fake_rest_client(error=Exception("403 Forbidden")):
            with pytest.raises(RuntimeError, match="first poll failed"):
                await source.start(["AAPL"])
        assert source._task is None

    async def test_start_with_no_prices_raises(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache())
        with fake_rest_client([make_snapshot("AAPL", None, None)]):
            with pytest.raises(RuntimeError, match="no prices"):
                await source.start(["AAPL"])

    async def test_start_with_no_tickers_still_validates_key(self):
        cache = PriceCache()
        source = MassiveDataSource(api_key="bad", price_cache=cache)
        with fake_rest_client(error=Exception("401")):
            with pytest.raises(RuntimeError):
                await source.start([])

        with fake_rest_client([make_snapshot("SPY")]) as rest:
            await source.start([])
        assert rest.return_value.get_snapshot_all.call_args.kwargs["tickers"] == ["SPY"]
        assert len(cache) == 0  # The probe ticker is not cached
        await source.stop()

    async def test_stop_cancels_task_and_is_idempotent(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache())
        with fake_rest_client([make_snapshot("AAPL")]):
            await source.start(["AAPL"])
        task = source._task
        await source.stop()
        await source.stop()
        assert task.cancelled()
        assert source._task is None


@pytest.mark.asyncio
class TestTickers:
    async def test_add_before_start_only_tracks(self):
        source = MassiveDataSource(api_key="k", price_cache=PriceCache())
        await source.add_ticker("  aapl ")
        assert source.get_tickers() == ["AAPL"]

    async def test_add_after_start_prices_immediately(self):
        source, cache = make_source(["AAPL"], [make_snapshot("PYPL", 70.0)])
        await source.add_ticker("pypl")
        assert source.get_tickers() == ["AAPL", "PYPL"]
        assert cache.get_price("PYPL") == 70.0
        assert source._client.get_snapshot_all.call_args.kwargs["tickers"] == ["PYPL"]

    async def test_add_unknown_ticker_raises(self):
        source, cache = make_source(["AAPL"], [])
        with pytest.raises(UnknownTickerError):
            await source.add_ticker("ZZZZZ")
        assert source.get_tickers() == ["AAPL"]
        assert "ZZZZZ" not in cache

    async def test_add_duplicate_is_noop(self):
        source, _ = make_source(["AAPL"])
        await source.add_ticker("AAPL")
        assert source.get_tickers() == ["AAPL"]
        source._client.get_snapshot_all.assert_not_called()

    async def test_remove_ticker(self):
        source, cache = make_source(["AAPL", "GOOGL"])
        cache.update("AAPL", 190.0)
        await source.remove_ticker("aapl")
        assert source.get_tickers() == ["GOOGL"]
        assert "AAPL" not in cache
