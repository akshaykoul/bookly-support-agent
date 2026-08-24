import os

from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.db import get_connection, init_db
from app.seed import seed
from app.models import ChatRequest, ChatResponse, new_session_id
from app.orchestrator import run_turn, get_session_trace

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


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest) -> ChatResponse:
    session_id = req.session_id or new_session_id()
    conn = get_connection()
    try:
        result = run_turn(conn, session_id, req.message)
    except Exception as e:  # pragma: no cover - defensive top-level handler
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        conn.close()
    return ChatResponse(reply=result["reply"], session_id=session_id)


@app.get("/trace/{session_id}")
def trace(session_id: str) -> list[dict]:
    """Read-only observability view: the full masked trace for one session.
    Stand-in for what a real tracing dashboard (Langfuse/Datadog/OTel) would
    show in production -- see README."""
    conn = get_connection()
    try:
        return get_session_trace(conn, session_id)
    finally:
        conn.close()


@app.get("/")
def index() -> FileResponse:
    return FileResponse(os.path.join(STATIC_DIR, "index.html"))


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
