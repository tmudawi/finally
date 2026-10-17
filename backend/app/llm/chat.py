"""Chat orchestration: context, one LLM call, action execution, persistence."""

from app import db, services

from .client import call_llm, is_mock, mock_response
from .prompt import build_messages, portfolio_context
from .schema import ChatResponse, TradeRequest, WatchlistChange


async def run_trade(state, trade: TradeRequest) -> dict:
    """Execute one trade and return its action result."""
    action = {"type": "trade", "ticker": trade.ticker.upper(), "side": trade.side,
              "quantity": trade.quantity}
    try:
        result = await services.execute_trade(state, trade.ticker, trade.side, trade.quantity)
    except ValueError as e:
        return {**action, "status": "error", "error": str(e)}
    return {**action, "status": "ok", "price": result["price"]}


async def run_watchlist_change(state, change: WatchlistChange) -> dict:
    """Apply one watchlist change and return its action result."""
    action = {"type": "watchlist", "ticker": change.ticker.upper(), "action": change.action}
    apply = services.add_ticker if change.action == "add" else services.remove_ticker
    try:
        await apply(state, change.ticker)
    except ValueError as e:
        return {**action, "status": "error", "error": str(e)}
    return {**action, "status": "ok"}


async def execute_actions(state, response: ChatResponse) -> list[dict]:
    """Run every trade then every watchlist change, recording each result."""
    actions = [await run_trade(state, t) for t in response.trades]
    actions += [await run_watchlist_change(state, c) for c in response.watchlist_changes]
    return actions


async def get_response(state, message: str) -> ChatResponse:
    """Return the mock response or call the LLM with full context."""
    if is_mock():
        return mock_response(message)
    context = portfolio_context(
        services.portfolio_summary(state), services.watchlist_with_prices(state)
    )
    messages = build_messages(context, db.recent_chat_messages(20), message)
    return await call_llm(messages)


async def run_chat(state, message: str) -> dict:
    """Handle one user chat message end to end; returns {message, actions}."""
    response = await get_response(state, message)
    actions = await execute_actions(state, response)
    db.add_chat_message("user", message, None)
    db.add_chat_message("assistant", response.message, actions)
    return {"message": response.message, "actions": actions}
