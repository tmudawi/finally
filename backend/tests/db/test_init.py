"""Tests for path resolution, initialization and seeding."""

from app import db
from app.db.connection import PROJECT_ROOT
from app.db.schema import DEFAULT_WATCHLIST


def test_db_path_from_env(fresh_db):
    assert db.get_db_path() == fresh_db


def test_db_path_default(monkeypatch):
    monkeypatch.delenv("DB_PATH")
    assert db.get_db_path() == PROJECT_ROOT / "db" / "finally.db"


def test_db_path_empty_env_uses_default(monkeypatch):
    monkeypatch.setenv("DB_PATH", "")
    assert db.get_db_path() == PROJECT_ROOT / "db" / "finally.db"


def test_init_creates_parent_dir_and_file(fresh_db):
    assert fresh_db.exists()


def test_seed_cash_and_watchlist():
    assert db.get_cash() == 10000.0
    assert db.list_watchlist() == DEFAULT_WATCHLIST


def test_init_is_idempotent_and_does_not_reseed():
    db.set_cash(500.0)
    db.remove_watchlist("AAPL")
    db.init_db()
    assert db.get_cash() == 500.0
    assert "AAPL" not in db.list_watchlist()


def test_empty_file_gets_initialized(tmp_path, monkeypatch):
    path = tmp_path / "empty.db"
    path.touch()
    monkeypatch.setenv("DB_PATH", str(path))
    db.init_db()
    assert db.get_cash() == 10000.0
