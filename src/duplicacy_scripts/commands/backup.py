"""The ``backup`` command: back up the repository."""

from __future__ import annotations

import argparse

from duplicacy_scripts import _cli


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Declare the ``backup`` subcommand and its arguments."""
    parser = subparsers.add_parser("backup", help="back up the repository")
    _cli.add_config_argument(parser)


def run(args: argparse.Namespace) -> int:
    """Run ``duplicacy backup`` in the repository directory."""
    executable, repo = _cli.prepare_repo(_cli.Config.load(args.config))
    return _cli.run_and_print([executable, "backup"], repo)
