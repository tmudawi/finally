"""Both market data sources honour the same MarketDataSource contract."""

from unittest.mock import MagicMock, patch

import pytest
from massive.rest.models import TickerSnapshot

from app.market.cache import PriceCache
from app.market.interface import MarketDataSource
from app.market.massive_client import MassiveDataSource
from app.market.seed_prices import SEED_PRICES
from app.market.simulator import SimulatorDataSource


def fake_snapshots(market_type, tickers):
    """Fake get_snapshot_all: every requested ticker trades at 100."""
    return [
        TickerSnapshot.from_dict({"ticker": t, "lastTrade": {"p": 100.0, "t": 1}}) for t in tickers
    ]


@pytest.fixture(params=["simulator", "massive"])
async def source_and_cache(request):
    cache = PriceCache()
    if request.param == "simulator":
        source = SimulatorDataSource(price_cache=cache, update_interval=10, seed=1)
        await source.start(["AAPL", "MSFT"])
        first_prices = {t: SEED_PRICES[t] for t in ("AAPL", "MSFT")}
    else:
        source = MassiveDataSource(api_key="k", price_cache=cache, poll_interval=10)
        client = MagicMock()
        client.get_snapshot_all.side_effect = fake_snapshots
        with patch("app.market.massive_client.RESTClient", return_value=client):
            await source.start(["AAPL", "MSFT"])
        first_prices = {"AAPL": 100.0, "MSFT": 100.0}
    yield source, cache, first_prices
    await source.stop()


async def test_is_market_data_source(source_and_cache):
    source, _, _ = source_and_cache
    assert isinstance(source, MarketDataSource)


async def test_start_prices_every_ticker(source_and_cache):
    source, cache, first_prices = source_and_cache
    assert source.get_tickers() == ["AAPL", "MSFT"]
    assert set(cache.get_all()) == {"AAPL", "MSFT"}
    # change_pct is measured from the first price (no prev close in the fake snapshots)
    assert {t: u.reference_price for t, u in cache.get_all().items()} == first_prices


async def test_add_ticker_prices_immediately(source_and_cache):
    source, cache, _ = source_and_cache
    await source.add_ticker("pypl")
    assert "PYPL" in source.get_tickers()
    assert cache.get_price("PYPL") is not None


async def test_add_existing_ticker_is_noop(source_and_cache):
    source, cache, _ = source_and_cache
    version = cache.version
    await source.add_ticker("AAPL")
    assert source.get_tickers() == ["AAPL", "MSFT"]
    assert cache.version == version


async def test_remove_ticker_clears_cache(source_and_cache):
    source, cache, _ = source_and_cache
    await source.remove_ticker("msft")
    await source.remove_ticker("NOPE")
    assert source.get_tickers() == ["AAPL"]
    assert "MSFT" not in cache


async def test_stop_is_idempotent(source_and_cache):
    source, _, _ = source_and_cache
    await source.stop()
    await source.stop()
