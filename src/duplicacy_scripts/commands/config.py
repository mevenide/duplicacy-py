"""The ``config`` command: manage saved configuration variables."""

from __future__ import annotations

import argparse
import sys

import yaml

from duplicacy_scripts import _cli

SNAPSHOT_ID = "duplicacy-py-dummy"


def add_parser(subparsers: argparse._SubParsersAction) -> None:
    """Declare the ``config`` subcommand and its nested subcommands."""
    config = subparsers.add_parser("config", help="manage saved configuration variables")
    config_commands = config.add_subparsers(dest="config_command", required=True)

    config_init = config_commands.add_parser(
        "init",
        help="create the configuration file and initialize a duplicacy repository",
    )
    _cli.add_config_argument(config_init)
    config_init.add_argument(
        "--storage",
        required=True,
        help="storage URL to initialize the duplicacy repository with",
    )

    config_var = config_commands.add_parser("var", help="save a configuration variable")
    _cli.add_config_argument(config_var)
    config_var.add_argument(
        "assignment",
        help="configuration variable assignment (for example, duplicacy=/path/to/duplicacy)",
    )


def _run_init(args: argparse.Namespace) -> int:
    """Create the configuration file and initialize the duplicacy repository."""
    existed = _cli.config_file(args.config).exists()
    try:
        path = _cli.init_config(args.config)
    except OSError as exc:
        print(f"Could not write configuration: {exc}", file=sys.stderr)
        return 1
    if existed:
        print(f"Configuration already exists at {path}")
    else:
        print(f"Configuration initialized at {path}")
    repo = _cli.repo_dir(args.config)
    if (repo / ".duplicacy" / "preferences").exists():
        print(f"Repository already initialized at {repo}")
        return 0
    try:
        repo.mkdir(parents=True, exist_ok=True)
        executable = _cli.resolve_executable(config_dir=args.config)
        result = _cli.run_cli([executable, "init", SNAPSHOT_ID, args.storage], cwd=repo)
    except OSError as exc:
        print(f"Could not initialize the repository: {exc}", file=sys.stderr)
        return 1
    except _cli.CliError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(result.stdout, end="")
    return 0


def _run_var(args: argparse.Namespace) -> int:
    """Save a NAME=VALUE configuration variable to the configuration file."""
    try:
        variable, value = args.assignment.split("=", 1)
    except ValueError:
        print("Configuration variable must use NAME=VALUE syntax", file=sys.stderr)
        return 1
    if not variable or not value:
        print("Configuration variable must use NAME=VALUE syntax", file=sys.stderr)
        return 1
    try:
        path = _cli.save_config(variable, value, args.config)
    except (OSError, TypeError, yaml.YAMLError) as exc:
        print(f"Could not write configuration: {exc}", file=sys.stderr)
        return 1
    print(f"Configuration saved to {path}")
    return 0


def run(args: argparse.Namespace) -> int:
    """Dispatch to the selected ``config`` subcommand."""
    if args.config_command == "init":
        return _run_init(args)
    return _run_var(args)