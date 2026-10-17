"""Chat route in LLM mock mode."""


def test_chat_plain_message(client):
    body = client.post("/api/chat", json={"message": "hi"}).json()
    assert body == {"message": "Mock: hello from FinAlly", "actions": []}


def test_chat_buy_executes_trade(client):
    body = client.post("/api/chat", json={"message": "Buy something"}).json()
    action = body["actions"][0]
    assert action["type"] == "trade"
    assert action["status"] == "ok"
    assert client.get("/api/portfolio").json()["positions"][0]["ticker"] == "AAPL"


def test_chat_watch_adds_ticker(client):
    client.post("/api/chat", json={"message": "watch PYPL"})
    tickers = [r["ticker"] for r in client.get("/api/watchlist").json()]
    assert "PYPL" in tickers


def test_chat_llm_error_returns_502(client, monkeypatch):
    import litellm

    async def fail(state, message):
        raise litellm.RateLimitError("quota exceeded", llm_provider="openrouter", model="m")

    monkeypatch.setattr("app.llm.run_chat", fail)
    resp = client.post("/api/chat", json={"message": "hi"})
    assert resp.status_code == 502
    assert "AI assistant unavailable" in resp.json()["detail"]
