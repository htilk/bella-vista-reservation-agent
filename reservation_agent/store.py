"""Persistent reservation store: a JSON file guarded by a reentrant lock.

Concurrency / state-integrity (BO-2, the §14 'race' question):
  * All reads and writes hold ``self._lock`` (an RLock).
  * A tool wraps its full read-validate-write sequence in ``store.transaction()``
    so availability-check and insert are ONE atomic critical section — closing
    the check-then-act (TOCTOU) window that would otherwise allow a double-book.
  * Writes go through an atomic file replace (write temp + os.replace), so a
    crash mid-write can't corrupt the store, and reservations survive a restart.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

from . import clock, config
from .models import CANCELLED, CONFIRMED, Reservation

PROJECT_ROOT = Path(__file__).resolve().parent.parent
# Override with BV_DATA_FILE to relocate the store (used by tests, or to pick a
# different on-disk location).
DEFAULT_PATH = Path(os.environ.get("BV_DATA_FILE") or (PROJECT_ROOT / "data" / "reservations.json"))


def _normalize_code(code: str) -> str:
    return (code or "").strip().upper()


class ReservationStore:
    def __init__(self, path: Optional[os.PathLike | str] = None, seed: bool = True):
        self.path = Path(path) if path else DEFAULT_PATH
        self._lock = threading.RLock()
        self._by_code: dict[str, Reservation] = {}
        self._load(seed=seed)

    # ------------------------------------------------------------------ load/save
    def _load(self, seed: bool) -> None:
        with self._lock:
            if self.path.exists():
                raw = json.loads(self.path.read_text(encoding="utf-8"))
                records = raw["reservations"] if isinstance(raw, dict) else raw
                self._by_code = {}
                for rec in records:
                    res = Reservation.from_dict(rec)
                    self._by_code[res.confirmation_code] = res
            elif seed:
                self._seed_locked()
                self._save_locked()

    def _seed_locked(self) -> None:
        """BRD §8: one pre-existing reservation for testing modify/cancel."""
        tomorrow = clock.today() + dt.timedelta(days=1)
        demo = Reservation(
            confirmation_code="BV-DEMO",
            date=tomorrow.isoformat(),
            time="19:00",
            party_size=4,
            guest_name="Alex Rivera",
            phone="555-0123",
            notes="",
            status=CONFIRMED,
            created_at=clock.created_at_iso(),
        )
        self._by_code = {demo.confirmation_code: demo}

    def _save_locked(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"reservations": [r.to_dict() for r in self._by_code.values()]}
        # Atomic write: temp file in the same dir, then replace.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
            os.replace(tmp, self.path)
        except Exception:  # pragma: no cover
            if os.path.exists(tmp):
                os.remove(tmp)
            raise

    # ------------------------------------------------------------------ tx + writes
    @contextmanager
    def transaction(self) -> Iterator["ReservationStore"]:
        """Hold the lock across a full read-validate-write sequence."""
        with self._lock:
            yield self

    def upsert(self, reservation: Reservation) -> None:
        with self._lock:
            self._by_code[reservation.confirmation_code] = reservation
            self._save_locked()

    # ------------------------------------------------------------------ reads
    def get(self, code: str) -> Optional[Reservation]:
        with self._lock:
            return self._by_code.get(_normalize_code(code))

    def all_reservations(self) -> list[Reservation]:
        with self._lock:
            return list(self._by_code.values())

    def all_codes(self) -> set[str]:
        with self._lock:
            return set(self._by_code.keys())

    def active_in_slot(self, date: str, time: str) -> list[Reservation]:
        with self._lock:
            return [
                r for r in self._by_code.values()
                if r.is_active and r.date == date and r.time == time
            ]

    def party_sizes_in_slot(self, date: str, time: str, exclude_code: Optional[str] = None) -> list[int]:
        ex = _normalize_code(exclude_code) if exclude_code else None
        return [
            r.party_size for r in self.active_in_slot(date, time)
            if r.confirmation_code != ex
        ]

    def find_active(self, name: Optional[str] = None, date: Optional[str] = None) -> list[Reservation]:
        """Look up active reservations by name and/or date (US-3/US-4 fallback)."""
        nm = name.strip().lower() if name else None
        with self._lock:
            return [
                r for r in self._by_code.values()
                if r.is_active
                and (nm is None or r.guest_name.strip().lower() == nm)
                and (date is None or r.date == date)
            ]

    def active_dates_for_phone(self, phone: str, exclude_code: Optional[str] = None) -> set[str]:
        """BR-8: a guest (identified by phone) holds at most one booking per date."""
        ex = _normalize_code(exclude_code) if exclude_code else None
        norm = _normalize_phone(phone)
        with self._lock:
            return {
                r.date for r in self._by_code.values()
                if r.is_active
                and _normalize_phone(r.phone) == norm
                and r.confirmation_code != ex
            }


def _normalize_phone(phone: str) -> str:
    """Compare phones by their digits only, so formatting doesn't matter."""
    return "".join(ch for ch in (phone or "") if ch.isdigit())
