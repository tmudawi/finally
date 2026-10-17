"""Structured output schema for the LLM response."""

from typing import Literal

from pydantic import BaseModel, ValidationError


class TradeRequest(BaseModel):
    """A market order the LLM wants to execute."""

    ticker: str
    side: Literal["buy", "sell"]
    quantity: float


class WatchlistChange(BaseModel):
    """A watchlist add or remove the LLM wants to make."""

    ticker: str
    action: Literal["add", "remove"]


class ChatResponse(BaseModel):
    """The full structured response from the LLM.

    All fields are required (lists may be empty) so the schema works in strict mode.
    """

    message: str
    trades: list[TradeRequest]
    watchlist_changes: list[WatchlistChange]


FALLBACK_MESSAGE = "Sorry, I could not understand the AI response. Please try again."


def parse_response(content: str | None) -> ChatResponse:
    """Parse the LLM's JSON content, falling back to a no-action reply if malformed."""
    try:
        return ChatResponse.model_validate_json(content or "")
    except ValidationError:
        return ChatResponse(message=FALLBACK_MESSAGE, trades=[], watchlist_changes=[])
