"""Portfolio routes: summary, trades and value history."""

from fastapi import APIRouter
from pydantic import BaseModel

from app import db, services
from app.api.deps import State

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


class TradeRequest(BaseModel):
    """A market order."""

    ticker: str
    quantity: float
    side: str


@router.get("")
def get_portfolio(state: State) -> dict:
    """Cash, positions, total value and unrealized P&L."""
    return services.portfolio_summary(state)


@router.post("/trade")
async def trade(body: TradeRequest, state: State) -> dict:
    """Execute a market order and return it with the updated portfolio."""
    result = await services.execute_trade(state, body.ticker, body.side, body.quantity)
    return {"trade": result, "portfolio": services.portfolio_summary(state)}


@router.get("/history")
def history() -> list[dict]:
    """Portfolio value snapshots, oldest first."""
    return db.list_snapshots()
