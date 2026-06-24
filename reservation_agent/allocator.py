"""Table-seating feasibility.

The store does NOT remember which physical table a booking holds. Instead, a
slot is considered seatable iff the *whole set* of parties in that slot can be
assigned to distinct tables simultaneously. We re-solve that little packing
problem on every availability check / booking. This has two nice properties:

  * The persisted reservation stays exactly the §9 schema (no table_id leakage).
  * It is impossible to "double-book a table": if the parties can't all be
    placed at once, the new booking is refused.

Modeling choices (documented in README):
  * Tables: 4×2, 6×4, 2×6  (config.TABLE_INVENTORY).
  * A party of 1–6 occupies the smallest single table that fits.
  * Max party size is 8 but the largest table seats 6, so parties of 7–8 are
    seated by COMBINING exactly two tables whose seats sum to >= the party
    (e.g. 6+2, 6+4, 4+4). We combine at most two tables.
  * Each reservation holds its table(s) for its single 30-minute slot only.

The search backtracks over every option, so ``can_seat`` returns True iff a
valid assignment genuinely exists (best-fit ordering is just for speed).
"""
from __future__ import annotations

from collections import Counter

from . import config

_INVENTORY_COUNTS = Counter(config.TABLE_SEATS)  # e.g. {2: 4, 4: 6, 6: 2}
_CAPACITIES = sorted(_INVENTORY_COUNTS)          # [2, 4, 6]


def _solve(parties: list[int], i: int, avail: dict[int, int]) -> bool:
    if i == len(parties):
        return True
    size = parties[i]

    if size <= config.LARGEST_TABLE:
        # One table that fits; try smallest-first, but backtrack through all.
        for cap in _CAPACITIES:
            if cap >= size and avail[cap] > 0:
                avail[cap] -= 1
                if _solve(parties, i + 1, avail):
                    avail[cap] += 1
                    return True
                avail[cap] += 1
        return False

    # size > largest single table -> combine exactly two tables.
    for a_idx, c1 in enumerate(_CAPACITIES):
        for c2 in _CAPACITIES[a_idx:]:
            if c1 + c2 < size:
                continue
            if c1 == c2:
                if avail[c1] < 2:
                    continue
                avail[c1] -= 2
                if _solve(parties, i + 1, avail):
                    avail[c1] += 2
                    return True
                avail[c1] += 2
            else:
                if avail[c1] < 1 or avail[c2] < 1:
                    continue
                avail[c1] -= 1
                avail[c2] -= 1
                if _solve(parties, i + 1, avail):
                    avail[c1] += 1
                    avail[c2] += 1
                    return True
                avail[c1] += 1
                avail[c2] += 1
    return False


def can_seat(party_sizes: list[int]) -> bool:
    """Can ALL these parties be seated at distinct tables in one slot?"""
    if not party_sizes:
        return True
    # Hardest (largest) parties first -> far less branching.
    parties = sorted(party_sizes, reverse=True)
    if any(p < 1 for p in parties):
        return False
    avail = dict(_INVENTORY_COUNTS)
    return _solve(parties, 0, avail)


def can_add_party(existing_party_sizes: list[int], new_party_size: int) -> bool:
    """Would adding ``new_party_size`` to a slot still be seatable?"""
    return can_seat(list(existing_party_sizes) + [new_party_size])
