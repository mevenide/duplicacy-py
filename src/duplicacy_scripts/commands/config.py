"""The ``config`` command: manage saved configuration variables and the
retention policy."""

from __future__ import annotations

import argparse
import sys

import yaml

from duplicacy_scripts import _cli
from duplicacy_scripts.duration import DurationError, parse_duration

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

    config_retention = config_commands.add_parser(
        "retention-policy",
        help="manage retention policy entries (age and frequency duration pairs)",
    )
    retention_commands = config_retention.add_subparsers(dest="retention_command", required=True)

    retention_add = retention_commands.add_parser(
        "add",
        help="append a retention policy entry",
    )
    _cli.add_config_argument(retention_add)
    retention_add.add_argument(
        "--age",
        required=True,
        help="maximum age of revisions to keep (duration, e.g. 1h, 5m, 1d, 1w, 1month)",
    )
    retention_add.add_argument(
        "--frequency",
        required=True,
        help="minimum interval between kept revisions (duration, e.g. 1h, 5m, 1d, 1w, 1month)",
    )

    retention_list = retention_commands.add_parser(
        "list",
        help="list the retention policy entries",
    )
    _cli.add_config_argument(retention_list)

    retention_remove = retention_commands.add_parser(
        "remove",
        help="remove a retention policy entry by index",
    )
    _cli.add_config_argument(retention_remove)
    retention_remove.add_argument(
        "index",
        type=int,
        metavar="INDEX",
        help="zero-based index of the entry to remove (from 'config retention-policy list')",
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


def _run_retention_add(args: argparse.Namespace) -> int:
    """Append an age/frequency entry to the ``retentionPolicy`` configuration."""
    try:
        parse_duration(args.age)
        parse_duration(args.frequency)
    except DurationError as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        entries = _cli.load_retention_policy(args.config)
        entries.append({"age": args.age, "frequency": args.frequency})
        path = _cli.save_retention_policy(entries, args.config)
    except (OSError, TypeError, yaml.YAMLError) as exc:
        print(f"Could not write configuration: {exc}", file=sys.stderr)
        return 1
    print(f"Retention policy saved to {path} ({len(entries)} entr{'y' if len(entries) == 1 else 'ies'})")
    return 0


def _run_retention_list(args: argparse.Namespace) -> int:
    """Print the ``retentionPolicy`` entries, one indexed line per entry."""
    try:
        entries = _cli.load_retention_policy(args.config)
    except (OSError, TypeError, yaml.YAMLError) as exc:
        print(f"Could not read configuration: {exc}", file=sys.stderr)
        return 1
    if not entries:
        print("No retention policy entries configured")
        return 0
    for index, entry in enumerate(entries):
        print(f"{index}: age={entry['age']} frequency={entry['frequency']}")
    return 0


def _run_retention_remove(args: argparse.Namespace) -> int:
    """Remove the ``retentionPolicy`` entry at the given zero-based index."""
    try:
        entries = _cli.load_retention_policy(args.config)
    except (OSError, TypeError, yaml.YAMLError) as exc:
        print(f"Could not read configuration: {exc}", file=sys.stderr)
        return 1
    if not 0 <= args.index < len(entries):
        print(f"Retention policy index out of range: {args.index}", file=sys.stderr)
        return 1
    removed = entries.pop(args.index)
    try:
        path = _cli.save_retention_policy(entries, args.config)
    except (OSError, TypeError, yaml.YAMLError) as exc:
        print(f"Could not write configuration: {exc}", file=sys.stderr)
        return 1
    print(
        f"Removed entry {args.index} (age={removed['age']} frequency={removed['frequency']}); "
        f"retention policy saved to {path}"
    )
    return 0


def run(args: argparse.Namespace) -> int:
    """Dispatch to the selected ``config`` subcommand."""
    if args.config_command == "init":
        return _run_init(args)
    if args.config_command == "retention-policy":
        if args.retention_command == "add":
            return _run_retention_add(args)
        if args.retention_command == "list":
            return _run_retention_list(args)
        return _run_retention_remove(args)
    return _run_var(args)