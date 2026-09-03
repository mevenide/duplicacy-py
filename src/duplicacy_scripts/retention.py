"""Retention policy buckets and frequency-based revision selection.

A retention policy is a list of ``{age, frequency}`` entries (see
``config retention-policy``) validated by :func:`validate_retention_policy`:
the ages must be unique, positive, and parsable durations, and the
frequencies positive, parsable, and from the supported set (see
:func:`is_supported_frequency`). The unique ages divide time relative to a
reference point (now) into chronological buckets, one more than the number of
ages: everything older than the oldest age, one bucket per gap between
consecutive ages, and everything newer than the newest age. All datetimes
are naive local times, matching the ``duplicacy list`` output. Callers pass
midnight (the start of today, or of the day of the snapshot's latest
revision — see the ``retentionAnchor`` configuration option) as ``now`` so
every boundary and tick is aligned to midnight and results do not depend on
the time of day.

Each bucket older than the newest age is thinned by its entry's frequency:
ideal timestamps are laid out on a grid anchored at the bucket's end
boundary, spaced one frequency apart and stopping at the bucket's start
(for the unbounded-past bucket, at the first tick at or below the oldest
revision), and the revision closest to each grid timestamp is kept — the
latest revision wins ties — while the rest are pruned. The end-boundary
tick needs no entry of its own when the next later bucket keeps a
revision within half a frequency of the boundary — the newest bucket
keeps everything it holds, and a thinned bucket keeps its start-boundary
revision — so that revision serves the slot and the earlier bucket's
entry nearest the boundary (e.g. 23:45 against a later 00:00 entry on an
hourly grid) is pruned. The newest bucket (revisions newer than the
smallest age) is always kept, matching the Duplicacy CLI's prune
behaviour. With midnight anchoring, day-and-larger frequencies tick
exactly at midnight.
"""

from __future__ import annotations

from bisect import bisect_left
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from duplicacy_scripts.duration import parse_duration

if TYPE_CHECKING:
    from duplicacy_scripts._cli import Revision


SECONDS_PER_HOUR = 60 * 60
HOURS_PER_DAY = 24
SUB_HOUR_FREQUENCIES = (15 * 60, 30 * 60)


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


def is_supported_frequency(frequency: float) -> bool:
    """Return whether ``frequency`` (in seconds) is a supported frequency.

    Supported frequencies keep the midnight-anchored retention grids at
    consistent times of day: ``15m`` and ``30m``, whole-hour divisors of
    24h (``1h``, ``2h``, ``3h``, ``4h``, ``6h``, ``8h``, ``12h``, ``24h``),
    and multiples of 24h (``1d``, ``2d``, ``1w``, ``1month``, ...). Finer
    granularities (``5m``), hour counts that do not divide 24 (``5h``,
    ``7h``, ...), and non-whole-hour durations (``45m``, ``1h30m``) are
    unsupported.
    """
    if frequency <= 0:
        return False
    if frequency in SUB_HOUR_FREQUENCIES:
        return True
    hours = frequency / SECONDS_PER_HOUR
    if hours != int(hours):
        return False
    hour_count = int(hours)
    return (
        hour_count <= HOURS_PER_DAY and HOURS_PER_DAY % hour_count == 0
    ) or hour_count % HOURS_PER_DAY == 0


