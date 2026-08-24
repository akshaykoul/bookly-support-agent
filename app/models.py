import uuid
from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None
    # True when this turn came from Voice mode in the UI -- lets the
    # orchestrator use a shorter, spoken-conversation system prompt and a
    # lower max_tokens instead of the normal written-reply behavior.
    voice: bool = False


class AccessCodeRequest(BaseModel):
    code: str


class SpeakRequest(BaseModel):
    text: str


class ChatResponse(BaseModel):
    reply: str
    session_id: str


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]
