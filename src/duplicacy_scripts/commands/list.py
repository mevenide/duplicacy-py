"""The ``list`` command: print the snapshot ids in the repository."""

from __future__ import annotations

import argparse

from duplicacy_scripts import _cli


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Declare the ``list`` subcommand and its arguments."""
    parser = subparsers.add_parser("list", help="list the snapshot ids in the repository")
    _cli.add_config_argument(parser)


def run(args: argparse.Namespace) -> int:
    """Run ``duplicacy list -all`` and print the unique, sorted snapshot ids."""
    executable, repo = _cli.prepare_repo(_cli.Config.load(args.config))
    result = _cli.run_cli([executable, "list", "-all"], cwd=repo)
    for snapshot_id in _cli.snapshot_ids(result.stdout):
        print(snapshot_id)
    return 0
