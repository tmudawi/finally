"""Tests for repository functions and transactions."""

import pytest

from app import db


def test_set_cash():
    db.set_cash(1234.5)
    assert db.get_cash() == 1234.5


def test_add_watchlist_appends_in_order():
    assert db.add_watchlist("PYPL") is True
    assert db.list_watchlist()[-1] == "PYPL"


def test_add_watchlist_duplicate_returns_false():
    assert db.add_watchlist("AAPL") is False
    assert db.list_watchlist().count("AAPL") == 1


def test_remove_watchlist():
    assert db.remove_watchlist("TSLA") is True
    assert db.remove_watchlist("TSLA") is False
    assert "TSLA" not in db.list_watchlist()


def test_positions_start_empty():
    assert db.list_positions() == []
    assert db.get_position("AAPL") is None


def test_save_position_insert_and_update():
    db.save_position("AAPL", 10, 190.0)
    db.save_position("AAPL", 15, 192.0)
    pos = db.get_position("AAPL")
    assert pos["quantity"] == 15
    assert pos["avg_cost"] == 192.0
    assert set(pos) == {"ticker", "quantity", "avg_cost", "updated_at"}
    assert len(db.list_positions()) == 1


def test_save_position_fractional():
    db.save_position("MSFT", 0.25, 400.0)
    assert db.get_position("MSFT")["quantity"] == 0.25


def test_save_position_zero_deletes_row():
    db.save_position("AAPL", 10, 190.0)
    db.save_position("AAPL", 0, 190.0)
    assert db.get_position("AAPL") is None
    assert db.list_positions() == []


def test_list_positions_ordered_by_ticker():
    db.save_position("MSFT", 1, 400.0)
    db.save_position("AAPL", 1, 190.0)
    assert [p["ticker"] for p in db.list_positions()] == ["AAPL", "MSFT"]


def test_record_trade_returns_trade():
    trade = db.record_trade("AAPL", "buy", 2, 190.0)
    assert trade["ticker"] == "AAPL"
    assert trade["side"] == "buy"
    assert trade["quantity"] == 2
    assert trade["price"] == 190.0
    assert trade["id"] and trade["executed_at"]
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 1


def test_snapshots_oldest_first():
    db.record_snapshot(10000.0)
    db.record_snapshot(10100.0)
    snaps = db.list_snapshots()
    assert [s["total_value"] for s in snaps] == [10000.0, 10100.0]
    assert set(snaps[0]) == {"total_value", "recorded_at"}


def test_chat_messages_roundtrip_actions():
    actions = [{"type": "trade", "ticker": "AAPL", "status": "ok"}]
    db.add_chat_message("user", "buy apple", None)
    db.add_chat_message("assistant", "done", actions)
    msgs = db.recent_chat_messages()
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["actions"] is None
    assert msgs[1]["actions"] == actions
    assert set(msgs[0]) == {"role", "content", "actions", "created_at"}


def test_recent_chat_messages_limit_keeps_latest_oldest_first():
    for i in range(25):
        db.add_chat_message("user", f"m{i}", None)
    msgs = db.recent_chat_messages(limit=20)
    assert [m["content"] for m in msgs] == [f"m{i}" for i in range(5, 25)]


def test_transaction_commits_together():
    with db.transaction():
        db.set_cash(8000.0)
        db.save_position("AAPL", 10, 200.0)
        db.record_trade("AAPL", "buy", 10, 200.0)
    assert db.get_cash() == 8000.0
    assert db.get_position("AAPL")["quantity"] == 10


def test_transaction_rolls_back_on_error():
    with pytest.raises(ValueError):
        with db.transaction():
            db.set_cash(8000.0)
            db.save_position("AAPL", 10, 200.0)
            db.record_trade("AAPL", "buy", 10, 200.0)
            raise ValueError("boom")
    assert db.get_cash() == 10000.0
    assert db.get_position("AAPL") is None
    with db.transaction() as conn:
        assert conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0
