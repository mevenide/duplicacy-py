"""Internal command modules for the common CLI.

Each module defines one ``duplicacy-py`` subcommand and exposes:

- ``add_parser(subparsers)`` — declares the subcommand and its arguments;
- ``run(args) -> int`` — executes the command.

``main.py`` imports the command modules through :data:`COMMANDS`, so adding a
command means adding a module and registering it here.
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

from . import backup, config, list, prune

COMMANDS: dict[str, tuple[Callable[[argparse._SubParsersAction], None], Callable[[argparse.Namespace], int]]] = {
    "backup": (backup.add_parser, backup.run),
    "list": (list.add_parser, list.run),
    "prune": (prune.add_parser, prune.run),
    "config": (config.add_parser, config.run),
}
