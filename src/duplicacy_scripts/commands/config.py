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

    supported = _cli.settable_config_variables()
    config_var = config_commands.add_parser(
        "var",
        help="save or list configuration variables",
        description=f"Save or list configuration variables (supported variables: {supported})",
    )
    _cli.add_config_argument(config_var)
    config_var.add_argument(
        "--list",
        "-l",
        action="store_true",
        help="list current configuration variables",
    )
    config_var.add_argument(
        "assignment",
        nargs="?",
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
        help=(
            "minimum interval between kept revisions (15m, 30m, whole hours dividing 24: "
            "1h, 2h, 3h, 4h, 6h, 8h, 12h, or multiples of 24h such as 1d, 1w, 1month)"
        ),
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


def _run_init(args: argparse.Namespace, config: _cli.Config) -> int:
    """Create the configuration file and initialize the duplicacy repository."""
    existed = config.path.exists()
    try:
        path = config.init()
    except OSError as exc:
        print(f"Could not write configuration: {exc}", file=sys.stderr)
        return 1
    if existed:
        print(f"Configuration already exists at {path}")
    else:
        print(f"Configuration initialized at {path}")
    repo = config.repo_dir()
    if (repo / ".duplicacy" / "preferences").exists():
        print(f"Repository already initialized at {repo}")
        return 0
    try:
        repo.mkdir(parents=True, exist_ok=True)
        result = _cli.run_cli([config.executable(), "init", SNAPSHOT_ID, args.storage], cwd=repo)
    except OSError as exc:
        print(f"Could not initialize the repository: {exc}", file=sys.stderr)
        return 1
    except _cli.CliError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(result.stdout, end="")
    return 0


def _run_var_list(config: _cli.Config) -> int:
    """Print the saved configuration variables, one per line."""
    variables = config.variables()
    if not variables:
        print("No configuration variables configured")
        return 0
    for name, value in variables.items():
        print(f"{name}={value}")
    return 0


def _run_var(args: argparse.Namespace, config: _cli.Config) -> int:
    """Save a NAME=VALUE configuration variable or list current variables."""
    if args.list and args.assignment:
        print("Cannot combine --list with a variable assignment", file=sys.stderr)
        return 1
    if args.list or args.assignment is None:
        return _run_var_list(config)
    try:
        variable, value = args.assignment.split("=", 1)
    except ValueError:
        print("Configuration variable must use NAME=VALUE syntax", file=sys.stderr)
        return 1
    if not variable or not value:
        print("Configuration variable must use NAME=VALUE syntax", file=sys.stderr)
        return 1
    if variable != variable.strip() or not variable.strip():
        # A whitespace-only or padded name can never be resolved
        # deliberately afterwards, so reject it before it reaches the file.
        print("Configuration variable name must not be empty or contain surrounding whitespace", file=sys.stderr)
        return 1
    if "\n" in variable:
        print("Configuration variable name must not contain newlines", file=sys.stderr)
        return 1
    try:
        path = config.save_variable(variable, value)
    except ValueError as exc:
        # save_variable validates the name (against the supported
        # variables) and pruneMaxRangesPerCommand values, so a typo or a
        # bad value never reaches the configuration file.
        print(str(exc), file=sys.stderr)
        return 1
    except (OSError, yaml.YAMLError) as exc:
        print(f"Could not write configuration: {exc}", file=sys.stderr)
        return 1
    print(f"Configuration saved to {path}")
    return 0


def _run_retention_add(args: argparse.Namespace, config: _cli.Config) -> int:
    """Append an age/frequency entry to the ``retentionPolicy`` configuration."""
    try:
        parse_duration(args.age)
        parse_duration(args.frequency)
    except DurationError as exc:
        print(exc, file=sys.stderr)
        return 1
    try:
        entries = config.retention_policy()
    except (TypeError, ValueError) as exc:
        print(f"Could not read configuration: {exc}", file=sys.stderr)
        return 1
    entries.append({"age": args.age, "frequency": args.frequency})
    try:
        # Saving validates the whole policy, so an unparsable, non-positive,
        # or duplicate age never reaches the configuration file.
        path = config.save_retention_policy(entries)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print(f"Could not write configuration: {exc}", file=sys.stderr)
        return 1
    print(f"Retention policy saved to {path} ({len(entries)} entr{'y' if len(entries) == 1 else 'ies'})")
    return 0


def _run_retention_list(args: argparse.Namespace, config: _cli.Config) -> int:
    """Print the ``retentionPolicy`` entries, one indexed line per entry."""
    try:
        entries = config.retention_policy()
    except (TypeError, ValueError) as exc:
        print(f"Could not read configuration: {exc}", file=sys.stderr)
        return 1
    if not entries:
        print("No retention policy entries configured")
        return 0
    for index, entry in enumerate(entries):
        print(f"{index}: age={entry['age']} frequency={entry['frequency']}")
    return 0


def _run_retention_remove(args: argparse.Namespace, config: _cli.Config) -> int:
    """Remove the ``retentionPolicy`` entry at the given zero-based index."""
    try:
        entries = config.retention_policy()
    except (TypeError, ValueError) as exc:
        print(f"Could not read configuration: {exc}", file=sys.stderr)
        return 1
    if not 0 <= args.index < len(entries):
        print(f"Retention policy index out of range: {args.index}", file=sys.stderr)
        return 1
    removed = entries.pop(args.index)
    try:
        path = config.save_retention_policy(entries)
    except (OSError, yaml.YAMLError, ValueError) as exc:
        print(f"Could not write configuration: {exc}", file=sys.stderr)
        return 1
    print(
        f"Removed entry {args.index} (age={removed['age']} frequency={removed['frequency']}); "
        f"retention policy saved to {path}"
    )
    return 0


def run(args: argparse.Namespace) -> int:
    """Dispatch to the selected ``config`` subcommand."""
    config = _cli.Config.load(args.config)
    if args.config_command == "init":
        return _run_init(args, config)
    if args.config_command == "retention-policy":
        if args.retention_command == "add":
            return _run_retention_add(args, config)
        if args.retention_command == "list":
            return _run_retention_list(args, config)
        return _run_retention_remove(args, config)
    return _run_var(args, config)
