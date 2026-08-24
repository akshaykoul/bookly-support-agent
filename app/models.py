import uuid
from typing import Optional

from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    message: str
    session_id: Optional[str] = None


class AccessCodeRequest(BaseModel):
    code: str


class SpeakRequest(BaseModel):
    text: str


class ChatResponse(BaseModel):
    reply: str
    session_id: str


def new_session_id() -> str:
    return uuid.uuid4().hex[:12]
