"""Shared fixtures: a frozen clock and a fresh temp-file store per test.

The clock is frozen to Wed 2026-06-24 12:00 so every date-based rule (past /
horizon / closed-Monday) and the BV-DEMO 'tomorrow' seed are deterministic.
"""
from __future__ import annotations

import datetime as dt
import os
import tempfile

# Relocate the default store to a throwaway file BEFORE the store module is
# imported, so the test suite never touches the real data/reservations.json.
os.environ.setdefault("BV_DATA_FILE", os.path.join(tempfile.mkdtemp(), "reservations.json"))

import pytest

from reservation_agent import clock
from reservation_agent.store import ReservationStore

# Wednesday, noon — same "today" the exercise was written against.
FROZEN_NOW = dt.datetime(2026, 6, 24, 12, 0, 0)
TOMORROW = (FROZEN_NOW.date() + dt.timedelta(days=1)).isoformat()  # 2026-06-25 (Thu, open)


@pytest.fixture
def frozen_clock():
    clock.set_now(FROZEN_NOW)
    try:
        yield FROZEN_NOW
    finally:
        clock.reset_now()


@pytest.fixture
def store(tmp_path, frozen_clock):
    """A seeded store backed by a throwaway file (so BV-DEMO exists)."""
    return ReservationStore(path=tmp_path / "reservations.json", seed=True)


def phone(n: int) -> str:
    """Distinct valid phone numbers so BR-8 (one/date/guest) doesn't collide."""
    return f"555-1{n:03d}"
