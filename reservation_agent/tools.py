"""The five agent capabilities (BRD §7) — and every business rule (BR-1..BR-9).

Design contract:
  * These functions are the ONLY place reservations are created/changed.
  * They validate strictly and raise typed errors (errors.py) on any rule
    violation or malformed input — i.e. they "fail loudly". The conversation
    layer catches the error and turns ``.message`` into a guest-facing reply.
  * ``create_reservation`` / ``modify_reservation`` / ``cancel_reservation`` run
    their whole read-validate-write inside ``store.transaction()`` so there is no
    check-then-act race (BO-2: zero double-bookings, even under concurrency).

Tools take the store explicitly, so they're trivially unit-testable with a
temp-file store and a frozen clock — no web server, no LLM.
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from . import allocator, clock, codes, config
from .errors import NotFoundError, UnavailableError, ValidationError
from .models import CANCELLED, CONFIRMED, Reservation
from .store import ReservationStore, _normalize_phone

# How far around a full slot we look for alternatives (US-2: within ±90 min).
ALTERNATIVE_WINDOW_MIN = 90
MAX_ALTERNATIVES = 3
MIN_PHONE_DIGITS = 7  # "555-0123" -> 7 digits

# ----------------------------------------------------------------------- validation


def _coerce_party_size(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ValidationError("Party size needs to be a whole number.")
    if n < config.MIN_PARTY_SIZE:
        raise ValidationError(f"Party size must be at least {config.MIN_PARTY_SIZE}.")
    if n > config.MAX_PARTY_SIZE:
        raise ValidationError(
            f"For parties larger than {config.MAX_PARTY_SIZE}, please call the "
            f"restaurant at {config.RESTAURANT_PHONE} so we can arrange seating."
        )
    return n


def _parse_date(date_str: str) -> dt.date:
    if not isinstance(date_str, str):
        raise ValidationError("I need a date in YYYY-MM-DD form.")
    try:
        return dt.datetime.strptime(date_str.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValidationError(f"'{date_str}' isn't a date I can read (use YYYY-MM-DD).")


def _parse_time_slot(time_str: str) -> dt.time:
    if not isinstance(time_str, str):
        raise ValidationError("I need a time like 19:30.")
    raw = time_str.strip()
    try:
        t = dt.datetime.strptime(raw, "%H:%M").time()
    except ValueError:
        raise ValidationError(f"'{time_str}' isn't a time I can read (use HH:MM).")
    if raw not in config.SLOT_STRINGS:  # BR-2: 30-minute increments within hours
        first, last = config.SLOT_STRINGS[0], config.SLOT_STRINGS[-1]
        raise ValidationError(
            f"We seat in 30-minute slots from {first} to {last}. "
            f"{raw} isn't one of them."
        )
    return t


def _validate_slot_rules(date_str: str, time_str: str) -> tuple[dt.date, dt.time, dt.datetime]:
    """Enforce BR-1, BR-2, BR-5, BR-6. Returns the parsed date/time/datetime."""
    date_obj = _parse_date(date_str)
    time_obj = _parse_time_slot(time_str)

    if date_obj.weekday() == config.CLOSED_WEEKDAY:  # BR-1: closed Mondays
        return _closed_monday(date_obj)

    when = dt.datetime.combine(date_obj, time_obj)
    now = clock.now()
    if when < now:  # BR-5: no times in the past
        raise ValidationError("That time is in the past — I can only book upcoming slots.")
    horizon = clock.today() + dt.timedelta(days=config.MAX_ADVANCE_DAYS)
    if date_obj > horizon:  # BR-6: <= 60 days out
        raise ValidationError(
            f"We only take reservations up to {config.MAX_ADVANCE_DAYS} days in advance."
        )
    return date_obj, time_obj, when


def _closed_monday(date_obj: dt.date):
    raise ValidationError(
        f"{config.RESTAURANT_NAME} is closed on Mondays (we're open Tue–Sun, "
        f"{config.OPEN_TIME.strftime('%H:%M')}–{config.CLOSE_TIME.strftime('%H:%M')})."
    )


def _validate_name(name: str) -> str:
    name = (name or "").strip()
    if not name:
        raise ValidationError("I'll need a name for the reservation.")
    return name


def _validate_phone(phone: str) -> str:
    phone = (phone or "").strip()
    if len(_normalize_phone(phone)) < MIN_PHONE_DIGITS:
        raise ValidationError("That phone number looks incomplete — can you re-share it?")
    return phone


# ----------------------------------------------------------------------- tools


def check_availability(store: ReservationStore, date: str, time: str, party_size: Any) -> dict:
    """BRD §7 — AvailabilityResult.

    Returns a dict: available, date, time, party_size, reason, alternatives.
    Never raises for a *rule* problem; it reports it in ``reason`` so the agent
    can explain. (Malformed/garbage input still surfaces as reason text.)
    """
    result = {
        "available": False,
        "date": date,
        "time": time,
        "party_size": party_size,
        "reason": None,
        "alternatives": [],
    }
    try:
        party = _coerce_party_size(party_size)
        date_obj, time_obj, _ = _validate_slot_rules(date, time)
    except ValidationError as exc:
        result["reason"] = exc.message
        return result

    result["party_size"] = party
    existing = store.party_sizes_in_slot(date_obj.isoformat(), time_obj.strftime("%H:%M"))
    if allocator.can_add_party(existing, party):
        result["available"] = True
        return result

    # Full: offer real, bookable nearby alternatives (US-2).
    result["reason"] = (
        f"{time_obj.strftime('%H:%M')} is fully booked for a party of {party}."
    )
    result["alternatives"] = _find_alternatives(store, date_obj, time_obj, party)
    return result


def _find_alternatives(store: ReservationStore, date_obj: dt.date, time_obj: dt.time, party: int) -> list[str]:
    """Real slots within ±90 min on the same day that can seat the party."""
    now = clock.now()
    requested = dt.datetime.combine(date_obj, time_obj)
    candidates: list[tuple[int, str]] = []
    for slot in config.SLOT_TIMES:
        if slot == time_obj:
            continue
        when = dt.datetime.combine(date_obj, slot)
        diff = int((when - requested).total_seconds() // 60)
        if abs(diff) > ALTERNATIVE_WINDOW_MIN:
            continue
        if when < now:  # never offer a past slot
            continue
        existing = store.party_sizes_in_slot(date_obj.isoformat(), slot.strftime("%H:%M"))
        if allocator.can_add_party(existing, party):
            candidates.append((abs(diff), slot.strftime("%H:%M")))
    candidates.sort(key=lambda c: (c[0], c[1]))
    return [hhmm for _, hhmm in candidates[:MAX_ALTERNATIVES]]


def create_reservation(
    store: ReservationStore,
    date: str,
    time: str,
    party_size: Any,
    guest_name: str,
    phone: str,
    notes: str = "",
) -> Reservation:
    """BRD §7 — create + persist. Fails on any rule violation or full slot."""
    with store.transaction():
        party = _coerce_party_size(party_size)
        date_obj, time_obj, _ = _validate_slot_rules(date, time)
        name = _validate_name(guest_name)
        phone_ok = _validate_phone(phone)
        d_iso = date_obj.isoformat()
        t_hhmm = time_obj.strftime("%H:%M")

        # BR-8: one active reservation per guest (phone) per date.
        if d_iso in store.active_dates_for_phone(phone_ok):
            raise ValidationError(
                "It looks like you already have a reservation on that date. "
                "I can modify it instead if you'd like."
            )

        # BR-7 capacity — evaluated INSIDE the lock to close the race window.
        existing = store.party_sizes_in_slot(d_iso, t_hhmm)
        if not allocator.can_add_party(existing, party):
            raise UnavailableError(f"{t_hhmm} is fully booked for a party of {party}.")

        reservation = Reservation(
            confirmation_code=codes.generate_code(store.all_codes()),
            date=d_iso,
            time=t_hhmm,
            party_size=party,
            guest_name=name,
            phone=phone_ok,
            notes=(notes or "").strip(),
            status=CONFIRMED,
            created_at=clock.created_at_iso(),
        )
        store.upsert(reservation)
        return reservation


def get_reservation(store: ReservationStore, confirmation_code: str) -> Optional[Reservation]:
    """BRD §7 — lookup. Returns the Reservation or None."""
    return store.get(confirmation_code)


_MODIFIABLE_FIELDS = {"date", "time", "party_size", "guest_name", "phone", "notes"}


def modify_reservation(store: ReservationStore, confirmation_code: str, changes: dict) -> Reservation:
    """BRD §7 — change fields, re-validating availability for the new slot.

    Because feasibility is recomputed from scratch (excluding this reservation),
    the original slot is implicitly released and the new slot is held.
    """
    unknown = set(changes) - _MODIFIABLE_FIELDS
    if unknown:
        raise ValidationError(f"I can't change: {', '.join(sorted(unknown))}.")

    with store.transaction():
        current = store.get(confirmation_code)
        if current is None:
            raise NotFoundError(f"I couldn't find a reservation with code {confirmation_code}.")
        if current.status == CANCELLED:
            raise ValidationError("That reservation was cancelled, so there's nothing to change.")
        if not changes:
            return current

        new_date = changes.get("date", current.date)
        new_time = changes.get("time", current.time)
        new_party = changes.get("party_size", current.party_size)
        new_name = changes.get("guest_name", current.guest_name)
        new_phone = changes.get("phone", current.phone)
        new_notes = changes.get("notes", current.notes)

        party = _coerce_party_size(new_party)
        date_obj, time_obj, _ = _validate_slot_rules(new_date, new_time)
        name = _validate_name(new_name)
        phone_ok = _validate_phone(new_phone)
        d_iso = date_obj.isoformat()
        t_hhmm = time_obj.strftime("%H:%M")

        # BR-8 on the (possibly new) phone/date, ignoring this reservation itself.
        if d_iso in store.active_dates_for_phone(phone_ok, exclude_code=current.confirmation_code):
            raise ValidationError("That guest already holds another reservation on that date.")

        # BR-7 — exclude self so moving in place / shrinking a party never self-conflicts.
        existing = store.party_sizes_in_slot(d_iso, t_hhmm, exclude_code=current.confirmation_code)
        if not allocator.can_add_party(existing, party):
            raise UnavailableError(f"{t_hhmm} on {d_iso} can't seat a party of {party}.")

        updated = Reservation(
            confirmation_code=current.confirmation_code,
            date=d_iso,
            time=t_hhmm,
            party_size=party,
            guest_name=name,
            phone=phone_ok,
            notes=(new_notes or "").strip(),
            status=CONFIRMED,
            created_at=current.created_at,
        )
        store.upsert(updated)
        return updated


def cancel_reservation(store: ReservationStore, confirmation_code: str) -> dict:
    """BRD §7 — mark cancelled and release the slot. Idempotent."""
    with store.transaction():
        current = store.get(confirmation_code)
        if current is None:
            raise NotFoundError(f"I couldn't find a reservation with code {confirmation_code}.")
        if current.status == CANCELLED:
            return {
                "cancelled": True,
                "already_cancelled": True,
                "confirmation_code": current.confirmation_code,
                "reservation": current.to_dict(),
            }
        cancelled = Reservation(**{**current.to_dict(), "status": CANCELLED})
        store.upsert(cancelled)
        return {
            "cancelled": True,
            "already_cancelled": False,
            "confirmation_code": cancelled.confirmation_code,
            "reservation": cancelled.to_dict(),
        }


# ----------------------------------------------------------------------- registry

# Callable registry: name -> function. The agent's dispatcher uses this so the
# LLM and the deterministic brain invoke tools through one logged path.
TOOL_FUNCS = {
    "check_availability": check_availability,
    "create_reservation": create_reservation,
    "get_reservation": get_reservation,
    "modify_reservation": modify_reservation,
    "cancel_reservation": cancel_reservation,
}

# JSON-Schema tool definitions handed to the LLM (provider-agnostic shape).
_DATE = {"type": "string", "description": "Date as YYYY-MM-DD (resolve relative dates yourself using today's date)."}
_TIME = {"type": "string", "description": "Start time as 24h HH:MM on a 30-minute slot, 17:00–21:30."}
_PARTY = {"type": "integer", "description": "Number of guests, 1–8.", "minimum": 1, "maximum": 8}

TOOL_SPECS = [
    {
        "name": "check_availability",
        "description": "Check whether a slot can seat a party; returns alternatives if it's full.",
        "parameters": {
            "type": "object",
            "properties": {"date": _DATE, "time": _TIME, "party_size": _PARTY},
            "required": ["date", "time", "party_size"],
        },
    },
    {
        "name": "create_reservation",
        "description": "Create and persist a reservation. Only call after check_availability says the slot is available and you have a name and phone.",
        "parameters": {
            "type": "object",
            "properties": {
                "date": _DATE, "time": _TIME, "party_size": _PARTY,
                "guest_name": {"type": "string"},
                "phone": {"type": "string"},
                "notes": {"type": "string", "description": "Optional free-text (occasion, seating preference, dietary note)."},
            },
            "required": ["date", "time", "party_size", "guest_name", "phone"],
        },
    },
    {
        "name": "get_reservation",
        "description": "Look up a reservation by its confirmation code.",
        "parameters": {
            "type": "object",
            "properties": {"confirmation_code": {"type": "string"}},
            "required": ["confirmation_code"],
        },
    },
    {
        "name": "modify_reservation",
        "description": "Change fields on an existing reservation; re-validates availability.",
        "parameters": {
            "type": "object",
            "properties": {
                "confirmation_code": {"type": "string"},
                "changes": {
                    "type": "object",
                    "description": "Any of: date, time, party_size, guest_name, phone, notes.",
                    "properties": {
                        "date": _DATE, "time": _TIME, "party_size": _PARTY,
                        "guest_name": {"type": "string"},
                        "phone": {"type": "string"},
                        "notes": {"type": "string"},
                    },
                },
            },
            "required": ["confirmation_code", "changes"],
        },
    },
    {
        "name": "cancel_reservation",
        "description": "Cancel a reservation and release its slot. Confirm with the guest first.",
        "parameters": {
            "type": "object",
            "properties": {"confirmation_code": {"type": "string"}},
            "required": ["confirmation_code"],
        },
    },
]
