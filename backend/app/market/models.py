"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import UTC, datetime


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time."""

    ticker: str
    price: float
    previous_price: float
    timestamp: float = field(default_factory=time.time)  # Unix seconds
    # Basis for change_pct: seed / first-seen price (simulator) or previous close (Massive).
    # None means "use previous_price".
    reference_price: float | None = None

    @property
    def change(self) -> float:
        """Absolute price change from previous update."""
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        """Percentage change from previous update."""
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def change_pct(self) -> float:
        """Percentage change relative to the reference price (falls back to previous_price)."""
        ref = self.previous_price if self.reference_price is None else self.reference_price
        if ref == 0:
            return 0.0
        return round((self.price - ref) / ref * 100, 4)

    @property
    def direction(self) -> str:
        """'up', 'down', or 'flat'."""
        if self.price > self.previous_price:
            return "up"
        elif self.price < self.previous_price:
            return "down"
        return "flat"

    def to_dict(self) -> dict:
        """Serialize for JSON / SSE transmission."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
        }

    def to_sse_dict(self) -> dict:
        """Serialize in the PLAN.md SSE payload shape (the caller keys it by ticker)."""
        return {
            "price": self.price,
            "prev_price": self.previous_price,
            "change_pct": self.change_pct,
            "timestamp": datetime.fromtimestamp(self.timestamp, UTC).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            ),
            "direction": self.direction,
        }
