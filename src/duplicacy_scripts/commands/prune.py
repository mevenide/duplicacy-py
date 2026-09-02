"""The ``prune`` command: list revisions for a snapshot id, classified into
retention policy buckets and marked kept or pruned. All bucket boundaries
and frequency grid ticks are aligned to midnight: the reference time is the
start of the current day, not the moment the command runs."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

import questionary

from duplicacy_scripts import _cli
from duplicacy_scripts.retention import Bucket, buckets, select_revisions

BUCKET_TIME_FORMAT = "%Y-%m-%d %H:%M"


def select_option(message: str, choices: list[str]) -> str | None:
    """Show an interactive picker and return the selected choice (None if cancelled)."""
    return questionary.select(message, choices=choices).ask()


def _select_snapshot_id(executable: str, repo) -> str | None:
    """Let the user pick a snapshot id, or return None after printing an error."""
    try:
        result = _cli.run_cli([executable, "list", "-all"], cwd=repo)
    except _cli.CliError as exc:
        print(exc, file=sys.stderr)
        return None
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
    parser = subparsers.add_parser("prune", help="list revisions for a snapshot id")
    _cli.add_config_argument(parser)
    parser.add_argument(
        "--snapshot-id",
        default=None,
        help="snapshot id to list revisions for (interactive picker when omitted)",
    )


def run(args: argparse.Namespace) -> int:
    """List revisions for the chosen snapshot id (interactive picker when omitted)."""
    executable, repo = _cli.prepare_repo(args.config)
    snapshot_id = args.snapshot_id
    if snapshot_id is None:
        snapshot_id = _select_snapshot_id(executable, repo)
        if snapshot_id is None:
            return 1
    # Anchor at midnight (the start of today) so bucket boundaries and
    # frequency grid ticks fall exactly on calendar days regardless of
    # when the command runs.
    now = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    try:
        policy = _cli.load_retention_policy(args.config)
        policy_buckets = buckets(policy, now)
        # Validate the frequencies too, before the duplicacy CLI runs.
        select_revisions([], policy, now)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    result = _cli.run_cli([executable, "list", "-id", snapshot_id], cwd=repo)
    revisions = _cli.revisions(result.stdout)
    if not policy_buckets:
        for revision in revisions:
            print(f"{revision.revision} created at {revision.created_at:%Y-%m-%d %H:%M}")
        return 0
    kept, pruned = select_revisions(revisions, policy, now)
    _print_bucketed_revisions(revisions, policy_buckets, kept, pruned)
    return 0