"""Common command-line entry point for Duplicacy helpers.

Run a backup or list the revisions for a snapshot from a repository directory:

    uv run duplicacy-py backup --repository /path/to/repo
    uv run duplicacy-py prune --repository /path/to/repo --id <snapshot id>
"""

from __future__ import annotations

import argparse
import sys

from duplicacy_scripts.cli import CliError, resolve_executable, run_cli, save_config


def _add_common_arguments(
    parser: argparse.ArgumentParser,
    *,
    repository: bool = True,
    executable: bool = True,
) -> None:
    if repository:
        parser.add_argument("--repository", required=True, help="duplicacy repository directory")
    parser.add_argument(
        "--config",
        default=None,
        help="configuration directory (default: platform user config directory)",
    )
    if executable:
        parser.add_argument(
            "--duplicacy",
            default=None,
            help="path to the duplicacy executable (default: saved config, $DUPLICACY_EXECUTABLE, or 'duplicacy' on PATH)",
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run helpers that drive the Duplicacy CLI.")
    commands = parser.add_subparsers(dest="command", required=True)

    backup = commands.add_parser("backup", help="back up the repository")
    _add_common_arguments(backup)

    prune = commands.add_parser("prune", help="list revisions for a snapshot id")
    _add_common_arguments(prune)
    prune.add_argument("--id", required=True, help="snapshot id to list revisions for")

    config = commands.add_parser("config", help="configure the Duplicacy executable")
    _add_common_arguments(config, repository=False, executable=False)
    config.add_argument(
        "--duplicacy",
        required=True,
        help="path to the duplicacy executable to save",
    )

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.command == "config":
        try:
            path = save_config(args.duplicacy, args.config)
        except OSError as exc:
            print(f"Could not write configuration: {exc}", file=sys.stderr)
            return 1
        print(f"Configuration saved to {path}")
        return 0

    command_args = ["list" if args.command == "prune" else args.command]
    if args.command == "prune":
        command_args.extend(["-id", args.id])

    try:
        executable = resolve_executable(args.duplicacy, config_dir=args.config)
        result = run_cli([executable, *command_args], cwd=args.repository)
    except CliError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(result.stdout, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())