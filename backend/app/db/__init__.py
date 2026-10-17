"""SQLite persistence layer for FinAlly."""

from .connection import get_db_path, init_db, transaction
from .repository import (
    add_chat_message,
    add_watchlist,
    get_cash,
    get_position,
    list_positions,
    list_snapshots,
    list_watchlist,
    recent_chat_messages,
    record_snapshot,
    record_trade,
    remove_watchlist,
    save_position,
    set_cash,
)

__all__ = [
    "add_chat_message",
    "add_watchlist",
    "get_cash",
    "get_db_path",
    "get_position",
    "init_db",
    "list_positions",
    "list_snapshots",
    "list_watchlist",
    "record_snapshot",
    "record_trade",
    "recent_chat_messages",
    "remove_watchlist",
    "save_position",
    "set_cash",
    "transaction",
]
