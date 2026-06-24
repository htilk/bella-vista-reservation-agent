"""The Reservation record — persisted exactly per BRD §9.

The on-disk JSON for each reservation contains these nine fields and nothing
else, so the store always 'conforms to this schema'. Table-seating feasibility
is recomputed from the set of reservations in a slot (see allocator.py); we do
not store table assignments, which keeps the schema pristine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CONFIRMED = "confirmed"
CANCELLED = "cancelled"
VALID_STATUSES = (CONFIRMED, CANCELLED)

# Canonical field order for the persisted JSON (matches §9 exactly).
SCHEMA_FIELDS = (
    "confirmation_code",
    "date",
    "time",
    "party_size",
    "guest_name",
    "phone",
    "notes",
    "status",
    "created_at",
)


@dataclass
class Reservation:
    confirmation_code: str
    date: str          # "YYYY-MM-DD"
    time: str          # "HH:MM" (24h)
    party_size: int
    guest_name: str
    phone: str
    notes: str
    status: str        # "confirmed" | "cancelled"
    created_at: str    # ISO-8601 UTC, e.g. "2026-05-26T14:32:00Z"

    def to_dict(self) -> dict[str, Any]:
        """Ordered dict matching the required schema field-for-field."""
        return {k: getattr(self, k) for k in SCHEMA_FIELDS}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Reservation":
        missing = [k for k in SCHEMA_FIELDS if k not in raw]
        if missing:
            raise ValueError(f"Reservation record missing fields: {missing}")
        if raw["status"] not in VALID_STATUSES:
            raise ValueError(f"Invalid status: {raw['status']!r}")
        return cls(**{k: raw[k] for k in SCHEMA_FIELDS})

    @property
    def is_active(self) -> bool:
        return self.status == CONFIRMED
