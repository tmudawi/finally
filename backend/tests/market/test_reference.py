"""Tests for reference price / change_pct and the SSE payload shape."""

from app.market.cache import PriceCache
from app.market.models import PriceUpdate


class TestChangePct:
    def test_uses_reference_price(self):
        update = PriceUpdate(
            ticker="AAPL", price=110.0, previous_price=109.0, reference_price=100.0
        )
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
