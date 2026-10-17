"""Repository functions for user state, watchlist, positions, trades, snapshots and chat."""

import json

from .connection import new_id, now_iso, transaction


def get_cash(user_id: str = "default") -> float:
    """Return the user's cash balance."""
    with transaction() as conn:
        row = conn.execute(
            "SELECT cash_balance FROM users_profile WHERE id = ?", (user_id,)
        ).fetchone()
    return row["cash_balance"]


def set_cash(amount: float, user_id: str = "default") -> None:
    """Set the user's cash balance."""
    with transaction() as conn:
        conn.execute("UPDATE users_profile SET cash_balance = ? WHERE id = ?", (amount, user_id))


def list_watchlist(user_id: str = "default") -> list[str]:
    """Return watched tickers ordered by when they were added."""
    with transaction() as conn:
        rows = conn.execute(
            "SELECT ticker FROM watchlist WHERE user_id = ? ORDER BY added_at, rowid", (user_id,)
        ).fetchall()
    return [row["ticker"] for row in rows]


def add_watchlist(ticker: str, user_id: str = "default") -> bool:
    """Add a ticker to the watchlist. Return False if it was already present."""
    with transaction() as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) VALUES (?, ?, ?, ?)",
            (new_id(), user_id, ticker, now_iso()),
        )
    return cur.rowcount == 1


def remove_watchlist(ticker: str, user_id: str = "default") -> bool:
    """Remove a ticker from the watchlist. Return False if it was absent."""
    with transaction() as conn:
        cur = conn.execute(
            "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?", (user_id, ticker)
        )
    return cur.rowcount == 1


def list_positions(user_id: str = "default") -> list[dict]:
    """Return all positions ordered by ticker."""
    with transaction() as conn:
        rows = conn.execute(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions "
            "WHERE user_id = ? ORDER BY ticker",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_position(ticker: str, user_id: str = "default") -> dict | None:
    """Return one position, or None if the ticker is not held."""
    with transaction() as conn:
        row = conn.execute(
            "SELECT ticker, quantity, avg_cost, updated_at FROM positions "
            "WHERE user_id = ? AND ticker = ?",
            (user_id, ticker),
        ).fetchone()
    return dict(row) if row else None


def save_position(ticker: str, quantity: float, avg_cost: float, user_id: str = "default") -> None:
    """Insert or update a position; delete the row when quantity is 0."""
    with transaction() as conn:
        if quantity == 0:
            conn.execute(
                "DELETE FROM positions WHERE user_id = ? AND ticker = ?", (user_id, ticker)
            )
            return
        conn.execute(
            "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (user_id, ticker) DO UPDATE SET "
            "quantity = excluded.quantity, avg_cost = excluded.avg_cost, "
            "updated_at = excluded.updated_at",
            (new_id(), user_id, ticker, quantity, avg_cost, now_iso()),
        )


def record_trade(
    ticker: str, side: str, quantity: float, price: float, user_id: str = "default"
) -> dict:
    """Append a trade to the log and return it."""
    trade = {
        "id": new_id(),
        "ticker": ticker,
        "side": side,
        "quantity": quantity,
        "price": price,
        "executed_at": now_iso(),
    }
    with transaction() as conn:
        conn.execute(
            "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at) "
            "VALUES (:id, :user_id, :ticker, :side, :quantity, :price, :executed_at)",
            {**trade, "user_id": user_id},
        )
    return trade


def record_snapshot(total_value: float, user_id: str = "default") -> None:
    """Record the portfolio's total value at this moment."""
    with transaction() as conn:
        conn.execute(
            "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) "
            "VALUES (?, ?, ?, ?)",
            (new_id(), user_id, total_value, now_iso()),
        )


def list_snapshots(user_id: str = "default") -> list[dict]:
    """Return portfolio snapshots, oldest first."""
    with transaction() as conn:
        rows = conn.execute(
            "SELECT total_value, recorded_at FROM portfolio_snapshots "
            "WHERE user_id = ? ORDER BY recorded_at, rowid",
            (user_id,),
        ).fetchall()
    return [dict(row) for row in rows]


def add_chat_message(
    role: str, content: str, actions: list | None, user_id: str = "default"
) -> None:
    """Store a chat message; actions are saved as JSON (or NULL)."""
    with transaction() as conn:
        conn.execute(
            "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (new_id(), user_id, role, content, _dump(actions), now_iso()),
        )


def recent_chat_messages(limit: int = 20, user_id: str = "default") -> list[dict]:
    """Return the last `limit` chat messages, oldest first, with actions decoded."""
    with transaction() as conn:
        rows = conn.execute(
            "SELECT role, content, actions, created_at FROM chat_messages "
            "WHERE user_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [{**dict(row), "actions": _load(row["actions"])} for row in reversed(rows)]


def _dump(actions: list | None) -> str | None:
    return None if actions is None else json.dumps(actions)


def _load(actions: str | None) -> list | None:
    return None if actions is None else json.loads(actions)
