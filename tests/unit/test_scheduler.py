"""Unit tests for the epoch-anchor capture scheduler (fetch_service).

Guards the GH #4 / LUXUPT-59 fix: the old design took the LCM of all active
intervals as a single global anchor, which grew without bound as intervals
diversified and could push the first capture far into the future — silently
stopping all capture. The epoch-anchor helpers must instead keep every interval's
next tick BOUNDED (within one interval) and mutually phase-aligned.
"""

import pytest
from app.fetch_service import current_aligned_timestamp, next_aligned_timestamp

INTERVALS = [15, 30, 60, 120, 300, 45, 7, 11, 13, 900, 3600]
NOW = 1_800_000_000  # a fixed reference "now" (avoids Date.now nondeterminism)


class TestNextAligned:
    @pytest.mark.parametrize("interval", INTERVALS)
    def test_is_future_multiple_within_one_interval(self, interval: int) -> None:
        for now in (NOW, NOW + 1, NOW + interval - 1, NOW + interval):
            nxt = next_aligned_timestamp(now, interval)
            assert nxt % interval == 0, "must land on an epoch-aligned tick"
            assert nxt > now, "must be strictly in the future"
            assert nxt - now <= interval, "must never be more than one interval away"


class TestCurrentAligned:
    @pytest.mark.parametrize("interval", INTERVALS)
    def test_is_recent_multiple_at_or_before_now(self, interval: int) -> None:
        for now in (NOW, NOW + 1, NOW + interval - 1):
            cur = current_aligned_timestamp(now, interval)
            assert cur % interval == 0
            assert cur <= now
            assert now - cur < interval


class TestGh4Regression:
    def test_no_far_future_for_many_diverse_intervals(self) -> None:
        """The core GH #4 property: adding many (even coprime) intervals never
        pushes any first capture beyond its own interval — no LCM blowup."""
        worst = max(next_aligned_timestamp(NOW, i) - NOW for i in INTERVALS)
        assert worst <= max(INTERVALS), (
            "a first capture landed further out than the largest interval — "
            "the unbounded-LCM regression is back"
        )

    def test_intervals_coincide_at_shared_boundary(self) -> None:
        """Epoch anchoring keeps intervals synchronized: at a time divisible by
        both, their current ticks are identical (15s and 60s both fire at :00)."""
        boundary = (NOW // 60) * 60  # a multiple of 60 (and of 15, 30)
        assert current_aligned_timestamp(boundary, 15) == boundary
        assert current_aligned_timestamp(boundary, 30) == boundary
        assert current_aligned_timestamp(boundary, 60) == boundary
