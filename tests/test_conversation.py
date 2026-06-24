"""Regression tests for conversation bugs found in the adversarial review."""
from __future__ import annotations

import pytest

from reservation_agent import tools
from reservation_agent.agent import Agent
from reservation_agent.models import CONFIRMED
from reservation_agent.store import ReservationStore

from tests.conftest import TOMORROW, phone

FRIDAY = "2026-06-26"  # day after TOMORROW, an open day


@pytest.fixture
def bot(tmp_path, frozen_clock):
    return Agent(ReservationStore(path=tmp_path / "reservations.json", seed=True))


def reply(bot, text: str) -> str:
    return bot.send(text)["reply"]


# Finding 5 (HIGH): a stray "ok" must not override a negation on a destructive confirm.
def test_negation_beats_stray_affirmation_on_cancel(bot):
    first = reply(bot, "cancel BV-DEMO").lower()
    assert "cancel it" in first  # we're at the confirm prompt
    out = reply(bot, "ok no, don't cancel it").lower()
    assert "cancelled" not in out
    assert bot.store.get("BV-DEMO").status == CONFIRMED  # still confirmed


# Finding 3: an abort word must escape the cancel/modify code prompt, not loop.
def test_abort_escapes_cancel_target(bot):
    reply(bot, "I'd like to cancel a reservation")  # -> asks for a code
    out = reply(bot, "never mind").lower()
    assert "never mind" in out or "anything else" in out
    assert bot.brain  # flow reset; next message starts clean
    assert "confirmation code" not in reply(bot, "hello").lower()


def test_no_exits_cancel_target(bot):
    reply(bot, "cancel a reservation")
    out = reply(bot, "no").lower()
    assert "never mind" in out or "anything else" in out


# Finding 4: a date change while picking an alternative is a correction, not a pick.
def test_date_correction_during_alternatives(bot):
    # Fill both six-tops at TOMORROW 20:00 so a party of 6 is full there.
    tools.create_reservation(bot.store, date=TOMORROW, time="20:00", party_size=6,
                             guest_name="A", phone=phone(1))
    tools.create_reservation(bot.store, date=TOMORROW, time="20:00", party_size=6,
                             guest_name="B", phone=phone(2))
    assert "fully booked" in reply(bot, "table for 6 tomorrow at 8pm").lower()
    # Correct the DATE while we're choosing an alternative time.
    out = reply(bot, "how about Friday at 8pm instead").lower()
    assert "june 26" in out and "available" in out  # moved to Friday, not booked on the original day
