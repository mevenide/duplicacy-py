"""The ``prune`` command: list revisions for a snapshot id, classified into
retention policy buckets and marked kept or pruned. All bucket boundaries
and frequency grid ticks are aligned to midnight: the reference time is
midnight of the day of the snapshot's latest revision (the default), or
midnight of the current day with ``retentionAnchor: today``.

Without ``--dry-run`` (or ``--analyze``) the command prunes: it runs the
``duplicacy prune`` command(s) that delete the pruned revisions in the
repository, forwarding duplicacy's own output to the streams it writes
(stdout to stdout, stderr to stderr). ``--dry-run`` prints those commands
instead (to stderr, marked "Would run"; nothing is pruned), and
``--analyze`` prints the bucketed kept/pruned listing instead. The retention
policy and anchor are printed to stderr for the user's information before
the snapshot id is chosen, the policy sorted latest to earliest, keeping
the informational lines off the duplicacy output streams."""

from __future__ import annotations

import argparse
import shlex
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
    """Forward duplicacy's stderr diagnostics, if any.

    Upstream duplicacy logs its diagnostics (e.g. ``Storage set to ...``)
    to stdout, so this is usually empty; forwarding it keeps any
    version-specific stderr diagnostics visible.
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
    # parse_duration cannot fail here: config.retention_policy() already
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


def _prune_command_args(snapshot_id: str, pruned: set[int], max_ranges: int) -> list[list[str]]:
    """Return the duplicacy-prune argument lists that delete ``pruned``.

    All revisions merge into one command with one ``-r`` argument per
    consecutive range: the upstream CLI's ``-r`` flag is a string-slice
    flag (repeating it deletes the union, see ``getRevisions`` in
    ``duplicacy_main.go``), so a single invocation deletes everything with
    one setup cost — one listing pass, one fossil collection — and
    singletons stay ``-r <revision>``. ``max_ranges`` caps how many ``-r``
    arguments a command may carry; past it the remaining ranges spill into
    the next command (the commands run sequentially, not in parallel).
    """
    commands: list[list[str]] = []
    args: list[str] = []
    for group in _group_consecutive(sorted(pruned)):
        if len(group) == 1:
            revision_arg = str(group[0])
        else:
            revision_arg = f"{group[0]}-{group[-1]}"
        if len(args) >= 2 * max_ranges:
            commands.append(args)
            args = []
        args.extend(("-r", revision_arg))
    if args:
        commands.append(args)
    return commands


def _print_prune_commands(executable: str, snapshot_id: str, pruned: set[int], max_ranges: int) -> None:
    """Print the ``duplicacy prune`` commands that would delete ``pruned``.

    The commands go to stderr so stdout stays parse-only, and they are
    only printed, never run: ``--dry-run`` reports what the real prune
    would do, and running it is the user's decision. ``shlex.join``
    quotes the executable so paths with spaces stay copy-paste-safe on
    POSIX shells (and read cleanly on Windows).
    """
    for args in _prune_command_args(snapshot_id, pruned, max_ranges):
        command = shlex.join([executable, "prune", "-id", snapshot_id, *args])
        print(f"Would run: {command}", file=sys.stderr)


def _run_prune_commands(
    executable: str,
    snapshot_id: str,
    pruned: set[int],
    max_ranges: int,
    repo,
) -> int:
    """Run the ``duplicacy prune`` commands that delete ``pruned``.

    One command per up-to-``max_ranges``-range batch, in order (the
    commands run sequentially, not in parallel). Duplicacy's own output
    is forwarded to the streams it writes — stdout to stdout (where its
    diagnostics and progress land, e.g. ``Storage set to ...``), stderr
    to stderr; a failing command reports the error and stops the run
    (exit 1).
    """
    commands = _prune_command_args(snapshot_id, pruned, max_ranges)
    for args in commands:
        try:
            result = _cli.run_cli([executable, "prune", "-id", snapshot_id, *args], cwd=repo)
        except _cli.CliError as exc:
            print(exc, file=sys.stderr)
            return 1
        print(result.stdout, end="")
        _print_duplicacy_stderr(result.stderr)
    return 0


def _group_consecutive(sorted_revisions: list[int]) -> list[list[int]]:
    """Group a sorted list of revision numbers into runs of consecutive numbers."""
    groups: list[list[int]] = []
    for revision in sorted_revisions:
        if groups and revision == groups[-1][-1] + 1:
            groups[-1].append(revision)
        else:
            groups.append([revision])
    return groups


def _print_bucketed_revisions(
    revisions: list[_cli.Revision],
    policy_buckets: list[Bucket],
    kept: set[int],
    pruned: set[int],
) -> None:
    """Print revisions grouped under their retention bucket headers.

    Buckets are printed in chronological order; empty buckets get a header
    with no revisions under them. Each bucket's revisions print as a small
    table, ``revision | created | kept/pruned``, with the revision column
    right-aligned to one width for the whole listing so the tables line up
    across buckets; each revision is marked ``kept`` when the retention
    policy selects it and ``pruned`` otherwise.
    """
    # The header ``revision`` is the minimum width, so the column stays as
    # wide as its caption even for single-digit revision numbers (and the
    # max keeps working for a snapshot id with no revisions at all).
    revision_width = max([len("revision"), *(len(str(revision.revision)) for revision in revisions)])
    for index, bucket in enumerate(policy_buckets):
        start = bucket.start.strftime(BUCKET_TIME_FORMAT) if bucket.start else "the beginning"
        end = bucket.end.strftime(BUCKET_TIME_FORMAT) if bucket.end else "now"
        print(f"Bucket {index}: [{start}, {end})")
        rows = [revision for revision in revisions if bucket.contains(revision.created_at)]
        if not rows:
            continue
        print(f"{'revision':>{revision_width}} | {'created':<16} | kept/pruned")
        for revision in rows:
            state = "kept" if revision.revision in kept else "pruned"
            print(f"{revision.revision:>{revision_width}} | {revision.created_at:%Y-%m-%d %H:%M} | {state}")


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Declare the ``prune`` subcommand and its arguments."""
    parser = subparsers.add_parser(
        "prune",
        help="prune revisions per the retention policy",
        description="Prune the revisions of a snapshot that the retention policy classifies as pruned",
    )
    _cli.add_config_argument(parser)
    parser.add_argument(
        "--snapshot-id",
        default=None,
        help="snapshot id to prune (interactive picker when omitted)",
    )
    parser.add_argument(
        "--analyze",
        action="store_true",
        help="print the bucketed kept/pruned listing instead of pruning",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print the duplicacy prune command(s) that would run, without running them",
    )


