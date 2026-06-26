"""Integration tests for the FastAPI layer: /chat sessions, refusals, endpoints.

These run on the deterministic brain (no API key in the test env), so they're
fully deterministic. Each test gets a fresh temp store and a frozen clock.
"""
from __future__ import annotations

import os
import re
import tempfile

# Point the app's module-level store at a throwaway file BEFORE importing app,
# so importing it never writes the real data/ store.
os.environ["BV_DATA_FILE"] = os.path.join(tempfile.mkdtemp(), "reservations.json")
# The PII debug view is off by default; these tests use it to inspect the store,
# so opt in before importing app (DEBUG_API is read at import time).
os.environ["BV_DEBUG"] = "1"

import pytest
from fastapi.testclient import TestClient

import app as appmod
from reservation_agent import clock
from reservation_agent.store import ReservationStore

from tests.conftest import FROZEN_NOW, TOMORROW

CODE_RE = re.compile(r"\bBV-[A-HJ-NP-Z2-9]{4}\b")


@pytest.fixture
def client(tmp_path):
    clock.set_now(FROZEN_NOW)
    appmod.STORE = ReservationStore(path=tmp_path / "reservations.json", seed=True)
    appmod.SESSIONS.clear()
    with TestClient(appmod.app) as c:
        yield c
    appmod.SESSIONS.clear()
    clock.reset_now()


def say(client, text: str) -> dict:
    res = client.post("/chat", json={"message": text})
    assert res.status_code == 200, res.text
    return res.json()


# ---------------------------------------------------------------- serving
def test_index_is_served(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "Bella Vista" in res.text


def test_seed_reservation_visible(client):
    res = client.get("/api/reservations")
    codes = [r["confirmation_code"] for r in res.json()["reservations"]]
    assert "BV-DEMO" in codes


# ---------------------------------------------------------------- US-1 end to end
def test_booking_happy_path_persists(client):
    say(client, "I'd like to book a table for Saturday at 7pm")
    say(client, "4 of us")
    final = say(client, "Alex Rivera, 555-0123. It's our anniversary.")
    assert "confirmation code" in final["reply"].lower()
    code = final["confirmation_code"]
    assert code and CODE_RE.match(code)

    # It really landed in the store with the right shape.
    res = client.get("/api/reservations").json()["reservations"]
    booked = next(r for r in res if r["confirmation_code"] == code)
    assert booked["party_size"] == 4
    assert booked["guest_name"] == "Alex Rivera"
    assert booked["status"] == "confirmed"
    assert "anniversary" in booked["notes"].lower()


# ---------------------------------------------------------------- US-2 alternatives
def test_full_slot_offers_alternatives(client):
    # Fill both six-tops at 20:00 directly through the store's tools.
    from reservation_agent import tools
    tools.create_reservation(appmod.STORE, date=TOMORROW, time="20:00", party_size=6,
                             guest_name="A", phone="555-1111")
    tools.create_reservation(appmod.STORE, date=TOMORROW, time="20:00", party_size=6,
                             guest_name="B", phone="555-2222")
    reply = say(client, "Table for 6 tomorrow at 8pm")["reply"].lower()
    assert "fully booked" in reply
    assert "pm" in reply  # offers at least one concrete alternative time


# ---------------------------------------------------------------- US-4 cancel
def test_cancel_demo_flow(client):
    first = say(client, "I need to cancel BV-DEMO")["reply"].lower()
    assert "alex rivera" in first and "cancel it" in first
    done = say(client, "yes")["reply"].lower()
    assert "cancelled" in done
    demo = next(r for r in client.get("/api/reservations").json()["reservations"]
                if r["confirmation_code"] == "BV-DEMO")
    assert demo["status"] == "cancelled"


# ---------------------------------------------------------------- US-6 refusals
@pytest.mark.parametrize("ask", [
    "How much is the lasagna?",
    "Can I order a pizza for delivery?",
    "What's on the menu tonight?",
])
def test_out_of_scope_is_refused(client, ask):
    reply = say(client, ask)["reply"].lower()
    assert "can't help" in reply or "reservations assistant" in reply
    assert "555-0100" in reply  # redirected to the restaurant


# ---------------------------------------------------------------- sessions
def test_sessions_are_isolated(client):
    # Client 1 starts a booking.
    say(client, "Book a table tomorrow at 6pm for 2")
    # A second, cookie-independent client should NOT inherit that context.
    with TestClient(appmod.app) as other:
        reply = other.post("/chat", json={"message": "what's my reservation?"}).json()["reply"]
    assert "6:00pm" not in reply  # no leakage of client 1's in-progress booking


def test_reset_starts_fresh(client):
    say(client, "Book a table tomorrow at 6pm for 2")
    assert client.post("/api/reset").json()["ok"] is True
    # After reset, the agent shouldn't think a booking is mid-flight.
    reply = say(client, "hello")["reply"].lower()
    assert "how many" not in reply
