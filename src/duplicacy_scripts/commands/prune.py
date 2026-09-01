"""The ``prune`` command: list revisions for a snapshot id."""

from __future__ import annotations

import argparse
import sys

import questionary

from duplicacy_scripts import _cli


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
    result = _cli.run_cli([executable, "list", "-id", snapshot_id], cwd=repo)
    for revision in _cli.revisions(result.stdout):
        print(f"{revision.revision} created at {revision.created_at:%Y-%m-%d %H:%M}")
    return 0