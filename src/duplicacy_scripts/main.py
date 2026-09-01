"""Common command-line entry point for Duplicacy helpers.

Run a backup or list the revisions for a snapshot in the repository kept in
the configuration directory:

    uv run duplicacy-py backup
    uv run duplicacy-py prune --id <snapshot id>
    uv run duplicacy-py config init --storage <storage url>
    uv run duplicacy-py config var duplicacy=/path/to/duplicacy
"""

from __future__ import annotations

import argparse
import sys

import yaml

from duplicacy_scripts.cli import CliError, config_file, init_config, repo_dir, resolve_executable, run_cli, save_config

SNAPSHOT_ID = "duplicacy-py-dummy"


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

    prune = commands.add_parser("prune", help="list revisions for a snapshot id")
    _add_common_arguments(prune)
    prune.add_argument("--id", required=True, help="snapshot id to list revisions for")

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

    command_args = ["list" if args.command == "prune" else args.command]
    if args.command == "prune":
        command_args.extend(["-id", args.id])

    try:
        executable = resolve_executable(config_dir=args.config)
        repo = repo_dir(args.config)
        if not repo.is_dir():
            print(f"Repository directory does not exist at {repo}; run 'config init' first", file=sys.stderr)
            return 1
        result = run_cli([executable, *command_args], cwd=repo)
    except CliError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(result.stdout, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())