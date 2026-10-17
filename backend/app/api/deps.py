"""Shared route dependencies."""

from typing import Annotated

from fastapi import Depends, Request

from app.services import AppState


def get_state(request: Request) -> AppState:
    """The AppState created in the app lifespan."""
    return request.app.state.app_state


State = Annotated[AppState, Depends(get_state)]
