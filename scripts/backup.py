"""Example script: run a duplicacy backup in a repository directory.

Copy this as a starting point for new scripts, or run it directly:

    uv run scripts/backup.py --repository /path/to/repo

It demonstrates the shared helpers in `src/duplicacy_scripts/cli.py`:
resolving the CLI executable, passing arguments as a list (never through a
shell), and turning a non-zero exit into a clean error with exit code 1.
"""

from __future__ import annotations

import argparse
import sys

from duplicacy_scripts.cli import CliError, resolve_executable, run_cli


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run `duplicacy backup` in a repository directory.")
    parser.add_argument("--repository", required=True, help="repository directory to back up")
    parser.add_argument(
        "--duplicacy",
        default=None,
        help="path to the duplicacy executable (default: $DUPLICACY_EXECUTABLE or 'duplicacy' on PATH)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        executable = resolve_executable(args.duplicacy)
        result = run_cli([executable, "backup"], cwd=args.repository)
    except CliError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(result.stdout, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())