def run(args: argparse.Namespace) -> int:
    """Prune the chosen snapshot id per the retention policy.

    Without ``--dry-run`` (or ``--analyze``) the ``duplicacy prune``
    command(s) that delete the pruned revisions run in the repository;
    ``--dry-run`` prints them instead without running, and ``--analyze``
    prints the bucketed kept/pruned listing instead. Nothing is pruned
    with either flag.
    """
    config = _cli.Config.load(args.config)
    executable, repo = _cli.prepare_repo(config)
    # config.retention_policy() validates the whole policy (unparsable,
    # non-positive, or duplicate ages; invalid frequencies),
    # config.retention_anchor() rejects unknown anchors, and
    # config.prune_max_ranges_per_command() rejects non-positive-integer
    # range limits, so an invalid configuration exits with an error
    # before the duplicacy CLI runs.
    try:
        policy = config.retention_policy()
        anchor = config.retention_anchor()
        max_ranges = config.prune_max_ranges_per_command()
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
    if args.analyze:
        policy_buckets = buckets(policy, now)
        if not policy_buckets:
            for revision in revisions:
                print(f"{revision.revision} created at {revision.created_at:%Y-%m-%d %H:%M}")
            return 0
        kept, pruned = select_revisions(revisions, policy, now)
        _print_bucketed_revisions(revisions, policy_buckets, kept, pruned)
        return 0
    _, pruned = select_revisions(revisions, policy, now)
    if not pruned:
        print("No revisions to prune", file=sys.stderr)
        return 0
    if args.dry_run:
        # Only the commands that would prune revisions are wanted: they
        # are printed to stderr (shlex-joined, copy-paste-safe) instead of
        # run, computed from the same classification --analyze displays.
        _print_prune_commands(executable, snapshot_id, pruned, max_ranges)
        return 0
    return _run_prune_commands(executable, snapshot_id, pruned, max_ranges, repo)
