"""The ``prune`` command: list revisions for a snapshot id, classified into
retention policy buckets and marked kept or pruned. All bucket boundaries
and frequency grid ticks are aligned to midnight: the reference time is
midnight of the day of the snapshot's latest revision (the default), or
midnight of the current day with ``retentionAnchor: today``.

The listing is the prune's dry run: it is printed only with ``--dry-run``;
without the flag the command does nothing. The retention policy and anchor
are printed to stderr for the user's information before the snapshot id is
chosen, the policy sorted latest to earliest, keeping stdout parse-only
revision output."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import questionary

from duplicacy_scripts import _cli
from duplicacy_scripts.duration import parse_duration
from duplicacy_scripts.retention import Bucket, buckets, select_revisions

BUCKET_TIME_FORMAT = "%Y-%m-%d %H:%M"


def select_option(message: str, choices: list[str]) -> str | None:
    """Show an interactive picker and return the selected choice (None if cancelled)."""
    return questionary.select(message, choices=choices).ask()


def _print_duplicacy_stderr(stderr: str) -> None:
    """Forward duplicacy's stderr diagnostics (e.g. ``Storage set to ...``).

    The duplicacy CLI logs diagnostics to stderr; forwarding them shows the
    storage context while stdout stays parse-only.
    """
    if stderr:
        print(stderr, end="", file=sys.stderr)


def _print_retention_summary(policy: list[dict[str, str]], anchor: _cli.RetentionAnchor) -> None:
    """Print the retention policy and anchor for the user's information.

    These go to stderr so stdout stays parse-only revision output, and
    before the snapshot id is chosen so the user sees what will classify
    the revisions while picking one. Entries print latest to earliest
    (ascending parsed age), each labelled with its index in the
    configuration file so it matches ``config retention-policy list``;
    the retention processing sorts the ages itself (see
    ``retention.py``), so the configuration order never affects the
    result.
    """
    print(f"Retention anchor: {anchor.value}", file=sys.stderr)
    if not policy:
        print("Retention policy: none (all revisions are kept)", file=sys.stderr)
        return
    print(f"Retention policy: {len(policy)} entr{'y' if len(policy) == 1 else 'ies'}", file=sys.stderr)
    # parse_duration cannot fail here: load_retention_policy already
    # validated every age before the summary is printed.
    latest_first = sorted(enumerate(policy), key=lambda indexed: parse_duration(indexed[1]["age"]))
    for index, entry in latest_first:
        print(f"  {index}: age={entry['age']} frequency={entry['frequency']}", file=sys.stderr)


def _select_snapshot_id(executable: str, repo) -> str | None:
    """Let the user pick a snapshot id, or return None after printing an error."""
    try:
        result = _cli.run_cli([executable, "list", "-all"], cwd=repo)
    except _cli.CliError as exc:
        print(exc, file=sys.stderr)
        return None
    _print_duplicacy_stderr(result.stderr)
    ids = _cli.snapshot_ids(result.stdout)
    if not ids:
        print("No snapshots found in the repository", file=sys.stderr)
        return None
    if not sys.stdin.isatty():
        print(
            "--snapshot-id is required when stdin is not interactive; "
            "available ids:\n  " + "\n  ".join(ids),
            file=sys.stderr,
        )
        return None
    return select_option(message="Select a snapshot id:", choices=ids)


def _print_bucketed_revisions(
    revisions: list[_cli.Revision],
    policy_buckets: list[Bucket],
    kept: set[int],
    pruned: set[int],
) -> None:
    """Print revisions grouped under their retention bucket headers.

    Buckets are printed in chronological order; empty buckets get a header
    with no revisions under them. Each revision is marked ``kept`` when the
    retention policy selects it and ``pruned`` otherwise.
    """
    for index, bucket in enumerate(policy_buckets):
        start = bucket.start.strftime(BUCKET_TIME_FORMAT) if bucket.start else "the beginning"
        end = bucket.end.strftime(BUCKET_TIME_FORMAT) if bucket.end else "now"
        print(f"Bucket {index}: [{start}, {end})")
        for revision in revisions:
            if bucket.contains(revision.created_at):
                state = "kept" if revision.revision in kept else "pruned"
                print(f"{revision.revision} created at {revision.created_at:%Y-%m-%d %H:%M} {state}")


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Declare the ``prune`` subcommand and its arguments."""
    parser = subparsers.add_parser("prune", help="print what the prune command would do if it ran")
    _cli.add_config_argument(parser)
    parser.add_argument(
        "--snapshot-id",
        default=None,
        help="snapshot id to list revisions for (interactive picker when omitted)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the prune preview (without it, prune does nothing)",
    )


def run(args: argparse.Namespace) -> int:
    """Print what the prune would do for the chosen snapshot id; do nothing without ``--dry-run``."""
    if not args.dry_run:
        # The listing is what the prune would do if it ran; without the
        # flag the command does nothing.
        return 0
    executable, repo = _cli.prepare_repo(args.config)
    # load_retention_policy validates the whole policy (unparsable,
    # non-positive, or duplicate ages; invalid frequencies), and
    # load_retention_anchor rejects unknown anchors, so an invalid
    # configuration exits with an error before the duplicacy CLI runs.
    try:
        policy = _cli.load_retention_policy(args.config)
        anchor = _cli.load_retention_anchor(args.config)
    except (TypeError, ValueError) as exc:
        print(exc, file=sys.stderr)
        return 1
    _print_retention_summary(policy, anchor)
    snapshot_id = args.snapshot_id
    if snapshot_id is None:
        snapshot_id = _select_snapshot_id(executable, repo)
        if snapshot_id is None:
            return 1
    else:
        # A CLI-supplied id is echoed for the user's information instead
        # of asking; stderr keeps stdout parse-only.
        print(f"Snapshot id: {snapshot_id}", file=sys.stderr)
    result = _cli.run_cli([executable, "list", "-id", snapshot_id], cwd=repo)
    _print_duplicacy_stderr(result.stderr)
    revisions = _cli.revisions(result.stdout)
    # Anchor at midnight so bucket boundaries and frequency grid ticks
    # fall exactly on calendar days: by default midnight of the day of
    # the latest revision (revisions come sorted by revision number, and
    # duplicacy assigns them monotonically), midnight of the current day
    # with the ``today`` anchor. With no revisions there is no latest
    # revision to anchor on, so today's midnight is used.
    if anchor is _cli.RetentionAnchor.TODAY or not revisions:
        now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        now = revisions[-1].created_at.replace(hour=0, minute=0, second=0, microsecond=0)
    policy_buckets = buckets(policy, now)
    if not policy_buckets:
        for revision in revisions:
            print(f"{revision.revision} created at {revision.created_at:%Y-%m-%d %H:%M}")
        return 0
    kept, pruned = select_revisions(revisions, policy, now)
    _print_bucketed_revisions(revisions, policy_buckets, kept, pruned)
    return 0
