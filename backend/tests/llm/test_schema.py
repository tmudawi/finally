"""Tests for structured output parsing."""

from app.llm.schema import FALLBACK_MESSAGE, ChatResponse, parse_response


def test_parse_full_response():
    content = (
        '{"message": "Done", "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 2.5}],'
        ' "watchlist_changes": [{"ticker": "PYPL", "action": "remove"}]}'
    )
    r = parse_response(content)
    assert r.message == "Done"
    assert r.trades[0].quantity == 2.5
    assert r.watchlist_changes[0].action == "remove"


def test_parse_empty_lists():
    r = parse_response('{"message": "Hi", "trades": [], "watchlist_changes": []}')
    assert r.trades == [] and r.watchlist_changes == []


def test_malformed_json_falls_back():
    r = parse_response("not json")
    assert r.message == FALLBACK_MESSAGE
    assert r.trades == []


def test_invalid_side_falls_back():
    content = (
        '{"message": "x", "trades": [{"ticker": "AAPL", "side": "hold", "quantity": 1}],'
        ' "watchlist_changes": []}'
    )
    assert parse_response(content).message == FALLBACK_MESSAGE


def test_none_content_falls_back():
    assert parse_response(None).message == FALLBACK_MESSAGE


def test_schema_marks_all_fields_required():
    required = set(ChatResponse.model_json_schema()["required"])
    assert required == {"message", "trades", "watchlist_changes"}
