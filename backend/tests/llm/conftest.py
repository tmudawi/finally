"""Fakes for app.services and app.db used by the LLM tests."""

import pytest

from app.llm import chat


class FakeServices:
    """Records calls; raises ValueError for tickers listed in `fail`."""

    def __init__(self):
        self.calls = []
        self.fail = set()

    async def execute_trade(self, state, ticker, side, quantity):
        self.calls.append(("trade", ticker, side, quantity))
        if ticker in self.fail:
            raise ValueError("Insufficient cash")
        return {"ticker": ticker, "side": side, "quantity": quantity, "price": 190.0}

    async def add_ticker(self, state, ticker):
        self.calls.append(("add", ticker))
        if ticker in self.fail:
            raise ValueError("Unknown ticker")

    async def remove_ticker(self, state, ticker):
        self.calls.append(("remove", ticker))

    def portfolio_summary(self, state):
        return {"cash": 10000.0, "total_value": 10000.0, "unrealized_pnl": 0.0, "positions": []}

    def watchlist_with_prices(self, state):
        return [{"ticker": "AAPL", "price": 190.0, "change_pct": 0.1, "direction": "up"}]


class FakeDb:
    """In-memory chat history."""

    def __init__(self):
        self.messages = []

    def recent_chat_messages(self, limit=20):
        return self.messages[-limit:]

    def add_chat_message(self, role, content, actions):
        self.messages.append({"role": role, "content": content, "actions": actions})


@pytest.fixture
def services(monkeypatch):
    fake = FakeServices()
    monkeypatch.setattr(chat, "services", fake)
    return fake


@pytest.fixture
def db(monkeypatch):
    fake = FakeDb()
    monkeypatch.setattr(chat, "db", fake)
    return fake
