"""Prompt construction for the chat assistant."""

import json

SYSTEM_PROMPT = """You are FinAlly, an AI trading assistant inside a simulated trading workstation.
You can:
- Analyze portfolio composition, risk concentration and P&L
- Suggest trades with brief reasoning
- Execute market orders when the user asks or agrees (add them to "trades")
- Manage the watchlist proactively (add them to "watchlist_changes")
Be concise and data-driven. Tickers are uppercase symbols. Quantities are share counts.
Always respond with valid JSON matching the schema: "message" (text shown to the user),
"trades" (list, may be empty) and "watchlist_changes" (list, may be empty).
Only include trades the user asked for or agreed to."""


def portfolio_context(portfolio: dict, watchlist: list[dict]) -> str:
    """Render the live portfolio and watchlist as a context message."""
    return (
        "Current portfolio (cash, total value, positions with P&L):\n"
        f"{json.dumps(portfolio)}\n\n"
        "Watchlist with live prices:\n"
        f"{json.dumps(watchlist)}"
    )


def build_messages(context: str, history: list[dict], message: str) -> list[dict]:
    """Assemble system prompt, prior conversation, live context and the new user message."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "system", "content": context})
    messages.append({"role": "user", "content": message})
    return messages
