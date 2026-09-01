"""Common command-line entry point for Duplicacy helpers.

Run a backup, list the snapshot ids, or list the revisions for a snapshot in
the repository kept in the configuration directory:

    uv run duplicacy-py backup
    uv run duplicacy-py list
    uv run duplicacy-py prune [--snapshot-id <snapshot id>]
    uv run duplicacy-py config init --storage <storage url>
    uv run duplicacy-py config var duplicacy=/path/to/duplicacy
"""

from __future__ import annotations

import argparse
import re
import sys

import questionary
import yaml

from duplicacy_scripts.cli import CliError, config_file, init_config, repo_dir, resolve_executable, run_cli, save_config

SNAPSHOT_ID = "duplicacy-py-dummy"

SNAPSHOT_LINE = re.compile(r"^Snapshot (?P<id>[^ ]+) revision \d+ ", re.MULTILINE)


def _add_common_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--config",
        default=None,
        help="configuration directory (default: platform user config directory)",
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run helpers that drive the Duplicacy CLI.")
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser("backup", help="back up the repository")
    _add_common_arguments(backup)

    list_parser = commands.add_parser("list", help="list the snapshot ids in the repository")
    _add_common_arguments(list_parser)

    prune = commands.add_parser("prune", help="list revisions for a snapshot id")
    _add_common_arguments(prune)
    prune.add_argument("--snapshot-id", default=None, help="snapshot id to list revisions for (interactive picker when omitted)")

    config = commands.add_parser("config", help="manage saved configuration variables")
    config_commands = config.add_subparsers(dest="config_command", required=True)

    config_init = config_commands.add_parser(
        "init",
        help="create the configuration file and initialize a duplicacy repository",
    )
    _add_common_arguments(config_init)
    config_init.add_argument(
        "--storage",
        required=True,
        help="storage URL to initialize the duplicacy repository with",
    )

    config_var = config_commands.add_parser("var", help="save a configuration variable")
    _add_common_arguments(config_var)
    config_var.add_argument(
        "assignment",
        help="configuration variable assignment (for example, duplicacy=/path/to/duplicacy)",
    )

    return parser.parse_args(argv)


def snapshot_ids(output: str) -> list[str]:
    """Extract the unique, sorted snapshot ids from ``duplicacy list -all`` output."""
    return sorted({match.group("id") for match in SNAPSHOT_LINE.finditer(output)})


def select_option(message: str, choices: list[str]) -> str | None:
    """Show an interactive picker and return the selected choice (None if cancelled)."""
    return questionary.select(message, choices=choices).ask()


def _select_snapshot_id(executable: str, repo: Path) -> str | None:
    """Let the user pick a snapshot id, or return None after printing an error."""
    try:
        result = run_cli([executable, "list", "-all"], cwd=repo)
    except CliError as exc:
        print(exc, file=sys.stderr)
        return None
    ids = snapshot_ids(result.stdout)
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "config":
        if args.config_command == "init":
            existed = config_file(args.config).exists()
            try:
                path = init_config(args.config)
            except OSError as exc:
                print(f"Could not write configuration: {exc}", file=sys.stderr)
                return 1
            if existed:
                print(f"Configuration already exists at {path}")
            else:
                print(f"Configuration initialized at {path}")
            repo = repo_dir(args.config)
            if (repo / ".duplicacy" / "preferences").exists():
                print(f"Repository already initialized at {repo}")
                return 0
            try:
                repo.mkdir(parents=True, exist_ok=True)
                executable = resolve_executable(config_dir=args.config)
                result = run_cli([executable, "init", SNAPSHOT_ID, args.storage], cwd=repo)
            except OSError as exc:
                print(f"Could not initialize the repository: {exc}", file=sys.stderr)
                return 1
            except CliError as exc:
                print(exc, file=sys.stderr)
                return 1
            print(result.stdout, end="")
            return 0
        try:
            variable, value = args.assignment.split("=", 1)
        except ValueError:
            print("Configuration variable must use NAME=VALUE syntax", file=sys.stderr)
            return 1
        if not variable or not value:
            print("Configuration variable must use NAME=VALUE syntax", file=sys.stderr)
            return 1
        try:
            path = save_config(variable, value, args.config)
        except (OSError, TypeError, yaml.YAMLError) as exc:
            print(f"Could not write configuration: {exc}", file=sys.stderr)
            return 1
        print(f"Configuration saved to {path}")
        return 0

    if args.command == "list":
        command_args = ["list", "-all"]
    elif args.command == "prune":
        command_args = ["list"] if args.snapshot_id is None else ["list", "-id", args.snapshot_id]
    else:
        command_args = [args.command]

    try:
        executable = resolve_executable(config_dir=args.config)
        repo = repo_dir(args.config)
        if not repo.is_dir():
            print(f"Repository directory does not exist at {repo}; run 'config init' first", file=sys.stderr)
            return 1
        if args.command == "prune" and args.snapshot_id is None:
            snapshot_id = _select_snapshot_id(executable, repo)
            if snapshot_id is None:
                return 1
            command_args = ["list", "-id", snapshot_id]
        result = run_cli([executable, *command_args], cwd=repo)
    except CliError as exc:
        print(exc, file=sys.stderr)
        return 1
    if args.command == "list":
        ids = snapshot_ids(result.stdout)
        for snapshot_id in ids:
            print(snapshot_id)
        return 0
    print(result.stdout, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())