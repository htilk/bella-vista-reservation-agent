"""Human-friendly rendering of dates, times, and reservation summaries.

Used by both brains so the guest always sees a resolved, absolute date
(addressing the §12 'next Friday' ambiguity: we echo back what we understood).
"""
from __future__ import annotations

import datetime as dt

from .models import Reservation


def fmt_date(iso: str) -> str:
    """'2026-06-27' -> 'Saturday, June 27'. (Avoids %-d for Windows.)"""
    d = dt.date.fromisoformat(iso)
    return f"{d.strftime('%A, %B')} {d.day}"


def fmt_time(hhmm: str) -> str:
    """'19:00' -> '7:00pm'."""
    h, m = (int(x) for x in hhmm.split(":"))
    suffix = "am" if h < 12 else "pm"
    hour12 = ((h + 11) % 12) + 1
    return f"{hour12}:{m:02d}{suffix}"


def fmt_when(date_iso: str, time_hhmm: str) -> str:
    return f"{fmt_date(date_iso)} at {fmt_time(time_hhmm)}"


def fmt_reservation(res: Reservation) -> str:
    """One-line summary for confirmations and look-ups."""
    base = (
        f"{res.guest_name}, party of {res.party_size}, "
        f"{fmt_when(res.date, res.time)}"
    )
    if res.notes:
        base += f" (note: {res.notes})"
    if res.status != "confirmed":
        base += f" — {res.status}"
    return base
