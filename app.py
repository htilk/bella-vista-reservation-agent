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
import time
import uuid
from collections import deque
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

# Load a local .env (if present) so ANTHROPIC_API_KEY / OPENAI_API_KEY / BV_*
# can be set there without exporting them. Does nothing if .env is absent.
load_dotenv(Path(__file__).resolve().parent / ".env")

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
# The /api/reservations debug view returns guest PII, so it's OFF by default.
# Opt in for local debugging with BV_DEBUG=1 (e.g. `BV_DEBUG=1 python app.py`).
DEBUG_API = os.environ.get("BV_DEBUG", "0").strip().lower() in ("1", "true", "yes", "on")

# Per-session rate limit for POST /chat: at most RATE_LIMIT_MAX messages within
# RATE_LIMIT_WINDOW seconds. Cheap in-memory sliding window keyed by session id;
# also blunts confirmation-code enumeration, which goes through /chat.
RATE_LIMIT_MAX = int(os.environ.get("BV_RATE_LIMIT_MAX", "20"))
RATE_LIMIT_WINDOW = float(os.environ.get("BV_RATE_LIMIT_WINDOW", "30"))
_RATE_HITS: dict[str, deque[float]] = {}
_RATE_LOCK = threading.Lock()


def _rate_limited(sid: str) -> bool:
    """Record a hit for ``sid`` and report whether it exceeds the window budget."""
    now = time.monotonic()
    cutoff = now - RATE_LIMIT_WINDOW
    with _RATE_LOCK:
        hits = _RATE_HITS.setdefault(sid, deque())
        while hits and hits[0] < cutoff:
            hits.popleft()
        if len(hits) >= RATE_LIMIT_MAX:
            return True
        hits.append(now)
        return False


# Conservative headers for every response. The UI only loads its own same-origin
# assets, so a strict CSP doesn't break anything.
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'self'; base-uri 'self'; frame-ancestors 'none'",
}

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


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    for header, value in SECURITY_HEADERS.items():
        response.headers.setdefault(header, value)
    return response


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
    if _rate_limited(sid):
        log.warning("CHAT [%s] rate limited", sid[:8])
        response.status_code = 429
        return {"reply": "You're sending messages a little too quickly — give me a moment and try again.",
                "confirmation_code": None}
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

    Returns guest PII, so it's OFF by default; opt in for local debugging with
    BV_DEBUG=1. Never enable it on a non-local deployment.
    """
    if not DEBUG_API:
        return JSONResponse({"error": "not found"}, status_code=404)
    data = [r.to_dict() for r in STORE.all_reservations()]
    return JSONResponse({"count": len(data), "reservations": data})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
