"""Health, CORS, static serving and startup."""

from fastapi.testclient import TestClient

from app.main import create_app


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_static_files_served(env, tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<h1>FinAlly</h1>")
    env.setenv("STATIC_DIR", str(static))
    with TestClient(create_app()) as client:
        assert "FinAlly" in client.get("/").text
        assert client.get("/api/health").json() == {"status": "ok"}


def test_cors_allows_dev_origin_only(client):
    ok = client.get("/api/health", headers={"Origin": "http://localhost:3000"})
    bad = client.get("/api/health", headers={"Origin": "http://evil.example"})
    assert ok.headers["access-control-allow-origin"] == "http://localhost:3000"
    assert "access-control-allow-origin" not in bad.headers


def test_startup_snapshot_recorded(client):
    history = client.get("/api/portfolio/history").json()
    assert history[0]["total_value"] == 10000.0
