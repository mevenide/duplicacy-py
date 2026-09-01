"""Shared helpers for driving CLIs (in particular, the Duplicacy CLI)."""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv
from platformdirs import user_config_dir
import yaml


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


def load_env(env_file: str | os.PathLike[str] | None = None) -> None:
    """Load variables from a ``.env`` file into the environment.

    Real environment variables always win over the file. By default the
    ``.env`` file in the current directory is used if it exists; pass an
    explicit path to load a different file.
    """
    load_dotenv(env_file, override=False)


def default_config_dir() -> Path:
    """Return the conventional per-user configuration directory."""
    return Path(user_config_dir("duplicacy-py"))


def config_file(config_dir: str | os.PathLike[str] | None = None) -> Path:
    """Return the YAML configuration file for ``config_dir``."""
    return (Path(config_dir) if config_dir else default_config_dir()) / "config.yaml"


def save_config(
    variable: str,
    value: str,
    config_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Persist a configuration variable and return the configuration path."""
    path = config_file(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    configuration: dict[str, str] = {}
    if path.exists():
        with path.open() as config_stream:
            configuration = yaml.safe_load(config_stream) or {}
        if not isinstance(configuration, dict):
            raise TypeError("configuration must contain a YAML mapping")
    configuration[variable] = value
    with path.open("w") as config_stream:
        yaml.safe_dump(configuration, config_stream, sort_keys=True)
    return path


def init_config(config_dir: str | os.PathLike[str] | None = None) -> Path:
    """Create the configuration file if it does not exist and return its path."""
    path = config_file(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    return path


def resolve_executable(
    explicit: str | None,
    env_var: str = "DUPLICACY_EXECUTABLE",
    default: str = "duplicacy",
    config_dir: str | os.PathLike[str] | None = None,
) -> str:
    """Return the executable to use.

    Precedence: explicit argument, then ``env_var`` from the process
    environment, the selected config file, or the working-directory ``.env``;
    finally the default name is looked up on PATH. Raises ``CliError`` if
    nothing usable is found.
    """
    if explicit:
        return explicit
    from_process = os.environ.get(env_var)
    if from_process:
        return from_process
    path = config_file(config_dir)
    if path.exists():
        with path.open() as config_stream:
            configuration = yaml.safe_load(config_stream) or {}
        if not isinstance(configuration, dict):
            raise CliError([str(path)], None, "configuration must contain a YAML mapping")
        from_config = configuration.get("duplicacy")
        if from_config:
            return str(from_config)
    load_env()
    from_env = os.environ.get(env_var)
    if from_env:
        return from_env
    if shutil.which(default):
        return default
    raise CliError(
        [default],
        None,
        f"{default!r} not found on PATH; pass it explicitly, set {env_var}, or configure it in config.yaml or .env",
    )


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