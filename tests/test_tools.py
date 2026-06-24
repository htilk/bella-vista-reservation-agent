"""Tool contracts: create/get/modify/cancel/check_availability + persistence."""
from __future__ import annotations

import re

import pytest

from reservation_agent import tools
from reservation_agent.errors import NotFoundError, UnavailableError, ValidationError
from reservation_agent.models import CANCELLED, CONFIRMED, SCHEMA_FIELDS
from reservation_agent.store import ReservationStore

from tests.conftest import TOMORROW, phone

CODE_RE = re.compile(r"^BV-[A-HJ-NP-Z2-9]{4}$")


def _book(store, **kw):
    base = dict(date=TOMORROW, time="18:00", party_size=2,
                guest_name="Alex Rivera", phone=phone(0))
    base.update(kw)
    return tools.create_reservation(store, **base)


# ---------------------------------------------------------------- create / schema
def test_create_returns_conformant_reservation(store):
    res = _book(store, notes="anniversary")
    assert CODE_RE.match(res.confirmation_code)
    assert res.status == CONFIRMED
    d = res.to_dict()
    assert list(d.keys()) == list(SCHEMA_FIELDS)         # exact §9 schema + order
    assert d["notes"] == "anniversary"
    assert d["created_at"].endswith("Z")


def test_create_persists_across_restart(store, tmp_path):
    res = _book(store)
    # Re-open the same file with a brand-new store object (simulates a restart).
    reopened = ReservationStore(path=store.path, seed=False)
    again = reopened.get(res.confirmation_code)
    assert again is not None
    assert again.to_dict() == res.to_dict()


def test_seed_demo_reservation_present(store):
    demo = store.get("BV-DEMO")
    assert demo is not None
    assert demo.guest_name == "Alex Rivera"
    assert demo.date == TOMORROW and demo.time == "19:00" and demo.party_size == 4


# ---------------------------------------------------------------- check_availability
def test_check_availability_open_slot(store):
    r = tools.check_availability(store, TOMORROW, "20:00", 4)
    assert r["available"] is True and r["reason"] is None


def test_check_availability_full_offers_real_alternatives(store):
    # Fill both six-tops at 20:00 so a party of 6 is "fully booked" there.
    _book(store, time="20:00", party_size=6, phone=phone(1))
    _book(store, time="20:00", party_size=6, phone=phone(2))
    r = tools.check_availability(store, TOMORROW, "20:00", 6)
    assert r["available"] is False
    assert "fully booked" in r["reason"]
    assert len(r["alternatives"]) >= 2                    # US-2: at least two
    for alt in r["alternatives"]:
        # Each alternative must be REAL: genuinely bookable for a party of 6.
        check = tools.check_availability(store, TOMORROW, alt, 6)
        assert check["available"] is True


def test_check_availability_reports_rule_reason(store):
    r = tools.check_availability(store, TOMORROW, "18:15", 2)   # bad slot
    assert r["available"] is False and "30-minute" in r["reason"]


# ---------------------------------------------------------------- get
def test_get_unknown_returns_none(store):
    assert tools.get_reservation(store, "BV-XXXX") is None


# ---------------------------------------------------------------- modify
def test_modify_moves_slot_and_releases_original(store):
    res = _book(store, time="18:00")
    updated = tools.modify_reservation(store, res.confirmation_code, {"time": "18:30"})
    assert updated.time == "18:30"
    # Original 18:00 slot is free again (this booking no longer counts there).
    assert store.party_sizes_in_slot(TOMORROW, "18:00") == []


def test_modify_revalidates_new_slot_availability(store):
    # Both six-tops full at 20:00.
    _book(store, time="20:00", party_size=6, phone=phone(1))
    _book(store, time="20:00", party_size=6, phone=phone(2))
    mover = _book(store, time="18:00", party_size=6, phone=phone(3))
    with pytest.raises(UnavailableError):
        tools.modify_reservation(store, mover.confirmation_code, {"time": "20:00"})


def test_modify_party_size_in_place(store):
    res = _book(store, time="18:00", party_size=2)
    updated = tools.modify_reservation(store, res.confirmation_code, {"party_size": 6})
    assert updated.party_size == 6  # excluding self avoids a phantom self-conflict


def test_modify_unknown_code_raises(store):
    with pytest.raises(NotFoundError):
        tools.modify_reservation(store, "BV-ZZZZ", {"time": "18:30"})


def test_modify_rejects_unknown_field(store):
    res = _book(store)
    with pytest.raises(ValidationError, match="can't change"):
        tools.modify_reservation(store, res.confirmation_code, {"status": "cancelled"})


# ---------------------------------------------------------------- cancel
def test_cancel_releases_slot_and_marks_cancelled(store):
    res = _book(store, time="18:00")
    result = tools.cancel_reservation(store, res.confirmation_code)
    assert result["cancelled"] is True and result["already_cancelled"] is False
    assert store.get(res.confirmation_code).status == CANCELLED
    assert store.party_sizes_in_slot(TOMORROW, "18:00") == []   # slot released


def test_cancel_is_idempotent(store):
    res = _book(store)
    tools.cancel_reservation(store, res.confirmation_code)
    again = tools.cancel_reservation(store, res.confirmation_code)
    assert again["already_cancelled"] is True


def test_cannot_modify_cancelled(store):
    res = _book(store)
    tools.cancel_reservation(store, res.confirmation_code)
    with pytest.raises(ValidationError, match="cancelled"):
        tools.modify_reservation(store, res.confirmation_code, {"time": "20:00"})


def test_cancel_unknown_code_raises(store):
    with pytest.raises(NotFoundError):
        tools.cancel_reservation(store, "BV-NOPE")
