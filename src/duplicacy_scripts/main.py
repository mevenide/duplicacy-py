"""Common command-line entry point for Duplicacy helpers.

List the snapshot ids, or list the revisions for a snapshot in the
repository kept in the configuration directory:

    uv run duplicacy-py list
    uv run duplicacy-py prune [--snapshot-id <snapshot id>]
    uv run duplicacy-py config init --storage <storage url>
    uv run duplicacy-py config var duplicacy=/path/to/duplicacy

The subcommands live in ``duplicacy_scripts.commands`` (one module per
command, registered in that package's ``COMMANDS`` table); this module only
assembles the parser, dispatches, and handles errors.
"""

from __future__ import annotations

import argparse
import sys

from duplicacy_scripts import commands
from duplicacy_scripts._cli import CliError, load_env


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build the parser from the command registry and parse ``argv``."""
    parser = argparse.ArgumentParser(
        description="Run helpers that drive the Duplicacy CLI.",
        epilog=(
            "Use '<command> --help' for details on each command "
            "(list, prune, and config with its init, var, and "
            "retention-policy subcommands)."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    for add_parser, _run in commands.COMMANDS.values():
        add_parser(subparsers)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Dispatch the parsed command and turn ``CliError`` into exit code 1.

    ``load_env()`` is the one ``.env`` load of the run, so the variables it
    defines (``DUPLICACY_CONFIG_DIR``, ``DUPLICACY_EXECUTABLE``) are in the
    environment before any command — and so :meth:`Config.load` and
    :meth:`Config.executable` — reads them.
    """
    load_env()
    args = parse_args(argv)
    _, run = commands.COMMANDS[args.command]
    try:
        return run(args)
    except CliError as exc:
        print(exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
