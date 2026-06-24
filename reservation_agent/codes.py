"""Confirmation codes — BR-9: unique, human-readable, e.g. ``BV-A4F2``."""
from __future__ import annotations

import secrets
from typing import Iterable

PREFIX = "BV-"
# Crockford-ish alphabet: no 0/O or 1/I/L, so codes are easy to read aloud.
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
CODE_LEN = 4


def generate_code(existing: Iterable[str]) -> str:
    """Return a fresh BV-XXXX code not present in ``existing``."""
    taken = {c.upper() for c in existing}
    # 31**4 ≈ 924k combinations; collisions are astronomically rare, but loop anyway.
    for _ in range(10_000):
        code = PREFIX + "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
        if code not in taken:
            return code
    raise RuntimeError("Exhausted confirmation-code space")  # pragma: no cover
