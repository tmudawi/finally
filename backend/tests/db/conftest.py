"""Fixtures giving each test a fresh, initialized database file."""

import pytest

from app import db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Point DB_PATH at a temp file and initialize it."""
    path = tmp_path / "nested" / "finally.db"
    monkeypatch.setenv("DB_PATH", str(path))
    db.init_db()
    return path
