"""Shared helpers for driving CLIs (in particular, the Duplicacy CLI)."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass


class CliError(RuntimeError):
    """Raised when a CLI command fails (non-zero exit or cannot run)."""

    def __init__(self, args: Sequence[str], returncode: int | None, output: str) -> None:
        self.args_list = list(args)
        self.returncode = returncode
        self.output = output
        printable = args[0] if args else "<command>"
        super().__init__(f"{printable} failed with exit code {returncode}:\n{output}")


@dataclass
class CliResult:
    """Result of a completed CLI invocation."""

    args: list[str]
    returncode: int
    stdout: str
    stderr: str

    @property
    def output(self) -> str:
        """Combined stdout + stderr, useful for logging."""
        return self.stdout + self.stderr


def resolve_executable(
    explicit: str | None,
    env_var: str = "DUPLICACY",
    default: str = "duplicacy",
) -> str:
    """Return the executable to use.

    Precedence: explicit argument, then the environment variable, then the
    default name looked up on PATH. Raises ``CliError`` if nothing usable is
    found.
    """
    if explicit:
        return explicit
    from_env = os.environ.get(env_var)
    if from_env:
        return from_env
    if shutil.which(default):
        return default
    raise CliError([default], None, f"{default!r} not found on PATH; pass it explicitly or set {env_var}")


def run_cli(
    args: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    check: bool = True,
) -> CliResult:
    """Run a CLI command and capture its output.

    Args are passed as a list (never through a shell), so paths with spaces
    work the same on Windows and POSIX. With ``check=True`` (the default) a
    non-zero exit raises :class:`CliError`.
    """
    args = [str(a) for a in args]
    completed = subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    result = CliResult(
        args=args,
        returncode=completed.returncode,
        stdout=completed.stdout,
        stderr=completed.stderr,
    )
    if check and completed.returncode != 0:
        raise CliError(args, completed.returncode, result.output)
    return result