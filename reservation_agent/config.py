"""Static configuration: operating hours, slot grid, party bounds, table inventory.

Every value here maps to a Business Rule (BR-*) in the BRD so the source of
truth is auditable in one place.
"""
from __future__ import annotations

import datetime as dt

RESTAURANT_NAME = "Bella Vista"
RESTAURANT_PHONE = "555-0100"  # what we point guests to for out-of-scope asks

# --- BR-1: Operating hours — Tue–Sun, 17:00–22:00. Closed Mondays. ---
# Python's date.weekday(): Monday=0 ... Sunday=6
CLOSED_WEEKDAY = 0  # Monday
OPEN_TIME = dt.time(17, 0)
CLOSE_TIME = dt.time(22, 0)

# --- BR-2: 30-minute slot increments. ---
SLOT_MINUTES = 30

# --- BR-3 / BR-4: party-size bounds (inclusive). ---
MIN_PARTY_SIZE = 1
MAX_PARTY_SIZE = 8

# --- BR-6: booking horizon — no more than 60 days in advance (inclusive). ---
MAX_ADVANCE_DAYS = 60

# --- BR-7: table inventory — 4×2, 6×4, 2×6  => 12 tables, 44 seats. ---
# A reservation occupies its table(s) for its single 30-minute slot only; we do
# not model dining duration / table turn time (documented assumption in README).
def _build_inventory() -> tuple[dict, ...]:
    inv: list[dict] = []
    n = 0
    for seats, count in ((2, 4), (4, 6), (6, 2)):
        for _ in range(count):
            n += 1
            inv.append({"id": f"T{n:02d}", "seats": seats})
    return tuple(inv)


TABLE_INVENTORY: tuple[dict, ...] = _build_inventory()
TABLE_SEATS: tuple[int, ...] = tuple(t["seats"] for t in TABLE_INVENTORY)
TOTAL_SEATS = sum(TABLE_SEATS)            # 44
LARGEST_TABLE = max(TABLE_SEATS)          # 6


def slot_times() -> list[dt.time]:
    """Valid reservation start times, e.g. 17:00, 17:30, ... 21:30.

    Last seating is one slot before close (we do not seat a party at the 22:00
    closing moment). Documented assumption.
    """
    out: list[dt.time] = []
    cur = dt.datetime.combine(dt.date.min, OPEN_TIME)
    close = dt.datetime.combine(dt.date.min, CLOSE_TIME)
    step = dt.timedelta(minutes=SLOT_MINUTES)
    while cur < close:
        out.append(cur.time())
        cur += step
    return out


SLOT_TIMES: list[dt.time] = slot_times()
SLOT_STRINGS: list[str] = [t.strftime("%H:%M") for t in SLOT_TIMES]
