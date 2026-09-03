"""Tests for :mod:`duplicacy_scripts.retention`."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from datetime import datetime

import pytest

from duplicacy_scripts.duration import parse_duration
from duplicacy_scripts.retention import (
    Bucket,
    _grid_timestamps,
    buckets,
    is_supported_frequency,
    select_revisions,
    validate_retention_policy,
)

NOW = datetime(2026, 9, 1, 12, 0)


class TestBuckets:
    def test_empty_policy_yields_no_buckets(self) -> None:
        assert buckets([], NOW) == []

    def test_single_age_splits_into_oldest_and_newest(self) -> None:
        boundary = datetime(2026, 8, 25, 12, 0)
        assert buckets([{"age": "7d", "frequency": "1h"}], NOW) == [
            Bucket(None, boundary),
            Bucket(boundary, None),
        ]

    def test_two_ages_yield_three_chronological_buckets(self) -> None:
        week = datetime(2026, 8, 25, 12, 0)
        day = datetime(2026, 8, 31, 12, 0)
        assert buckets([{"age": "1d", "frequency": "1h"}, {"age": "7d", "frequency": "1h"}], NOW) == [
            Bucket(None, week),
            Bucket(week, day),
            Bucket(day, None),
        ]

    def test_policy_order_does_not_matter(self) -> None:
        week = datetime(2026, 8, 25, 12, 0)
        day = datetime(2026, 8, 31, 12, 0)
        assert buckets([{"age": "7d", "frequency": "1h"}, {"age": "1d", "frequency": "1h"}], NOW) == buckets(
            [{"age": "1d", "frequency": "1h"}, {"age": "7d", "frequency": "1h"}], NOW
        )

    def test_rejects_duplicate_ages(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            buckets([{"age": "7d", "frequency": "1h"}, {"age": "1w", "frequency": "1d"}], NOW)
        assert "ages must be unique" in str(excinfo.value)

    @pytest.mark.parametrize("age", ["0s", "-1h"])
    def test_rejects_non_positive_age(self, age: str) -> None:
        with pytest.raises(ValueError):
            buckets([{"age": age, "frequency": "1h"}], NOW)

    def test_rejects_unparsable_age(self) -> None:
        with pytest.raises(ValueError):
            buckets([{"age": "7x", "frequency": "1h"}], NOW)


class TestBucketContains:
    def test_unbounded_past_start_is_none(self) -> None:
        bucket = Bucket(None, datetime(2026, 8, 25, 12, 0))
        assert bucket.contains(datetime(2020, 1, 1))
        assert not bucket.contains(datetime(2026, 8, 25, 12, 0))

    def test_start_is_inclusive_and_end_exclusive(self) -> None:
        bucket = Bucket(datetime(2026, 8, 25, 12, 0), datetime(2026, 8, 31, 12, 0))
        assert bucket.contains(datetime(2026, 8, 25, 12, 0))
        assert bucket.contains(datetime(2026, 8, 30, 23, 59))
        assert not bucket.contains(datetime(2026, 8, 31, 12, 0))

    def test_unbounded_future_end_is_none(self) -> None:
        bucket = Bucket(datetime(2026, 8, 31, 12, 0), None)
        assert bucket.contains(NOW)
        assert not bucket.contains(datetime(2026, 8, 30, 12, 0))


class TestSelectRevisions:
    def test_empty_policy_keeps_everything(self) -> None:
        revisions = [
            _revision(1, datetime(2026, 8, 20, 10, 0)),
            _revision(2, datetime(2026, 8, 30, 9, 0)),
        ]
        kept, pruned = select_revisions(revisions, [], NOW)
        assert kept == {1, 2}
        assert pruned == set()

    def test_single_entry_selects_grid_closest_revision(self) -> None:
        # age=7d, frequency=7d: the unbounded-past bucket ends at now - 7d
        # (2026-08-25 12:00) and its grid ticks at 08-25 and 08-18 12:00.
        # Revision 3 (08-24 14:00) is closest to the 08-25 tick and
        # revision 1 (08-24 10:00) to the 08-18 tick; revision 2 (08-24
        # 11:00) is never the closest and is pruned.
        revisions = [
            _revision(1, datetime(2026, 8, 24, 10, 0)),
            _revision(2, datetime(2026, 8, 24, 11, 0)),
            _revision(3, datetime(2026, 8, 24, 14, 0)),
        ]
        kept, pruned = select_revisions(revisions, [{"age": "7d", "frequency": "7d"}], NOW)
        assert kept == {1, 3}
        assert pruned == {2}

    def test_frequency_grid_selects_one_per_interval(self) -> None:
        # age=7d, frequency=1d: the grid ticks daily backwards from the
        # bucket end 2026-08-25 12:00. The 08-24 and 08-22 ticks keep
        # revisions 1 and 3; the 08-23 tick keeps revision 2 (exactly on it)
        # over revision 4 (one hour later).
        revisions = [
            _revision(1, datetime(2026, 8, 24, 11, 0)),
            _revision(2, datetime(2026, 8, 23, 12, 0)),
            _revision(3, datetime(2026, 8, 22, 13, 0)),
            _revision(4, datetime(2026, 8, 23, 11, 0)),
        ]
        kept, pruned = select_revisions(revisions, [{"age": "7d", "frequency": "1d"}], NOW)
        assert kept == {1, 2, 3}
        assert pruned == {4}

    def test_buckets_thin_independently(self) -> None:
        # The oldest (unbounded past) bucket takes the 7d entry's 1d
        # frequency and keeps its only revision, extending its grid down
        # to the first tick at or below 08-25 06:00. The 7d..1d bucket
        # takes the 1d entry's 1h frequency, so its hourly 10:00 and
        # 11:00 ticks keep revisions 2 and 4 and prune revision 3; the
        # newest bucket keeps revision 5 unconditionally.
        revisions = [
            _revision(1, datetime(2026, 8, 25, 6, 0)),
            _revision(2, datetime(2026, 8, 27, 10, 5)),
            _revision(3, datetime(2026, 8, 27, 10, 15)),
            _revision(4, datetime(2026, 8, 27, 10, 25)),
            _revision(5, datetime(2026, 9, 1, 11, 0)),
        ]
        kept, pruned = select_revisions(
            revisions,
            [{"age": "1d", "frequency": "1h"}, {"age": "7d", "frequency": "1d"}],
            NOW,
        )
        assert kept == {1, 2, 4, 5}
        assert pruned == {3}

    def test_multiple_entries_select_per_bucket(self) -> None:
        revisions = [
            _revision(1, datetime(2026, 8, 20, 12, 0)),  # oldest bucket
            _revision(2, datetime(2026, 8, 27, 10, 10)),  # 7d..1d bucket
            _revision(3, datetime(2026, 8, 27, 10, 30)),
            _revision(4, datetime(2026, 8, 27, 10, 50)),
            _revision(5, datetime(2026, 9, 1, 11, 0)),  # newest bucket: kept
        ]
        kept, pruned = select_revisions(
            revisions,
            [{"age": "1d", "frequency": "1h"}, {"age": "7d", "frequency": "1d"}],
            NOW,
        )
        # The oldest bucket's grid (frequency 1d) ticks down to 08-20 12:00,
        # keeping revision 1. In the 7d..1d bucket the grid ticks hourly, so
        # the 10:00 tick keeps revision 2 and the 11:00 tick keeps revision
        # 4, leaving revision 3 pruned.
        assert kept == {1, 2, 4, 5}
        assert pruned == {3}

    def test_each_bucket_uses_its_entry_frequency(self) -> None:
        # Both buckets hold revisions clustered 10-30 minutes apart; the
        # oldest bucket's frequency is 1d (from the 7d entry) so only the
        # cluster edges survive its 12:00 ticks, while the 7d..1d bucket's
        # frequency is 1h (from the 1d entry) so its hourly 10:00 and 11:00
        # ticks keep three of the four revisions.
        revisions = [
            _revision(1, datetime(2026, 8, 20, 9, 30)),
            _revision(2, datetime(2026, 8, 20, 9, 40)),
            _revision(3, datetime(2026, 8, 20, 10, 10)),
            _revision(4, datetime(2026, 8, 20, 10, 20)),
            _revision(5, datetime(2026, 8, 27, 9, 30)),
            _revision(6, datetime(2026, 8, 27, 9, 40)),
            _revision(7, datetime(2026, 8, 27, 10, 10)),
            _revision(8, datetime(2026, 8, 27, 10, 20)),
        ]
        kept, pruned = select_revisions(
            revisions,
            [{"age": "1d", "frequency": "1h"}, {"age": "7d", "frequency": "1d"}],
            NOW,
        )
        assert kept == {1, 4, 5, 7, 8}
        assert pruned == {2, 3, 6}

    def test_newest_bucket_is_always_kept(self) -> None:
        revisions = [
            _revision(1, datetime(2026, 8, 31, 12, 0)),
            _revision(2, datetime(2026, 9, 1, 0, 0)),
        ]
        kept, pruned = select_revisions(revisions, [{"age": "1d", "frequency": "1h"}], NOW)
        assert kept == {1, 2}
        assert pruned == set()

    def test_oldest_bucket_uses_its_entry_frequency(self) -> None:
        # In the oldest (unbounded past) bucket the grid for age=7d runs
        # backwards weekly from 2026-08-25 12:00 down to the first tick at
        # or below the oldest revision (08-18 12:00), keeping the revision
        # exactly on that tick and the one closest to the 08-25 tick.
        revisions = [
            _revision(1, datetime(2026, 8, 18, 12, 0)),
            _revision(2, datetime(2026, 8, 19, 12, 0)),
            _revision(3, datetime(2026, 8, 20, 12, 0)),
            _revision(4, datetime(2026, 8, 20, 18, 0)),
        ]
        kept, pruned = select_revisions(revisions, [{"age": "7d", "frequency": "1w"}], NOW)
        assert kept == {1, 4}
        assert pruned == {2, 3}

    def test_midnight_anchored_grid_prunes_around_midnight(self) -> None:
        # With now at midnight (2026-09-01 00:00) the age=1d boundary is
        # 08-31 00:00, so the unbounded-past bucket's daily grid ticks at
        # 08-31, 08-30 and 08-29 00:00. Revision 1 (08-30 06:00) wins the
        # 08-30 tick (6h) and the 08-29 tick (30h), revision 3 (08-30
        # 20:00) wins the 08-31 tick (4h), and revision 2 (08-30 12:00) is
        # never the closest.
        midnight = datetime(2026, 9, 1)
        revisions = [
            _revision(1, datetime(2026, 8, 30, 6, 0)),
            _revision(2, datetime(2026, 8, 30, 12, 0)),
            _revision(3, datetime(2026, 8, 30, 20, 0)),
        ]
        kept, pruned = select_revisions(revisions, [{"age": "1d", "frequency": "1d"}], midnight)
        assert kept == {1, 3}
        assert pruned == {2}

    def test_rejects_duplicate_ages(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            select_revisions(
                [],
                [{"age": "7d", "frequency": "1h"}, {"age": "1w", "frequency": "1d"}],
                NOW,
            )
        assert "ages must be unique" in str(excinfo.value)

    def test_rejects_non_positive_frequency(self) -> None:
        with pytest.raises(ValueError):
            select_revisions([], [{"age": "7d", "frequency": "0s"}], NOW)

    def test_rejects_unsupported_frequency(self) -> None:
        with pytest.raises(ValueError) as excinfo:
            select_revisions([], [{"age": "7d", "frequency": "5m"}], NOW)
        assert "unsupported retention policy frequency" in str(excinfo.value)

    def test_rejects_unparsable_frequency(self) -> None:
        with pytest.raises(ValueError):
            select_revisions([], [{"age": "7d", "frequency": "1x"}], NOW)


class TestValidateRetentionPolicy:
    def test_accepts_valid_policy(self) -> None:
        validate_retention_policy(
            [{"age": "7d", "frequency": "1h"}, {"age": "30d", "frequency": "1d"}]
        )

    def test_accepts_empty_policy(self) -> None:
        validate_retention_policy([])

    @pytest.mark.parametrize(
        ("policy", "message"),
        [
            ([{"age": "7x", "frequency": "1h"}], "invalid duration"),
            ([{"age": "0s", "frequency": "1h"}], "ages must be positive"),
            ([{"age": "-1h", "frequency": "1h"}], "ages must be positive"),
            ([{"age": "7d", "frequency": "1x"}], "invalid duration"),
            ([{"age": "7d", "frequency": "0s"}], "frequencies must be positive"),
            ([{"age": "7d", "frequency": "5m"}], "unsupported retention policy frequency"),
        ],
    )
    def test_rejects_invalid_entry(self, policy: list[dict[str, str]], message: str) -> None:
        with pytest.raises(ValueError) as excinfo:
            validate_retention_policy(policy)
        assert message in str(excinfo.value)

    def test_rejects_identical_ages(self) -> None:
        policy = [{"age": "7d", "frequency": "1h"}, {"age": "7d", "frequency": "1d"}]
        with pytest.raises(ValueError) as excinfo:
            validate_retention_policy(policy)
        assert "ages must be unique: '7d' is the same duration as '7d'" in str(excinfo.value)

    def test_rejects_equivalent_ages(self) -> None:
        # 7d and 1w parse to the same number of seconds, so they collide.
        policy = [{"age": "7d", "frequency": "1h"}, {"age": "1w", "frequency": "1d"}]
        with pytest.raises(ValueError) as excinfo:
            validate_retention_policy(policy)
        assert "ages must be unique: '1w' is the same duration as '7d'" in str(excinfo.value)

    def test_rejects_duplicate_later_in_policy(self) -> None:
        policy = [
            {"age": "7d", "frequency": "1h"},
            {"age": "30d", "frequency": "1d"},
            {"age": "1w", "frequency": "30m"},
        ]
        with pytest.raises(ValueError) as excinfo:
            validate_retention_policy(policy)
        assert "ages must be unique" in str(excinfo.value)


class TestIsSupportedFrequency:
    @pytest.mark.parametrize(
        "frequency",
        ["15m", "30m", "1h", "2h", "3h", "4h", "6h", "8h", "12h", "24h", "1d", "2d", "1w", "1month", "1y"],
    )
    def test_accepts_supported_frequencies(self, frequency: str) -> None:
        assert is_supported_frequency(parse_duration(frequency))

    @pytest.mark.parametrize(
        "frequency",
        ["5m", "10m", "45m", "1h30m", "5h", "7h", "9h", "11h", "20h", "25h", "36h", "0s", "-1h"],
    )
    def test_rejects_unsupported_frequencies(self, frequency: str) -> None:
        assert not is_supported_frequency(parse_duration(frequency))


class TestGridTimestamps:
    def test_bounded_bucket_stops_at_inclusive_start(self) -> None:
        bucket = Bucket(datetime(2026, 8, 25, 12, 0), datetime(2026, 8, 31, 12, 0))
        assert _grid_timestamps(bucket, timedelta(days=2), bucket.start) == [
            datetime(2026, 8, 31, 12, 0),
            datetime(2026, 8, 29, 12, 0),
            datetime(2026, 8, 27, 12, 0),
            datetime(2026, 8, 25, 12, 0),
        ]

    def test_unbounded_bucket_extends_to_tick_at_or_below_floor(self) -> None:
        bucket = Bucket(None, datetime(2026, 8, 25, 12, 0))
        assert _grid_timestamps(bucket, timedelta(days=7), datetime(2026, 8, 20, 10, 0)) == [
            datetime(2026, 8, 25, 12, 0),
            datetime(2026, 8, 18, 12, 0),
        ]


def _revision(number: int, created_at: datetime) -> SimpleNamespace:
    """Return a lightweight stand-in for ``_cli.Revision``."""
    return SimpleNamespace(revision=number, created_at=created_at)