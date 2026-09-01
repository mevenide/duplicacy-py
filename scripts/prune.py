"""Prune helper for duplicacy: list all revisions for a snapshot id.

This is the first step toward a fuller prune script. For now it runs
`duplicacy list -id <id>` in a repository directory and shows the result,
so you can see which revisions exist before deleting anything:

    uv run scripts/prune.py --repository /path/to/repo --id <snapshot id>
"""

from __future__ import annotations

import argparse
import sys

from duplicacy_scripts.cli import CliError, resolve_executable, run_cli


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="List all revisions for a duplicacy snapshot id.")
    parser.add_argument("--repository", required=True, help="duplicacy repository directory")
    parser.add_argument("--id", required=True, help="snapshot id to list revisions for")
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
        result = run_cli([executable, "list", "-id", args.id], cwd=args.repository)
    except CliError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(result.stdout, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())