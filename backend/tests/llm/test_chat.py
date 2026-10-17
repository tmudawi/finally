"""Tests for run_chat orchestration with faked services, db and LLM."""

from types import SimpleNamespace

from app.llm import chat, client, run_chat
from app.llm.schema import ChatResponse


def fake_completion(content):
    """Build an acompletion stand-in that returns `content` and captures kwargs."""
    captured = {}

    async def acompletion(**kwargs):
        captured.update(kwargs)
        message = SimpleNamespace(content=content)
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return acompletion, captured


async def test_mock_buy_executes_trade(monkeypatch, services, db):
    monkeypatch.setenv("LLM_MOCK", "true")
    result = await run_chat(None, "buy apple")
    assert result == {
        "message": "Mock: buying 1 AAPL",
        "actions": [{"type": "trade", "ticker": "AAPL", "side": "buy", "quantity": 1,
                     "status": "ok", "price": 190.0}],
    }
    assert services.calls == [("trade", "AAPL", "buy", 1)]


async def test_mock_watch_adds_ticker(monkeypatch, services, db):
    monkeypatch.setenv("LLM_MOCK", "true")
    result = await run_chat(None, "watch paypal")
    assert result["actions"] == [
        {"type": "watchlist", "ticker": "PYPL", "action": "add", "status": "ok"}
    ]


async def test_failed_trade_recorded_as_error(monkeypatch, services, db):
    monkeypatch.setenv("LLM_MOCK", "true")
    services.fail.add("AAPL")
    action = (await run_chat(None, "buy"))["actions"][0]
    assert action["status"] == "error"
    assert action["error"] == "Insufficient cash"
    assert "price" not in action


async def test_failed_watchlist_change_recorded(monkeypatch, services, db):
    monkeypatch.setenv("LLM_MOCK", "true")
    services.fail.add("PYPL")
    action = (await run_chat(None, "watch"))["actions"][0]
    assert action["status"] == "error"
    assert action["error"] == "Unknown ticker"


async def test_messages_stored(monkeypatch, services, db):
    monkeypatch.setenv("LLM_MOCK", "true")
    result = await run_chat(None, "buy")
    assert db.messages == [
        {"role": "user", "content": "buy", "actions": None},
        {"role": "assistant", "content": "Mock: buying 1 AAPL", "actions": result["actions"]},
    ]


async def test_one_action_failure_does_not_stop_others(monkeypatch, services, db):
    monkeypatch.delenv("LLM_MOCK", raising=False)
    response = ChatResponse.model_validate({
        "message": "ok",
        "trades": [{"ticker": "TSLA", "side": "buy", "quantity": 1},
                   {"ticker": "AAPL", "side": "sell", "quantity": 2}],
        "watchlist_changes": [{"ticker": "NFLX", "action": "remove"}],
    })

    async def fake_call(messages):
        return response

    monkeypatch.setattr(chat, "call_llm", fake_call)
    services.fail.add("TSLA")
    result = await run_chat(None, "trade")
    assert [a["status"] for a in result["actions"]] == ["error", "ok", "ok"]
    assert services.calls[-1] == ("remove", "NFLX")


async def test_real_path_sends_context_and_history(monkeypatch, services, db):
    monkeypatch.delenv("LLM_MOCK", raising=False)
    db.messages = [{"role": "user", "content": "earlier", "actions": None}]
    acompletion, captured = fake_completion(
        '{"message": "Hi", "trades": [], "watchlist_changes": []}'
    )
    monkeypatch.setattr(client, "acompletion", acompletion)
    result = await run_chat(None, "how am I doing?")
    assert result == {"message": "Hi", "actions": []}
    assert captured["model"] == "openrouter/openai/gpt-oss-120b"
    assert captured["response_format"] is ChatResponse
    assert captured["extra_body"] == {"provider": {"order": ["cerebras"], "allow_fallbacks": False}}
    assert captured["max_tokens"] == 2000
    msgs = captured["messages"]
    assert msgs[0]["role"] == "system"
    assert msgs[1] == {"role": "user", "content": "earlier"}
    assert "10000.0" in msgs[-2]["content"]
    assert msgs[-1] == {"role": "user", "content": "how am I doing?"}


async def test_malformed_llm_reply_returns_fallback(monkeypatch, services, db):
    monkeypatch.delenv("LLM_MOCK", raising=False)
    acompletion, _ = fake_completion("{oops")
    monkeypatch.setattr(client, "acompletion", acompletion)
    result = await run_chat(None, "hi")
    assert result["actions"] == []
    assert "could not understand" in result["message"]
