"""
The agent's reasoning loop: hand-rolled Claude tool-use, no framework.

call model -> if stop_reason == "tool_use": run the tool(s), feed results
back -> repeat until the model returns plain text (or we hit the iteration
cap, which exists so a confused model can't loop forever).

Session state (message history + verified_order_id + any pending return) is
kept in memory, keyed by session_id. A production deployment would back this
with Redis or a DB-backed session store instead -- see README "What I'd do
differently".
"""

import json
import os
import time
from datetime import datetime
from typing import Any, Optional

from anthropic import Anthropic

from app.prompts import SYSTEM_PROMPT
from app.security import redact, scrub_text
from app.tools import TOOL_SCHEMAS, dispatch_tool

MAX_TOOL_ITERATIONS = 5
MODEL = os.environ.get("BOOKLY_MODEL", "claude-sonnet-4-5-20250929")

# Cheap keyword guardrail for obvious prompt-injection / scope-break attempts.
# This does NOT block the message -- the system prompt instructs the model to
# handle out-of-scope/override requests gracefully on its own -- it exists so
# the attempt is flagged and visible in the observability trace.
_INJECTION_PATTERNS = [
    "ignore previous instructions",
    "ignore your instructions",
    "ignore all previous",
    "disregard the above",
    "you are now",
    "reveal your instructions",
    "reveal your system prompt",
    "jailbreak",
]

_client: Optional[Anthropic] = None


def get_client() -> Anthropic:
    global _client
    if _client is None:
        _client = Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
    return _client


# In-memory session store: session_id -> {messages, verified_order_id, pending_return, turn_index}
SESSIONS: dict[str, dict[str, Any]] = {}


def _get_session(session_id: str) -> dict[str, Any]:
    if session_id not in SESSIONS:
        SESSIONS[session_id] = {
            "messages": [],
            "verified_order_id": None,
            "pending_return": None,
            "turn_index": 0,
        }
    return SESSIONS[session_id]


def reset_session(session_id: str) -> None:
    SESSIONS.pop(session_id, None)


def _check_injection(text: str) -> bool:
    lowered = text.lower()
    return any(p in lowered for p in _INJECTION_PATTERNS)


def _extract_text(response) -> str:
    parts = [b.text for b in response.content if getattr(b, "type", None) == "text"]
    return "\n".join(parts).strip()


def _log_trace(
    conn,
    session_id: str,
    turn_index: int,
    role: str,
    content: Optional[str] = None,
    tool_name: Optional[str] = None,
    tool_args: Optional[dict] = None,
    tool_result: Optional[dict] = None,
    latency_ms: Optional[int] = None,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    guardrail_flag: Optional[str] = None,
) -> None:
    conn.execute(
        """INSERT INTO agent_traces
           (session_id, turn_index, role, content, tool_name, tool_args, tool_result,
            latency_ms, input_tokens, output_tokens, guardrail_flag, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            session_id,
            turn_index,
            role,
            scrub_text(content),
            tool_name,
            json.dumps(redact(tool_args)) if tool_args is not None else None,
            json.dumps(redact(tool_result)) if tool_result is not None else None,
            latency_ms,
            input_tokens,
            output_tokens,
            guardrail_flag,
            datetime.now().isoformat(),
        ),
    )
    conn.commit()


def get_session_trace(conn, session_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM agent_traces WHERE session_id = ? ORDER BY id ASC", (session_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def run_turn(conn, session_id: str, user_message: str) -> dict:
    session = _get_session(session_id)
    session["turn_index"] += 1
    turn_index = session["turn_index"]

    if _check_injection(user_message):
        _log_trace(
            conn, session_id, turn_index, "guardrail",
            content=user_message, guardrail_flag="possible_injection_or_scope",
        )

    session["messages"].append({"role": "user", "content": user_message})
    _log_trace(conn, session_id, turn_index, "user", content=user_message)

    iterations = 0
    while True:
        iterations += 1
        if iterations > MAX_TOOL_ITERATIONS:
            fallback = (
                "I'm having trouble completing that request. Let me connect you with "
                "a human agent instead."
            )
            session["messages"].append({"role": "assistant", "content": fallback})
            _log_trace(
                conn, session_id, turn_index, "guardrail",
                content=fallback, guardrail_flag="tool_loop_cap_hit",
            )
            return {"reply": fallback, "session_id": session_id}

        start = time.monotonic()
        response = get_client().messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOL_SCHEMAS,
            messages=session["messages"],
        )
        latency_ms = int((time.monotonic() - start) * 1000)

        _log_trace(
            conn, session_id, turn_index, "assistant",
            content=_extract_text(response),
            latency_ms=latency_ms,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )

        session["messages"].append({"role": "assistant", "content": response.content})

        if response.stop_reason != "tool_use":
            return {"reply": _extract_text(response), "session_id": session_id}

        tool_results = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            result = dispatch_tool(block.name, block.input, conn, session)
            _log_trace(
                conn, session_id, turn_index, "tool_call",
                tool_name=block.name, tool_args=block.input, tool_result=result,
            )
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(result),
                }
            )

        session["messages"].append({"role": "user", "content": tool_results})
