"""Tests for the LLM_MOCK keyword responses."""

import pytest

from app.llm.client import mock_response


@pytest.mark.parametrize(
    "text, message",
    [
        ("Please BUY something", "Mock: buying 1 AAPL"),
        ("sell it", "Mock: selling 1 AAPL"),
        ("Watch PYPL", "Mock: adding PYPL"),
        ("hello", "Mock: hello from FinAlly"),
    ],
)
def test_mock_messages(text, message):
    assert mock_response(text).message == message


def test_mock_buy_trade():
    trade = mock_response("buy").trades[0]
    assert (trade.ticker, trade.side, trade.quantity) == ("AAPL", "buy", 1)


def test_mock_sell_trade():
    trade = mock_response("sell").trades[0]
    assert (trade.ticker, trade.side, trade.quantity) == ("AAPL", "sell", 1)


def test_mock_watch_change():
    change = mock_response("watch").watchlist_changes[0]
    assert (change.ticker, change.action) == ("PYPL", "add")


def test_mock_hello_has_no_actions():
    r = mock_response("hi")
    assert r.trades == [] and r.watchlist_changes == []
