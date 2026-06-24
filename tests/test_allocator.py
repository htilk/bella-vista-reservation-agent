"""Allocator: table-level seating feasibility (BR-7) incl. the 7–8 combine case."""
from __future__ import annotations

from reservation_agent import allocator, config


def test_inventory_is_44_seats_over_12_tables():
    assert config.TOTAL_SEATS == 44
    assert len(config.TABLE_INVENTORY) == 12
    assert sorted(config.TABLE_SEATS) == [2, 2, 2, 2, 4, 4, 4, 4, 4, 4, 6, 6]


def test_empty_slot_seats_any_single_party():
    for size in range(1, 9):
        assert allocator.can_seat([size]) is True


def test_party_of_six_needs_a_six_top():
    # Both six-tops taken by parties of 6 -> a third party of 6 cannot be seated,
    # even though 4+2 seats remain (we don't combine for parties <= 6). This
    # matches the BRD example "fully booked for parties of 6".
    assert allocator.can_seat([6, 6]) is True
    assert allocator.can_seat([6, 6, 6]) is False
    # ...but a 4 and a 2 still fit alongside the two sixes.
    assert allocator.can_seat([6, 6, 4, 2]) is True


def test_parties_of_seven_and_eight_combine_two_tables():
    assert allocator.can_seat([8]) is True   # 6+2
    assert allocator.can_seat([7]) is True   # 6+2 (>=7) or 6+4
    # Two eights at once: 6+2 and 4+4.
    assert allocator.can_seat([8, 8]) is True


def test_seat_sum_is_not_enough_table_level_refusal():
    # Exactly 44 seats requested but it does NOT fit: an 8-top is left with only
    # 2-tops, which can't combine to 8. A naive seat-counting model (44<=44)
    # would wrongly accept this.
    parties = [6, 6, 4, 4, 4, 4, 4, 4, 8]
    assert sum(parties) == config.TOTAL_SEATS
    assert allocator.can_seat(parties) is False


def test_can_add_party_helper():
    assert allocator.can_add_party([6], 6) is True       # one six-top left
    assert allocator.can_add_party([6, 6], 6) is False    # no six-top left
