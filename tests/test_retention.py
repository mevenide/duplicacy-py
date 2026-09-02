"""Tests for :mod:`duplicacy_scripts.retention`."""

from __future__ import annotations

from datetime import datetime

import pytest

from duplicacy_scripts.retention import Bucket, buckets


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

    def test_duplicate_ages_are_merged(self) -> None:
        boundary = datetime(2026, 8, 25, 12, 0)
        assert buckets([{"age": "7d", "frequency": "1h"}, {"age": "1w", "frequency": "5m"}], NOW) == [
            Bucket(None, boundary),
            Bucket(boundary, None),
        ]

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