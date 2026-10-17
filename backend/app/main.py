"""FastAPI application: REST API, SSE stream and the static frontend."""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app import db, services
from app.api import chat, health, portfolio, watchlist
from app.market import PriceCache, create_market_data_source, create_stream_router

BACKEND_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BACKEND_DIR.parent / ".env")

SNAPSHOT_INTERVAL = 30.0  # Seconds between portfolio snapshots


async def snapshot_loop(state: services.AppState) -> None:
    """Record the portfolio value now and every SNAPSHOT_INTERVAL seconds."""
    while True:
        services.record_snapshot(state)
        await asyncio.sleep(SNAPSHOT_INTERVAL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize the database, start market data and the snapshot task."""
    db.init_db()
    source = create_market_data_source(app.state.cache)
    await source.start(services.tracked_tickers())
    state = services.AppState(cache=app.state.cache, source=source)
    app.state.app_state = state
    task = asyncio.create_task(snapshot_loop(state), name="snapshot-loop")
    yield
    task.cancel()
    await source.stop()


async def value_error_handler(request: Request, exc: ValueError) -> JSONResponse:
    """Validation failures from app.services become HTTP 400."""
    return JSONResponse(status_code=400, content={"detail": str(exc)})


def create_app() -> FastAPI:
    """Build the app; static files are mounted last so /api routes win."""
    app = FastAPI(title="FinAlly", lifespan=lifespan)
    app.state.cache = PriceCache()
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(ValueError, value_error_handler)
    app.include_router(create_stream_router(app.state.cache))
    for module in (portfolio, watchlist, chat, health):
        app.include_router(module.router)
    static_dir = Path(os.environ.get("STATIC_DIR") or BACKEND_DIR / "static")
    if static_dir.is_dir():
        app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
    return app


app = create_app()