def validate_retention_policy(retention_policy: list[dict[str, str]]) -> None:
    """Validate a retention policy's entries.

    Every age must be a unique, positive, parsable duration and every
    frequency a positive, parsable duration from the supported set (see
    :func:`is_supported_frequency`). Ages are compared as parsed
    durations, so two entries whose ages resolve to the same number of
    seconds (``7d`` and ``1w``) count as duplicates. Raises ``ValueError``
    (including ``DurationError``) describing the first problem found.
    """
    seen_ages: dict[float, str] = {}
    for entry in retention_policy:
        age_text = entry["age"]
        age = parse_duration(age_text)
        if age <= 0:
            raise ValueError("retention policy ages must be positive")
        if age in seen_ages:
            raise ValueError(
                f"retention policy ages must be unique: {age_text!r} is the same duration as {seen_ages[age]!r}"
            )
        seen_ages[age] = age_text
        frequency = parse_duration(entry["frequency"])
        if frequency <= 0:
            raise ValueError("retention policy frequencies must be positive")
        if not is_supported_frequency(frequency):
            raise ValueError(
                f"unsupported retention policy frequency: {entry['frequency']!r} "
                "(use 15m, 30m, 1h, 2h, 3h, 4h, 6h, 8h, 12h, or a multiple of 24h such as 1d, 1w)"
            )


def buckets(retention_policy: list[dict[str, str]], now: datetime) -> list[Bucket]:
    """Return the retention buckets for ``retentionPolicy`` entries at ``now``.

    Ages are duration strings measured backwards from ``now``; the policy is
    validated first (see :func:`validate_retention_policy`), so ages must be
    unique and frequencies must be from the supported set. Pass midnight (the
    start of today, or of the day of the snapshot's latest revision — see the
    ``retentionAnchor`` configuration option) as ``now`` to align every
    boundary to midnight. Buckets are chronological (oldest first): the
    earliest covers everything older than the oldest age, the latest
    everything newer than the newest age, and each remaining bucket spans
    the gap between two consecutive ages. An empty policy yields no buckets.
    Raises ``ValueError`` (including ``DurationError``) for an invalid policy
    (unparsable, non-positive, or duplicate age; unparsable, non-positive, or
    unsupported frequency).
    """
    validate_retention_policy(retention_policy)
    ages = sorted(parse_duration(entry["age"]) for entry in retention_policy)
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


def select_revisions(
    revisions: list[Revision],
    retention_policy: list[dict[str, str]],
    now: datetime,
) -> tuple[set[int], set[int]]:
    """Return the revision numbers kept and pruned under the retention policy.

    Revisions are classified into the retention buckets (see :func:`buckets`).
    Every bucket except the newest is thinned: ideal timestamps are laid out
    one frequency apart, anchored at the bucket's end boundary and stepping
    backwards down to the bucket's start (or, for the unbounded-past bucket,
    down to the first tick at or below the oldest revision, so the deepest
    history always keeps a revision), and the revision closest to each
    timestamp is kept — the latest revision wins ties. The end-boundary tick
    is skipped when the next later bucket already keeps a revision within
    half a frequency of the boundary: that revision serves the slot, so the
    earlier bucket's entry nearest the boundary (e.g. 23:45 when the later
    bucket keeps one at exactly 00:00 for an hourly frequency) is pruned.
    Buckets are therefore thinned from the newest backwards. Revisions newer
    than the smallest age — the newest bucket — are always kept, matching the
    Duplicacy CLI's prune behaviour. An empty policy keeps everything. Pass
    midnight (the start of today, or of the day of the snapshot's latest
    revision — see the ``retentionAnchor`` configuration option) as ``now``
    to anchor the buckets and grid ticks to midnight.

    The policy is validated first (see :func:`validate_retention_policy`), so
    ages must be unique and every age and frequency positive and parsable.
    Raises ``ValueError`` (including ``DurationError``) for an unparsable,
    non-positive, or duplicate age or an unparsable, non-positive, or
    unsupported frequency.
    """
    validate_retention_policy(retention_policy)
    frequencies = {
        parse_duration(entry["age"]): timedelta(seconds=parse_duration(entry["frequency"]))
        for entry in retention_policy
    }
    policy_buckets = buckets(retention_policy, now)
    if not policy_buckets:
        return {revision.revision for revision in revisions}, set()
    kept: set[int] = set()
    # Revisions the bucket after each boundary keeps, keyed by the boundary
    # timestamp: the newest bucket keeps every revision it holds, and each
    # thinned bucket records the revision kept for its tick timestamps. A
    # thinned bucket consults them at its end boundary to avoid keeping a
    # second entry for a slot the later bucket already serves.
    kept_after_boundary: dict[datetime, list[Revision]] = {}
    newest_bucket = policy_buckets[-1]
    for revision in revisions:
        if newest_bucket.contains(revision.created_at):
            kept.add(revision.revision)
            kept_after_boundary.setdefault(newest_bucket.start, []).append(revision)
    # Bucket i ends at now - ages_desc[i], so pair the chronological buckets
    # with the entries sorted by descending age; thin from the newest bucket
    # backwards so each bucket sees what the later bucket keeps at their
    # shared boundary before it is thinned itself. strict=True guarantees
    # the bucket list stays aligned with the descending-age entries.
    for bucket, age in reversed(list(zip(policy_buckets[:-1], sorted(frequencies, reverse=True), strict=True))):
        in_bucket = [revision for revision in revisions if bucket.contains(revision.created_at)]
        if not in_bucket:
            continue
        in_bucket.sort(key=lambda revision: revision.created_at)
        times = [revision.created_at for revision in in_bucket]
        floor = bucket.start if bucket.start is not None else in_bucket[0].created_at
        for timestamp in _grid_timestamps(bucket, frequencies[age], floor):
            tick_revision = _closest_revision(in_bucket, times, timestamp)
            if timestamp == bucket.end and _boundary_kept_satisfies(
                kept_after_boundary.get(timestamp), timestamp, frequencies[age]
            ):
                continue
            kept.add(tick_revision.revision)
            kept_after_boundary.setdefault(timestamp, []).append(tick_revision)
    pruned = {revision.revision for revision in revisions} - kept
    return kept, pruned


