"""A single, injectable source of 'now'.

Centralising time lets the whole BR-5 (no past) / BR-6 (<=60 days) surface be
tested deterministically: a test calls ``set_now(...)`` and every rule sees the
same frozen clock. Production simply uses the real wall clock.
"""
from __future__ import annotations

import datetime as dt
from typing import Optional

_now_override: Optional[dt.datetime] = None


def set_now(value: dt.datetime) -> None:
    """Freeze 'now' to a specific local datetime (tests only)."""
    global _now_override
    _now_override = value


def reset_now() -> None:
    """Return to the real wall clock."""
    global _now_override
    _now_override = None


def now() -> dt.datetime:
    """Current local datetime (naive), honouring any test override."""
    if _now_override is not None:
        return _now_override
    return dt.datetime.now()


def today() -> dt.date:
    return now().date()


def created_at_iso() -> str:
    """UTC timestamp for the reservation schema's ``created_at`` (…Z form)."""
    base = _now_override
    if base is not None:
        stamp = base.replace(microsecond=0)
        return stamp.isoformat() + "Z"
    stamp = dt.datetime.now(dt.timezone.utc).replace(microsecond=0)
    return stamp.isoformat().replace("+00:00", "Z")
