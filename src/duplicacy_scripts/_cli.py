"""Internal helpers for driving CLIs (in particular, the Duplicacy CLI), used by
the per-command modules in ``duplicacy_scripts.commands``."""

from __future__ import annotations

import argparse
import enum
import os
import re
import shutil
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from dotenv import find_dotenv, load_dotenv
from platformdirs import user_config_dir

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
    ``.env`` file in the current directory (or its nearest parent that has
    one) is used if it exists; pass an explicit path to load a different
    file.
    """
    if env_file is None:
        # find_dotenv(usecwd=True) anchors the walk-up at the working
        # directory; load_dotenv's implicit find_dotenv() anchors at this
        # module's location instead, so a console script would skip a
        # .env next to the user's data and find an unrelated one.
        env_file = find_dotenv(usecwd=True)
    load_dotenv(env_file, override=False)


def default_config_dir() -> Path:
    """Return the configuration directory to use when none is given.

    The ``DUPLICACY_CONFIG_DIR`` environment variable wins when set; it may
    have been loaded from a ``.env`` file by :meth:`Config.load`, which
    always runs ``load_env()`` before calling this — a checkout points
    ``.env`` at a sandbox configuration directory to keep development runs
    away from the per-user configuration. Without it, the conventional
    per-user configuration directory is returned.
    """
    from_env = os.environ.get("DUPLICACY_CONFIG_DIR")
    if from_env:
        return Path(from_env).expanduser()
    return Path(user_config_dir("duplicacy-py"))


@dataclass
class Config:
    """The configuration of one command run, loaded exactly once.

    ``Config.load`` resolves the configuration directory (an explicit
    ``--config`` value, else :func:`default_config_dir`), reads and parses
    ``config.yaml`` once, and every accessor reads that parsed mapping —
    no helper re-reads the file, however many settings a command needs.
    """

    config_dir: Path
    path: Path
    data: dict

    @classmethod
    def load(cls, config_dir: str | os.PathLike[str] | None = None) -> Config:
        """Return the configuration loaded from ``config_dir`` (or the default).

        ``DUPLICACY_CONFIG_DIR`` (for the default directory) and
        ``DUPLICACY_EXECUTABLE`` (see :meth:`executable`) may come from the
        working-directory ``.env`` file, which ``main()`` loads via
        :func:`load_env` before dispatching; a library entry point that does
        not go through ``main()`` should call ``load_env()`` itself first.
        A missing file yields an empty configuration. A file that cannot be
        read, is not valid YAML, or is not a mapping raises ``CliError`` so
        ``main()`` reports it and every command exits with code 1 — the
        configuration file itself is broken, which no command can work
        around.
        """
        directory = Path(config_dir) if config_dir else default_config_dir()
        path = directory / "config.yaml"
        if not path.exists():
            return cls(directory, path, {})
        try:
            with path.open() as config_stream:
                configuration = yaml.safe_load(config_stream) or {}
        except (OSError, yaml.YAMLError) as exc:
            raise CliError([str(path)], None, str(exc)) from exc
        if not isinstance(configuration, dict):
            raise CliError([str(path)], None, "configuration must contain a YAML mapping")
        return cls(directory, path, configuration)

    def repo_dir(self) -> Path:
        """Return the duplicacy repository directory inside the configuration directory."""
        return self.config_dir / "repo"

    def executable(
        self,
        explicit: str | None = None,
        env_var: str = "DUPLICACY_EXECUTABLE",
        default: str = "duplicacy",
    ) -> str:
        """Return the executable to use.

        Precedence: explicit argument (mainly for tests), then ``env_var`` from
        the process environment (``main()`` has loaded the working-directory
        ``.env`` file into it), then the ``duplicacy`` key
        in the loaded configuration, finally the default name looked up on
        PATH. Raises ``CliError`` if nothing usable is found.
        """
        if explicit:
            return explicit
        from_env = os.environ.get(env_var)
        if from_env:
            return from_env
        from_config = self.data.get("duplicacy")
        if from_config:
            return str(from_config)
        if shutil.which(default):
            return default
        raise CliError(
            [default],
            None,
            f"{default!r} not found on PATH; set {env_var} in the environment or .env, or configure it in config.yaml",
        )

    def retention_policy(self) -> list[dict[str, str]]:
        """Return the ``retentionPolicy`` entries from the configuration.

        Each entry is a mapping with ``age`` and ``frequency`` duration strings.
        A missing file or key yields an empty list; a non-list or malformed entry
        raises ``TypeError``, and an invalid policy (unparsable or non-positive
        age or frequency, or duplicate ages) raises ``ValueError``.
        """
        policy = self.data.get("retentionPolicy", [])
        if not isinstance(policy, list) or any(
            not isinstance(entry, dict) or "age" not in entry or "frequency" not in entry
            for entry in policy
        ):
            raise TypeError(f"{self.path}: retentionPolicy must contain a list of {{age, frequency}} mappings")
        entries = [{"age": str(entry["age"]), "frequency": str(entry["frequency"])} for entry in policy]
        validate_retention_policy(entries)
        return entries

    def retention_anchor(self) -> RetentionAnchor:
        """Return the :class:`RetentionAnchor` from the configuration.

        The anchor picks the midnight the retention buckets are computed
        from (see :class:`RetentionAnchor`): a missing key yields
        ``LATEST_REVISION``; any other value raises ``ValueError`` naming
        the configuration file and the allowed values.
        """
        value = self.data.get(RETENTION_ANCHOR_KEY, RetentionAnchor.LATEST_REVISION)
        try:
            return RetentionAnchor(value)
        except (TypeError, ValueError):
            # TypeError covers unhashable YAML values (e.g. a list), which
            # the enum lookup rejects just like an unknown string.
            allowed = ", ".join(repr(anchor.value) for anchor in RetentionAnchor)
            raise ValueError(
                f"{self.path}: {RETENTION_ANCHOR_KEY} must be {allowed} (got {value!r})"
            ) from None

    def prune_max_ranges_per_command(self) -> int:
        """Return the maximum ``-r`` ranges merged into one ``duplicacy prune`` command.

        Read from the ``pruneMaxRangesPerCommand`` configuration key: a
        missing key yields the default (64, enough for any real retention
        run while keeping the command readable); a value that is not a
        positive integer (bools included — YAML ``true`` parses as a bool,
        which is not an integer here) raises ``ValueError`` naming the
        configuration file and the key.
        """
        value = self.data.get(PRUNE_MAX_RANGES_PER_COMMAND_KEY, DEFAULT_PRUNE_MAX_RANGES)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(
                f"{self.path}: {PRUNE_MAX_RANGES_PER_COMMAND_KEY} must be a positive integer (got {value!r})"
            )
        return int(value)

    def save_variable(self, variable: str, value: str) -> Path:
        """Persist a configuration variable and return the configuration path."""
        self.data[variable] = value
        return self._dump()

    def save_retention_policy(self, entries: list[dict[str, str]]) -> Path:
        """Persist ``retentionPolicy`` entries and return the configuration path.

        The entries are validated first (unparsable or non-positive age or
        frequency, or duplicate ages, raise ``ValueError``) so an invalid policy
        is never written.
        """
        validate_retention_policy(entries)
        self.data["retentionPolicy"] = entries
        return self._dump()

    def init(self) -> Path:
        """Create the configuration file if it does not exist and return its path."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.touch()
        return self.path

    def _dump(self) -> Path:
        """Write the configuration mapping back to its file and return the path."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w") as config_stream:
            yaml.safe_dump(self.data, config_stream, sort_keys=True)
        return self.path


RETENTION_ANCHOR_KEY = "retentionAnchor"


class RetentionAnchor(enum.StrEnum):
    """The midnight the retention buckets are computed from.

    ``LATEST_REVISION`` (the default) anchors at midnight of the day of
    the snapshot's latest revision and ``TODAY`` at midnight of the
    current day; the values are the ``retentionAnchor`` strings accepted
    in the configuration file.
    """

    LATEST_REVISION = "latestRevision"
    TODAY = "today"


PRUNE_MAX_RANGES_PER_COMMAND_KEY = "pruneMaxRangesPerCommand"
DEFAULT_PRUNE_MAX_RANGES = 64


def run_cli(
    args: Sequence[str],
    *,
    cwd: str | os.PathLike[str] | None = None,
    check: bool = True,
) -> CliResult:
    """Run a CLI command and capture its output.

    Args are passed as a list (never through a shell), so paths with spaces
    work the same on Windows and POSIX. With ``check=True`` (the default) a
    non-zero exit raises :class:`CliError`, as does a failure to run the
    command at all (e.g. the resolved executable does not exist).
    """
    args = [str(a) for a in args]
    try:
        completed = subprocess.run(
            args,
            cwd=cwd,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        # subprocess.run raises before any result exists when the
        # executable path is stale/broken (FileNotFoundError), not
        # executable (PermissionError), etc.; surface that as a CliError
        # instead of letting a raw traceback escape to the user.
        raise CliError(args, None, str(exc)) from exc
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
        help=(
            "configuration directory (default: $DUPLICACY_CONFIG_DIR, "
            "else the platform user config directory)"
        ),
    )


def snapshot_ids(output: str) -> list[str]:
    """Extract the unique, sorted snapshot ids from ``duplicacy list -all`` output."""
    return sorted({match.group("id") for match in SNAPSHOT_LINE.finditer(output)})


def revisions(output: str) -> list[Revision]:
    """Extract the unique, revision-sorted revisions from ``duplicacy list -id`` output.

    Timestamps are parsed as naive local times, matching the ``duplicacy``
    output; the retention logic (see ``retention.py``) is built on this
    assumption — midnight-anchored buckets shift if ``duplicacy`` ever
    emits timezone-aware or UTC timestamps instead.
    """
    found: dict[int, Revision] = {}
    for match in REVISION_LINE.finditer(output):
        revision = int(match.group("revision"))
        created_at = datetime.strptime(match.group("created"), REVISION_TIME_FORMAT)
        found.setdefault(revision, Revision(revision, created_at))
    return [found[number] for number in sorted(found)]


def prepare_repo(config: Config) -> tuple[str, Path]:
    """Return the resolved executable and repository directory.

    Raises ``CliError`` when the repository directory has not been created by
    ``config init`` yet.
    """
    executable = config.executable()
    repo = config.repo_dir()
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
    "Config",
    "DEFAULT_PRUNE_MAX_RANGES",
    "PRUNE_MAX_RANGES_PER_COMMAND_KEY",
    "RETENTION_ANCHOR_KEY",
    "RetentionAnchor",
    "REVISION_LINE",
    "REVISION_TIME_FORMAT",
    "Revision",
    "SNAPSHOT_LINE",
    "add_config_argument",
    "default_config_dir",
    "load_env",
    "prepare_repo",
    "revisions",
    "run_and_print",
    "run_cli",
    "snapshot_ids",
]