def _boundary_kept_satisfies(
    kept: list[Revision] | None,
    timestamp: datetime,
    frequency: timedelta,
) -> bool:
    """Return whether the later bucket's kept revisions serve this end-boundary tick.

    ``kept`` holds the revisions the bucket after the boundary keeps (the
    newest bucket keeps everything it holds; a thinned bucket keeps the
    revision closest to its start-boundary tick). When one of them lies
    within half a ``frequency`` of the boundary ``timestamp``, it satisfies
    the tick's frequency slot and the earlier bucket needs no near-boundary
    entry of its own.
    """
    if not kept:
        return False
    return any(abs(revision.created_at - timestamp) <= frequency / 2 for revision in kept)


def _closest_revision(
    in_bucket: list[Revision],
    times: list[datetime],
    timestamp: datetime,
) -> Revision:
    """Return the revision in ``in_bucket`` closest to ``timestamp``.

    ``in_bucket`` must be sorted by ``created_at`` with the parallel list
    ``times``. Equidistant candidates resolve to the latest revision.
    """
    index = bisect_left(times, timestamp)
    candidates = []
    if index > 0:
        candidates.append(in_bucket[index - 1])
    if index < len(in_bucket):
        candidates.append(in_bucket[index])
    return min(candidates, key=lambda revision: (abs(revision.created_at - timestamp), -revision.revision))


def _grid_timestamps(bucket: Bucket, frequency: timedelta, floor: datetime) -> list[datetime]:
    """Return the ideal timestamps for ``bucket`` spaced one ``frequency`` apart.

    The grid starts at the bucket's end boundary and steps backwards one
    frequency at a time. Bounded buckets stop at their start (inclusive),
    so every timestamp lies within the bucket; the unbounded-past bucket
    continues to the first tick at or below ``floor`` (the oldest
    revision), so the deepest history always keeps a revision.
    """
    timestamps: list[datetime] = []
    moment = bucket.end
    if bucket.start is None:
        while moment > floor:
            timestamps.append(moment)
            moment -= frequency
        timestamps.append(moment)
    else:
        while moment >= floor:
            timestamps.append(moment)
            moment -= frequency
    return timestamps
