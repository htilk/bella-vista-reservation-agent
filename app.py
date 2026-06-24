"""Bella Vista reservation agent — FastAPI backend + static chat UI.

Run:  python app.py        (or: uvicorn app:app --reload)
Then open http://127.0.0.1:8000

The HTTP layer is deliberately thin: it owns sessions and serves the UI, but
all reservation logic lives behind the Agent/tools. The browser talks only to
POST /chat, so the agent could be swapped without touching the frontend.
"""
from __future__ import annotations

import logging
import os
import threading
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from reservation_agent import config
from reservation_agent.agent import Agent, get_brain
from reservation_agent.store import DEFAULT_PATH, ReservationStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-5s  %(name)s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("bella_vista.app")

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
COOKIE_NAME = "bv_session"
# The /api/reservations debug view returns guest PII; gate it off with BV_DEBUG=0.
DEBUG_API = os.environ.get("BV_DEBUG", "1").strip().lower() not in ("0", "false", "no", "off")

# One shared, persistent store for the whole server (reservations are global).
# Chat sessions only isolate per-guest *conversation* state.
STORE = ReservationStore(path=DEFAULT_PATH, seed=True)
SESSIONS: dict[str, Agent] = {}
_SESSIONS_LOCK = threading.Lock()

@asynccontextmanager
async def lifespan(_app: FastAPI):
    brain = get_brain()
    log.info("%s reservations agent ready.", config.RESTAURANT_NAME)
    log.info("Conversation brain: %s", getattr(brain, "name", type(brain).__name__))
    log.info("Reservation store: %s", STORE.path)
    yield


app = FastAPI(title="Bella Vista Reservations", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=2000)


def _get_or_create_session(sid: str | None) -> tuple[str, Agent]:
    with _SESSIONS_LOCK:
        if sid and sid in SESSIONS:
            return sid, SESSIONS[sid]
        new_sid = sid or uuid.uuid4().hex  # keep the cookie's id if the server restarted
        agent = Agent(STORE)
        SESSIONS[new_sid] = agent
        return new_sid, agent


@app.get("/")
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.post("/chat")
def chat(req: ChatRequest, request: Request, response: Response) -> dict:
    sid, agent = _get_or_create_session(request.cookies.get(COOKIE_NAME))
    response.set_cookie(COOKIE_NAME, sid, httponly=True, samesite="lax")
    log.info("CHAT [%s] guest: %s", sid[:8], req.message)
    try:
        result = agent.send(req.message)
    except Exception:  # never surface a raw 500 to the chat UI
        log.exception("CHAT [%s] handler error", sid[:8])
        return {"reply": "Sorry — something went wrong on my end. Could you try that again?",
                "confirmation_code": None}
    log.info("CHAT [%s] agent: %s", sid[:8], result["reply"])
    return result


@app.post("/api/reset")
def reset(response: Response) -> dict:
    """Start a fresh conversation (new session); reservations are untouched."""
    new_sid = uuid.uuid4().hex
    with _SESSIONS_LOCK:
        SESSIONS[new_sid] = Agent(STORE)
    response.set_cookie(COOKIE_NAME, new_sid, httponly=True, samesite="lax")
    return {"ok": True}


@app.get("/api/reservations")
def reservations() -> JSONResponse:
    """Read-only view of the data store — handy for verifying the demo.

    Returns guest PII, so it's a localhost debug aid only; set BV_DEBUG=0 to
    disable it (e.g. before any non-local deployment).
    """
    if not DEBUG_API:
        return JSONResponse({"error": "not found"}, status_code=404)
    data = [r.to_dict() for r in STORE.all_reservations()]
    return JSONResponse({"count": len(data), "reservations": data})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
