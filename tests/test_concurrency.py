"""State integrity under concurrency (BO-2 / §14 'what happens under a race?').

Set up a slot with exactly ONE six-top free, then fire two party-of-6 bookings
at the same instant. The transactional lock must serialise them so EXACTLY ONE
wins. Without the lock both threads would pass the availability check before
either wrote, double-booking the last six-top.
"""
from __future__ import annotations

import threading

from reservation_agent import tools
from reservation_agent.errors import UnavailableError

from tests.conftest import TOMORROW, phone


def test_no_double_booking_under_race(store):
    # Occupy one of the two six-tops at 20:00; one six-top remains.
    tools.create_reservation(
        store, date=TOMORROW, time="20:00", party_size=6,
        guest_name="Seed Six", phone=phone(1),
    )

    start = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt(idx: int):
        start.wait()  # line both threads up to maximise contention
        try:
            tools.create_reservation(
                store, date=TOMORROW, time="20:00", party_size=6,
                guest_name=f"Racer {idx}", phone=phone(10 + idx),
            )
            result = "ok"
        except UnavailableError:
            result = "rejected"
        with lock:
            outcomes.append(result)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert outcomes.count("ok") == 1, outcomes
    assert outcomes.count("rejected") == 1, outcomes
    # And the store reflects exactly two sixes in that slot (seed + one winner).
    assert sorted(store.party_sizes_in_slot(TOMORROW, "20:00")) == [6, 6]


def test_many_concurrent_bookings_never_exceed_capacity(store):
    # 20 threads each try to grab a 2-top at the same slot. Inventory caps how
    # many can succeed; the store must never seat more than is feasible.
    start = threading.Barrier(20)
    outcomes: list[str] = []
    lock = threading.Lock()

    def attempt(idx: int):
        start.wait()
        try:
            tools.create_reservation(
                store, date=TOMORROW, time="21:00", party_size=2,
                guest_name=f"Guest {idx}", phone=phone(100 + idx),
            )
            r = "ok"
        except Exception:
            r = "rejected"
        with lock:
            outcomes.append(r)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    booked = store.party_sizes_in_slot(TOMORROW, "21:00")
    # Whatever succeeded must be a feasible packing (the allocator agrees).
    from reservation_agent import allocator
    assert allocator.can_seat(booked) is True
    assert outcomes.count("ok") == len(booked)
