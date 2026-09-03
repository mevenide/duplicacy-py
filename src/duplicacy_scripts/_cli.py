"""Internal helpers for driving CLIs (in particular, the Duplicacy CLI), used by
the per-command modules in ``duplicacy_scripts.commands``."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from platformdirs import user_config_dir
import yaml

from duplicacy_scripts.retention import validate_retention_policy


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


@dataclass
class Revision:
    """A snapshot revision reported by ``duplicacy list``."""

    revision: int
    created_at: datetime


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


def repo_dir(config_dir: str | os.PathLike[str] | None = None) -> Path:
    """Return the duplicacy repository directory inside ``config_dir``."""
    return (Path(config_dir) if config_dir else default_config_dir()) / "repo"


def load_config(config_dir: str | os.PathLike[str] | None = None) -> dict:
    """Return the configuration mapping from the configuration file.

    A missing file yields an empty mapping; anything other than a mapping
    raises ``TypeError``.
    """
    path = config_file(config_dir)
    if not path.exists():
        return {}
    with path.open() as config_stream:
        configuration = yaml.safe_load(config_stream) or {}
    if not isinstance(configuration, dict):
        raise TypeError("configuration must contain a YAML mapping")
    return configuration


def save_config(
    variable: str,
    value: str,
    config_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Persist a configuration variable and return the configuration path."""
    path = config_file(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    configuration = load_config(config_dir)
    configuration[variable] = value
    with path.open("w") as config_stream:
        yaml.safe_dump(configuration, config_stream, sort_keys=True)
    return path


def load_retention_policy(config_dir: str | os.PathLike[str] | None = None) -> list[dict[str, str]]:
    """Return the ``retentionPolicy`` entries from the configuration file.

    Each entry is a mapping with ``age`` and ``frequency`` duration strings.
    A missing file or key yields an empty list; a non-list or malformed entry
    raises ``TypeError``, and an invalid policy (unparsable or non-positive
    age or frequency, or duplicate ages) raises ``ValueError``.
    """
    policy = load_config(config_dir).get("retentionPolicy", [])
    if not isinstance(policy, list) or any(
        not isinstance(entry, dict) or "age" not in entry or "frequency" not in entry
        for entry in policy
    ):
        raise TypeError("retentionPolicy must contain a list of {age, frequency} mappings")
    entries = [{"age": str(entry["age"]), "frequency": str(entry["frequency"])} for entry in policy]
    validate_retention_policy(entries)
    return entries


def save_retention_policy(
    entries: list[dict[str, str]],
    config_dir: str | os.PathLike[str] | None = None,
) -> Path:
    """Persist ``retentionPolicy`` entries and return the configuration path.

    The entries are validated first (unparsable or non-positive age or
    frequency, or duplicate ages, raise ``ValueError``) so an invalid policy
    is never written.
    """
    validate_retention_policy(entries)
    path = config_file(config_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    configuration = load_config(config_dir)
    configuration["retentionPolicy"] = entries
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
    explicit: str | None = None,
    env_var: str = "DUPLICACY_EXECUTABLE",
    default: str = "duplicacy",
    config_dir: str | os.PathLike[str] | None = None,
) -> str:
    """Return the executable to use.

    Precedence: explicit argument (mainly for tests), then ``env_var`` from the
    process environment or the working-directory ``.env`` file, then the
    ``duplicacy`` key in the selected config file, finally the default name
    looked up on PATH. Raises ``CliError`` if nothing usable is found.
    """
    if explicit:
        return explicit
    load_env()
    from_env = os.environ.get(env_var)
    if from_env:
        return from_env
    path = config_file(config_dir)
    if path.exists():
        with path.open() as config_stream:
            configuration = yaml.safe_load(config_stream) or {}
        if not isinstance(configuration, dict):
            raise CliError([str(path)], None, "configuration must contain a YAML mapping")
        from_config = configuration.get("duplicacy")
        if from_config:
            return str(from_config)
    if shutil.which(default):
        return default
    raise CliError(
        [default],
        None,
        f"{default!r} not found on PATH; set {env_var} in the environment or .env, or configure it in config.yaml",
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


SNAPSHOT_LINE = re.compile(r"^Snapshot (?P<id>[^ ]+) revision \d+ ", re.MULTILINE)
REVISION_LINE = re.compile(
    r"^Snapshot [^ ]+ revision (?P<revision>\d+) created at (?P<created>\d{4}-\d{2}-\d{2} \d{2}:\d{2})",
    re.MULTILINE,
)
REVISION_TIME_FORMAT = "%Y-%m-%d %H:%M"


def add_config_argument(parser: argparse.ArgumentParser) -> None:
    """Add the common ``--config`` argument to a command's subparser."""
    parser.add_argument(
        "--config",
        default=None,
        help="configuration directory (default: platform user config directory)",
    )


def snapshot_ids(output: str) -> list[str]:
    """Extract the unique, sorted snapshot ids from ``duplicacy list -all`` output."""
    return sorted({match.group("id") for match in SNAPSHOT_LINE.finditer(output)})


def revisions(output: str) -> list[Revision]:
    """Extract the unique, revision-sorted revisions from ``duplicacy list -id`` output."""
    found: dict[int, Revision] = {}
    for match in REVISION_LINE.finditer(output):
        revision = int(match.group("revision"))
        created_at = datetime.strptime(match.group("created"), REVISION_TIME_FORMAT)
        found.setdefault(revision, Revision(revision, created_at))
    return [found[number] for number in sorted(found)]


def prepare_repo(config_dir: str | os.PathLike[str] | None) -> tuple[str, Path]:
    """Return the resolved executable and repository directory.

    Raises ``CliError`` when the repository directory has not been created by
    ``config init`` yet.
    """
    executable = resolve_executable(config_dir=config_dir)
    repo = repo_dir(config_dir)
    if not repo.is_dir():
        raise CliError(
            ["duplicacy"],
            None,
            f"Repository directory does not exist at {repo}; run 'config init' first",
        )
    return executable, repo


def run_and_print(args: Sequence[str], repo: str | os.PathLike[str]) -> int:
    """Run a CLI command in ``repo`` and print its stdout, returning 0."""
    result = run_cli([str(a) for a in args], cwd=repo)
    print(result.stdout, end="")
    return 0


__all__ = [
    "CliError",
    "CliResult",
    "REVISION_LINE",
    "REVISION_TIME_FORMAT",
    "Revision",
    "SNAPSHOT_LINE",
    "add_config_argument",
    "config_file",
    "default_config_dir",
    "init_config",
    "load_config",
    "load_env",
    "load_retention_policy",
    "prepare_repo",
    "repo_dir",
    "resolve_executable",
    "revisions",
    "run_and_print",
    "run_cli",
    "save_config",
    "save_retention_policy",
    "snapshot_ids",
]