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
from typing import Any

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
    from_env = os.environ.get(EnvVariable.DUPLICACY_CONFIG_DIR)
    if from_env:
        return Path(from_env).expanduser()
    return Path(user_config_dir("duplicacy-py"))


class ConfigVariable(enum.StrEnum):
    """The variables recognized in ``config.yaml``.

    The values are the YAML key names and the enum is the single list of
    what is supported: the ``Config`` accessors read through it and
    ``config var`` rejects any other name, listing these. Members are
    strings, so they work as ``data`` mapping keys on reads; writes go
    through ``.value`` because ``yaml.safe_dump`` cannot represent an
    enum key.
    """

    DUPLICACY = "duplicacy"
    RETENTION_ANCHOR = "retentionAnchor"
    RETENTION_POLICY = "retentionPolicy"
    PRUNE_MAX_RANGES_PER_COMMAND = "pruneMaxRangesPerCommand"


class EnvVariable(enum.StrEnum):
    """The environment variables the scripts read.

    ``DUPLICACY_EXECUTABLE`` selects the Duplicacy executable (see
    :meth:`Config.executable`) and ``DUPLICACY_CONFIG_DIR`` the
    configuration directory (see :func:`default_config_dir`); both may
    come from a working-directory ``.env`` file loaded by
    :func:`load_env`.
    """

    DUPLICACY_EXECUTABLE = "DUPLICACY_EXECUTABLE"
    DUPLICACY_CONFIG_DIR = "DUPLICACY_CONFIG_DIR"


def settable_config_variables() -> str:
    """Return the configuration variable names ``config var`` can save.

    Every :class:`ConfigVariable` name except ``retentionPolicy``, whose
    entries are managed by ``config retention-policy`` instead; both the
    ``config var`` help text and the ``save_variable`` error message list
    these.
    """
    return ", ".join(
        member.value for member in ConfigVariable if member is not ConfigVariable.RETENTION_POLICY
    )


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
        env_var: EnvVariable = EnvVariable.DUPLICACY_EXECUTABLE,
        default: str = "duplicacy",
    ) -> str:
        """Return the executable to use.

        Precedence: explicit argument (mainly for tests), then ``env_var``
        (an :class:`EnvVariable`) from the process environment (``main()``
        has loaded the working-directory ``.env`` file into it), then the
        ``duplicacy`` key in the loaded configuration, finally the default
        name looked up on PATH. Raises ``CliError`` if nothing usable is
        found; the message shows what ``env_var`` and the ``duplicacy``
        configuration key currently hold, so a mis-set ``.env`` or
        configuration file is visible at a glance.
        """
        if explicit:
            return explicit
        from_env = os.environ.get(env_var)
        if from_env:
            return from_env
        from_config = self.data.get(ConfigVariable.DUPLICACY)
        if from_config:
            return str(from_config)
        if shutil.which(default):
            return default
        raise CliError(
            [default],
            None,
            f"{default!r} not found on PATH; set {env_var} in the environment or .env, or configure it in config.yaml"
            f" ({env_var} is currently {from_env!r},"
            f" config.yaml '{ConfigVariable.DUPLICACY}' is currently {from_config!r})",
        )

    def retention_policy(self) -> list[dict[str, str]]:
        """Return the ``retentionPolicy`` entries from the configuration.

        Each entry is a mapping with ``age`` and ``frequency`` duration strings.
        A missing file or key yields an empty list; a non-list or malformed entry
        raises ``TypeError``, and an invalid policy (unparsable or non-positive
        age or frequency, or duplicate ages) raises ``ValueError``.
        """
        policy = self.data.get(ConfigVariable.RETENTION_POLICY, [])
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
        value = self.data.get(ConfigVariable.RETENTION_ANCHOR, RetentionAnchor.LATEST_REVISION)
        try:
            return RetentionAnchor(value)
        except (TypeError, ValueError):
            # TypeError covers unhashable YAML values (e.g. a list), which
            # the enum lookup rejects just like an unknown string.
            allowed = ", ".join(repr(anchor.value) for anchor in RetentionAnchor)
            raise ValueError(
                f"{self.path}: {ConfigVariable.RETENTION_ANCHOR} must be {allowed} (got {value!r})"
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
        value = self.data.get(ConfigVariable.PRUNE_MAX_RANGES_PER_COMMAND, DEFAULT_PRUNE_MAX_RANGES)
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(
                f"{self.path}: {ConfigVariable.PRUNE_MAX_RANGES_PER_COMMAND} must be"
                f" a positive integer (got {value!r})"
            )
        return int(value)

    def variables(self) -> dict[str, Any]:
        """Return the saved configuration variables (excluding ``retentionPolicy``).

        Recognized :class:`ConfigVariable` members other than
        ``retentionPolicy`` (which is managed by :meth:`retention_policy`)
        that are present in the configuration are returned, in their
        definition order.
        """
        return {
            member.value: self.data[member.value]
            for member in ConfigVariable
            if member is not ConfigVariable.RETENTION_POLICY and member.value in self.data
        }

    def save_variable(self, variable: str, value: str) -> Path:
        """Persist a configuration variable and return the configuration path.

        ``variable`` must be a supported configuration variable name
        (:class:`ConfigVariable`); anything else raises ``ValueError``
        listing the supported names, so a typo (e.g. ``duplicaty``) is
        rejected instead of being saved and silently ignored later.
        ``retentionPolicy`` is rejected too: its entries are managed (and
        validated) by ``config retention-policy``, not by ``config var``.
        ``pruneMaxRangesPerCommand`` is stored as an integer, matching
        what :meth:`prune_max_ranges_per_command` reads back.
        """
        try:
            name = ConfigVariable(variable)
        except ValueError:
            raise ValueError(
                f"unsupported configuration variable {variable!r};"
                f" supported variables: {settable_config_variables()}"
            ) from None
        if name is ConfigVariable.RETENTION_POLICY:
            raise ValueError(
                f"{name.value} is managed by 'config retention-policy add' and 'config retention-policy remove';"
                " it cannot be saved with 'config var'"
            )
        stored = value
        if name is ConfigVariable.PRUNE_MAX_RANGES_PER_COMMAND:
            try:
                stored = int(value)
            except ValueError:
                # An unparsable value would fail every later run; report
                # the read accessor's message here so the mistake
                # surfaces at save time instead.
                stored = 0
            if stored < 1:
                raise ValueError(
                    f"{self.path}: {name.value} must be a positive integer (got {value!r})"
                ) from None
        # An enum member key would fail yaml.safe_dump (it cannot
        # represent enum members), so write the plain name.
        self.data[name.value] = stored
        return self._dump()

    def save_retention_policy(self, entries: list[dict[str, str]]) -> Path:
        """Persist ``retentionPolicy`` entries and return the configuration path.

        The entries are validated first (unparsable or non-positive age or
        frequency, or duplicate ages, raise ``ValueError``) so an invalid policy
        is never written.
        """
        validate_retention_policy(entries)
        self.data[ConfigVariable.RETENTION_POLICY.value] = entries
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


class RetentionAnchor(enum.StrEnum):
    """The midnight the retention buckets are computed from.

    ``LATEST_REVISION`` (the default) anchors at midnight of the day of
    the snapshot's latest revision and ``TODAY`` at midnight of the
    current day; the values are the ``retentionAnchor`` strings accepted
    in the configuration file.
    """

    LATEST_REVISION = "latestRevision"
    TODAY = "today"


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
    "ConfigVariable",
    "DEFAULT_PRUNE_MAX_RANGES",
    "EnvVariable",
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
    "settable_config_variables",
    "snapshot_ids",
]
