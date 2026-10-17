"""Tests for GBMSimulator."""

import math

import numpy as np
import pytest

from app.market.seed_prices import SEED_PRICES, TICKER_PARAMS
from app.market.simulator import GBMSimulator


class TestGBMSimulator:
    """Unit tests for the GBM price simulator."""

    def test_step_returns_all_tickers(self):
        """Test that step() returns prices for all tickers."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        result = sim.step()
        assert set(result.keys()) == {"AAPL", "GOOGL"}

    def test_prices_are_positive(self):
        """GBM prices can never go negative (exp() is always positive)."""
        sim = GBMSimulator(tickers=["AAPL"])
        for _ in range(10_000):
            prices = sim.step()
            assert prices["AAPL"] > 0

    def test_initial_prices_match_seeds(self):
        """Test that initial prices match seed prices."""
        sim = GBMSimulator(tickers=["AAPL"])
        # Before any step, price should be the seed price
        assert sim.get_price("AAPL") == SEED_PRICES["AAPL"]

    def test_add_ticker(self):
        """Test adding a ticker dynamically."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("TSLA")
        result = sim.step()
        assert "TSLA" in result

    def test_remove_ticker(self):
        """Test removing a ticker."""
        sim = GBMSimulator(tickers=["AAPL", "GOOGL"])
        sim.remove_ticker("GOOGL")
        result = sim.step()
        assert "GOOGL" not in result
        assert "AAPL" in result

    def test_add_duplicate_is_noop(self):
        """Test that adding a duplicate ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.add_ticker("AAPL")
        assert len(sim._tickers) == 1

    def test_remove_nonexistent_is_noop(self):
        """Test that removing a non-existent ticker is a no-op."""
        sim = GBMSimulator(tickers=["AAPL"])
        sim.remove_ticker("NOPE")  # Should not raise

    def test_unknown_ticker_gets_random_seed_price(self):
        """Test that unknown tickers get random seed prices."""
        sim = GBMSimulator(tickers=["ZZZZ"])
        price = sim.get_price("ZZZZ")
        assert price is not None
        assert 50.0 <= price <= 300.0

    def test_empty_step(self):
        """Test stepping with no tickers."""
        sim = GBMSimulator(tickers=[])
        result = sim.step()
        assert result == {}

    def test_prices_change_over_time(self):
        """After many steps, prices should have drifted from their seeds."""
        sim = GBMSimulator(tickers=["AAPL"])
        initial_price = sim.get_price("AAPL")

        for _ in range(1000):
            sim.step()

        final_price = sim.get_price("AAPL")
        # Price should have changed (extremely unlikely to be exactly the seed)
        assert final_price != initial_price

    def test_cholesky_rebuilds_on_add(self):
        """Test that Cholesky matrix is rebuilt when tickers are added."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None  # Only 1 ticker, no correlation matrix
        sim.add_ticker("GOOGL")
        assert sim._cholesky is not None  # Now 2 tickers, matrix exists

    def test_cholesky_none_with_one_ticker(self):
        """Test that Cholesky is None with only one ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim._cholesky is None

    def test_get_price_returns_none_for_unknown(self):
        """Test that get_price returns None for unknown ticker."""
        sim = GBMSimulator(tickers=["AAPL"])
        assert sim.get_price("UNKNOWN") is None

    def test_pairwise_correlation_tech_stocks(self):
        """Test that tech stocks have high correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "GOOGL")
        assert corr == 0.6

    def test_pairwise_correlation_finance_stocks(self):
        """Test that finance stocks have moderate correlation."""
        corr = GBMSimulator._pairwise_correlation("JPM", "V")
        assert corr == 0.5

    def test_pairwise_correlation_tsla(self):
        """Test that TSLA has lower correlation with everything."""
        corr = GBMSimulator._pairwise_correlation("TSLA", "AAPL")
        assert corr == 0.3
        corr = GBMSimulator._pairwise_correlation("TSLA", "JPM")
        assert corr == 0.3

    def test_pairwise_correlation_cross_sector(self):
        """Test cross-sector correlation."""
        corr = GBMSimulator._pairwise_correlation("AAPL", "JPM")
        assert corr == 0.3

    def test_default_dt_is_reasonable(self):
        """Test that default dt is a reasonable small value."""
        assert 0 < GBMSimulator.DEFAULT_DT < 0.0001

    def test_prices_rounded_to_two_decimals(self):
        """Test that prices are rounded to 2 decimal places."""
        sim = GBMSimulator(tickers=["AAPL"], seed=1)
        price = sim.step()["AAPL"]
        assert price == round(price, 2)

    def test_same_seed_same_path(self):
        a = GBMSimulator(tickers=["AAPL", "ZZZZ"], seed=7)
        b = GBMSimulator(tickers=["AAPL", "ZZZZ"], seed=7)
        c = GBMSimulator(tickers=["AAPL", "ZZZZ"], seed=8)
        path_a = [a.step() for _ in range(100)]
        assert path_a == [b.step() for _ in range(100)]
        assert path_a != [c.step() for _ in range(100)]


TICKERS = ["AAPL", "GOOGL", "TSLA", "JPM", "V"]


def log_returns(sim: GBMSimulator, ticks: int) -> np.ndarray:
    """Per-tick full-precision log returns, shape (ticks, n_tickers)."""
    prices = np.empty((ticks + 1, len(TICKERS)))
    prices[0] = [sim.get_price(t) for t in TICKERS]
    for i in range(1, ticks + 1):
        sim.step()
        prices[i] = [sim.get_price(t) for t in TICKERS]
    return np.diff(np.log(prices), axis=0)


class TestGBMStatistics:
    """The GBM math: per-tick volatility, correlation structure and jump events."""

    returns = log_returns(GBMSimulator(TICKERS, event_probability=0.0, seed=42), 20_000)

    def test_per_tick_volatility_matches_sigma(self):
        for i, ticker in enumerate(TICKERS):
            expected = TICKER_PARAMS[ticker]["sigma"] * math.sqrt(GBMSimulator.DEFAULT_DT)
            assert self.returns[:, i].std() == pytest.approx(expected, rel=0.05)

    @pytest.mark.parametrize(
        ("a", "b", "rho"),
        [("AAPL", "GOOGL", 0.6), ("JPM", "V", 0.5), ("AAPL", "JPM", 0.3), ("TSLA", "AAPL", 0.3)],
    )
    def test_correlation_structure(self, a, b, rho):
        corr = np.corrcoef(self.returns[:, TICKERS.index(a)], self.returns[:, TICKERS.index(b)])
        assert corr[0, 1] == pytest.approx(rho, abs=0.05)

    def test_events_move_price_2_to_5_percent(self):
        returns = log_returns(GBMSimulator(TICKERS, event_probability=1.0, seed=1), 200)
        moves = np.abs(np.expm1(returns))
        assert moves.min() > 0.019
        assert moves.max() < 0.051
