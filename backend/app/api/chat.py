"""Chat route backed by the LLM assistant."""

from fastapi import APIRouter, HTTPException
from openai import APIError
from pydantic import BaseModel

from app import llm
from app.api.deps import State

router = APIRouter(prefix="/api", tags=["chat"])


class ChatRequest(BaseModel):
    """A user chat message."""

    message: str


@router.post("/chat")
async def chat(body: ChatRequest, state: State) -> dict:
    """Send a message to FinAlly; returns {message, actions}.

    LiteLLM errors (all subclasses of openai.APIError) become HTTP 502.
    """
    try:
        return await llm.run_chat(state, body.message)
    except APIError as e:
        raise HTTPException(502, f"AI assistant unavailable: {e.message}") from e
