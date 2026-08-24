import os
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.db import get_connection, init_db
from app.seed import seed
from app.models import AccessCodeRequest, ChatRequest, ChatResponse, SpeakRequest, new_session_id
from app.orchestrator import run_turn, get_session_trace
from app.observability import init_langfuse
from app.voice import synthesize_speech

app = FastAPI(title="Bookly Support Agent")

STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static")


@app.on_event("startup")
def startup() -> None:
    conn = get_connection()
    init_db(conn)
    seed(conn)
    conn.close()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "WARNING: ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add "
            "your key before sending a chat message."
        )
    if not os.environ.get("BOOKLY_ACCESS_CODE"):
        print(
            "NOTE: BOOKLY_ACCESS_CODE is not set -- /chat and /trace are open, no passcode "
            "gate. Set it before deploying anywhere public."
        )
    if init_langfuse():
        print("Langfuse tracing enabled.")
    elif os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"):
        # Keys are set but init_langfuse() still returned False -- that's a
        # credential/host problem (see the WARNING logged just above this),
        # not a missing-config one. Saying "unset" here would be actively
        # wrong and send you looking in the wrong place.
        print("NOTE: Langfuse keys are set but failed to authenticate -- see the WARNING above "
              "for the specific reason (a swapped/malformed key or a wrong LANGFUSE_BASE_URL "
              "are the two most common causes) -- continuing with the local agent_traces "
              "table only.")
    else:
        print("NOTE: Langfuse not configured (LANGFUSE_PUBLIC_KEY/LANGFUSE_SECRET_KEY unset) "
              "-- continuing with the local agent_traces table only.")


def require_access_code(
    x_access_code: Optional[str] = Header(default=None),
    code: Optional[str] = Query(default=None),
) -> None:
    """Gate on a shared passcode -- ONLY when BOOKLY_ACCESS_CODE is configured.
    Left unset for local dev (no gate); set as a secret on any public deploy
    so a shared demo link doesn't let anyone burn the real Claude API key.
    Accepts the code via header (chat requests) or query param (the plain
    <a href> trace link can't set custom headers). Not real auth -- a
    documented scope decision, see README."""
    expected = os.environ.get("BOOKLY_ACCESS_CODE")
    if not expected:
        return  # gate disabled (local dev default)
    if x_access_code != expected and code != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing access code.")


@app.post("/verify-code")
def verify_code(req: AccessCodeRequest) -> dict:
    """Lets the frontend check a code before showing the chat UI, without
    needing a real chat turn (and therefore a Claude API call) just to find
    out the code was wrong."""
    expected = os.environ.get("BOOKLY_ACCESS_CODE")
    if not expected:
        return {"ok": True, "gate_enabled": False}
    return {"ok": req.code == expected, "gate_enabled": True}


@app.post("/chat", response_model=ChatResponse, dependencies=[Depends(require_access_code)])
def chat(req: ChatRequest) -> ChatResponse:
    session_id = req.session_id or new_session_id()
    conn = get_connection()
    try:
        result = run_turn(conn, session_id, req.message, voice=req.voice)
    except Exception as e:  # pragma: no cover - defensive top-level handler
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
    return ChatResponse(reply=result["reply"], session_id=session_id)


@app.post("/speak", dependencies=[Depends(require_access_code)])
def speak(req: SpeakRequest) -> Response:
    """Server-side ElevenLabs TTS. Returns 204 (no body) when ElevenLabs
    isn't configured or the call failed for any reason -- the frontend
    treats that as "fall back to browser speechSynthesis", not an error."""
    audio = synthesize_speech(req.text)
    if audio is None:
        return Response(status_code=204)
    return Response(content=audio, media_type="audio/mpeg")


@app.get("/trace/{session_id}", dependencies=[Depends(require_access_code)])
def trace(session_id: str) -> list[dict]:
    """Read-only observability view: the full masked trace for one session.
    Stand-in for what a real tracing dashboard (Langfuse/Datadog/OTel) would
    show in production -- see README. Gated the same as /chat since it shows
    conversation content (masked, but still gated for consistency)."""
    conn = get_connection()
    try:
        return get_session_trace(conn, session_id)
    finally:
        conn.close()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
