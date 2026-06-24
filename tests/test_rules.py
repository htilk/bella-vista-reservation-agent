"""Business rules BR-1..BR-8 enforced by the tools layer."""
from __future__ import annotations

import datetime as dt

import pytest

from reservation_agent import clock, tools
from reservation_agent.errors import ValidationError

from tests.conftest import TOMORROW, phone


def _book(store, **kw):
    base = dict(date=TOMORROW, time="18:00", party_size=2,
                guest_name="Test Guest", phone=phone(0))
    base.update(kw)
    return tools.create_reservation(store, **base)


def test_br1_closed_on_monday(store):
    monday = (clock.today() + dt.timedelta(days=5)).isoformat()  # 2026-06-29 = Mon
    assert dt.date.fromisoformat(monday).weekday() == 0
    with pytest.raises(ValidationError, match="Monday"):
        _book(store, date=monday)


def test_br2_only_30_minute_slots(store):
    with pytest.raises(ValidationError, match="30-minute"):
        _book(store, time="18:15")


def test_br3_max_party_size_eight(store):
    with pytest.raises(ValidationError, match="call"):
        _book(store, party_size=9)


def test_br4_min_party_size_one(store):
    with pytest.raises(ValidationError, match="at least 1"):
        _book(store, party_size=0)


def test_br5_no_past_times(store):
    yesterday = (clock.today() - dt.timedelta(days=1)).isoformat()  # 2026-06-23 Tue
    with pytest.raises(ValidationError, match="past"):
        _book(store, date=yesterday, time="19:00")


def test_br5_same_day_future_is_allowed(store):
    # Clock is frozen at 12:00, so a 19:00 booking today is fine.
    today = clock.today().isoformat()
    res = _book(store, date=today, time="19:00")
    assert res.date == today


def test_br6_no_more_than_60_days_out(store):
    far = clock.today() + dt.timedelta(days=61)
    while far.weekday() == 0:  # skip a Monday so we hit the horizon rule, not BR-1
        far += dt.timedelta(days=1)
    with pytest.raises(ValidationError, match="60 days"):
        _book(store, date=far.isoformat())


def test_br8_one_reservation_per_date_per_guest(store):
    _book(store, time="18:00", phone="555-7777")
    with pytest.raises(ValidationError, match="already have a reservation"):
        _book(store, time="20:00", phone="555-7777")  # same phone, same date


def test_br8_different_phone_same_date_is_fine(store):
    _book(store, time="18:00", phone="555-7777")
    other = _book(store, time="18:00", phone="555-8888")
    assert other.confirmation_code


def test_phone_must_look_real(store):
    with pytest.raises(ValidationError, match="phone"):
        _book(store, phone="123")


def test_name_required(store):
    with pytest.raises(ValidationError, match="name"):
        _book(store, guest_name="   ")
