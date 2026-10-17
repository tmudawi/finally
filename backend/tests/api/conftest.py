"""API test fixtures: a fresh app on a temporary database."""

import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated environment: temp DB, simulator, mock LLM, no static dir."""
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("MASSIVE_API_KEY", "")
    monkeypatch.setenv("LLM_MOCK", "true")
    monkeypatch.setenv("STATIC_DIR", str(tmp_path / "no-static"))
    return monkeypatch


@pytest.fixture
def client(env):
    """TestClient with the lifespan running."""
    with TestClient(create_app()) as client:
        yield client


def price_of(client, ticker: str) -> float:
    """Current cached price for a ticker."""
    return client.app.state.cache.get_price(ticker)
