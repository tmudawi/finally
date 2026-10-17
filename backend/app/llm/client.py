"""LLM call via LiteLLM -> OpenRouter (Cerebras), plus the deterministic mock."""

import os

from litellm import acompletion

from .schema import ChatResponse, TradeRequest, WatchlistChange, parse_response

MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"], "allow_fallbacks": False}}
MAX_TOKENS = 2000  # Without this, OpenRouter reserves 40960 tokens and may fall back off Cerebras


def is_mock() -> bool:
    """True when LLM_MOCK=true."""
    return os.environ.get("LLM_MOCK", "").strip().lower() == "true"


def mock_response(message: str) -> ChatResponse:
    """Keyword-based deterministic response (PLAN.md section 9)."""
    text = message.lower()
    if "buy" in text:
        trade = TradeRequest(ticker="AAPL", side="buy", quantity=1)
        return ChatResponse(message="Mock: buying 1 AAPL", trades=[trade], watchlist_changes=[])
    if "sell" in text:
        trade = TradeRequest(ticker="AAPL", side="sell", quantity=1)
        return ChatResponse(message="Mock: selling 1 AAPL", trades=[trade], watchlist_changes=[])
    if "watch" in text:
        change = WatchlistChange(ticker="PYPL", action="add")
        return ChatResponse(message="Mock: adding PYPL", trades=[], watchlist_changes=[change])
    return ChatResponse(message="Mock: hello from FinAlly", trades=[], watchlist_changes=[])


async def call_llm(messages: list[dict]) -> ChatResponse:
    """Make one structured-output LLM call and parse the result."""
    response = await acompletion(
        model=MODEL,
        messages=messages,
        response_format=ChatResponse,
        reasoning_effort="low",
        max_tokens=MAX_TOKENS,
        extra_body=EXTRA_BODY,
    )
    return parse_response(response.choices[0].message.content)
