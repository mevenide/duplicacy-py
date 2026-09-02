"""Retention policy buckets for classifying snapshot revisions by age.

A retention policy is a list of ``{age, frequency}`` entries (see
``config retention-policy``). The ages divide time relative to a reference
point (now) into chronological buckets, one more than the number of distinct
ages: everything older than the oldest age, one bucket per gap between
consecutive ages, and everything newer than the newest age. All datetimes
are naive local times, matching the ``duplicacy list`` output.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from duplicacy_scripts.duration import parse_duration


@dataclass
class Bucket:
    """A half-open time range used to classify revisions.

    ``start`` is inclusive and ``end`` exclusive; ``None`` means unbounded
    past (for ``start``) or unbounded future (for ``end``).
    """

    start: datetime | None
    end: datetime | None

    def contains(self, moment: datetime) -> bool:
        """Return whether ``moment`` falls within the bucket."""
        return (self.start is None or self.start <= moment) and (
            self.end is None or moment < self.end
        )


def buckets(retention_policy: list[dict[str, str]], now: datetime) -> list[Bucket]:
    """Return the retention buckets for ``retentionPolicy`` entries at ``now``.

    Ages are duration strings measured backwards from ``now``; duplicate ages
    are merged. Buckets are chronological (oldest first): the earliest covers
    everything older than the oldest age, the latest everything newer than
    the newest age, and each remaining bucket spans the gap between two
    consecutive ages. An empty policy yields no buckets. Raises ``ValueError``
    (including ``DurationError``) for an unparsable or non-positive age.
    """
    ages = sorted({parse_duration(entry["age"]) for entry in retention_policy})
    if ages and ages[0] <= 0:
        raise ValueError("retention policy ages must be positive")
    # Ages ascend from the newest to the oldest age, so their boundaries
    # (now - age) descend; reverse them into chronological order.
    boundaries = [now - timedelta(seconds=age) for age in reversed(ages)]
    bucket_list: list[Bucket] = []
    start: datetime | None = None
    for boundary in boundaries:
        bucket_list.append(Bucket(start, boundary))
        start = boundary
    if boundaries:
        bucket_list.append(Bucket(start, None))
    return bucket_list