"""Tests for the `duplicacy-py` entry point and its command modules.

The command modules call the shared helpers through the internal
`duplicacy_scripts._cli` module (loading their configuration via `_cli.Config`),
so tests stub them there; the interactive
prune picker is stubbed on `duplicacy_scripts.commands.prune`.
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from duplicacy_scripts import _cli
from duplicacy_scripts import main as duplicacy
from duplicacy_scripts.commands import prune as prune_command

FAKE_DUPLICACY = "/fake/duplicacy"


class FixedDateTime(datetime):
    """A ``datetime`` whose ``now()`` is fixed, for deterministic bucket tests.

    ``prune`` anchors bucket boundaries at midnight (today's midnight with
    the ``retentionAnchor: today`` config, or the latest revision's
    midnight by default), so the fixed 12:00 ``now()`` verifies that the
    command truncates it.
    """

    @classmethod
    def now(cls, tz=None) -> datetime:  # noqa: ARG003
        return datetime(2026, 9, 1, 12, 0)


@pytest.fixture()
def fake_executable(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(_cli.Config, "executable", lambda self, *a, **k: FAKE_DUPLICACY)
    return FAKE_DUPLICACY


class TestParseArgs:
    def test_requires_command(self) -> None:
        with pytest.raises(SystemExit):
            duplicacy.parse_args([])

    def test_parses_backup(self) -> None:
        args = duplicacy.parse_args(["backup", "--config", "/tmp/settings"])
        assert args.command == "backup"
        assert args.config == "/tmp/settings"

    def test_parses_list(self) -> None:
        args = duplicacy.parse_args(["list"])
        assert args.command == "list"
        assert args.config is None

    def test_parses_prune(self) -> None:
        args = duplicacy.parse_args(["prune", "--snapshot-id", "vm"])
        assert args.command == "prune"
        assert args.snapshot_id == "vm"
        assert args.dry_run is False
        assert args.analyze is False

    def test_parses_prune_analyze_prints_the_listing(self) -> None:
        # --analyze prints the bucketed kept/pruned listing (a separate
        # flag from --dry-run, which prints the prune commands).
        args = duplicacy.parse_args(["prune", "--analyze", "--snapshot-id", "vm"])
        assert args.command == "prune"
        assert args.analyze is True
        assert args.dry_run is False

    def test_parses_prune_dry_run_prints_the_commands(self) -> None:
        # --dry-run prints (never runs) the duplicacy prune commands that
        # would prune the pruned revisions.
        args = duplicacy.parse_args(["prune", "--dry-run", "--snapshot-id", "vm"])
        assert args.command == "prune"
        assert args.dry_run is True

    def test_parses_config_var(self) -> None:
        args = duplicacy.parse_args(["config", "var", "--config", "/tmp/settings", "duplicacy=/opt/duplicacy"])
        assert args.command == "config"
        assert args.config_command == "var"
        assert args.config == "/tmp/settings"
        assert args.assignment == "duplicacy=/opt/duplicacy"

    def test_does_not_parse_duplicacy_argument(self) -> None:
        with pytest.raises(SystemExit):
            duplicacy.parse_args(["backup", "--duplicacy", "/opt/duplicacy"])

    def test_parses_config_init(self) -> None:
        args = duplicacy.parse_args(
            ["config", "init", "--config", "/tmp/settings", "--storage", "/tmp/storage"]
        )
        assert args.command == "config"
        assert args.config_command == "init"
        assert args.config == "/tmp/settings"
        assert args.storage == "/tmp/storage"

    def test_config_init_requires_storage(self) -> None:
        with pytest.raises(SystemExit):
            duplicacy.parse_args(["config", "init"])

    def test_config_requires_subcommand(self) -> None:
        with pytest.raises(SystemExit):
            duplicacy.parse_args(["config"])


class TestRevisions:
    def test_parses_revisions_from_list_output(self) -> None:
        output = (
            "Storage set to /mnt/h/duplicacy-savegames.storage/\n"
            "Snapshot Jenna3_user_savegames revision 1 created at 2026-08-26 19:07 -hash\n"
            "Snapshot Jenna3_user_savegames revision 2 created at 2026-08-26 19:15\n"
            "Snapshot Jenna3_user_savegames revision 10 created at 2026-08-26 21:30\n"
            "Snapshot Jenna3_user_savegames revision 11 created at 2026-08-26 21:45\n"
            "Snapshot Jenna3_user_savegames revision 12 created at 2026-08-26 22:00\n"
        )
        assert _cli.revisions(output) == [
            _cli.Revision(1, datetime(2026, 8, 26, 19, 7)),
            _cli.Revision(2, datetime(2026, 8, 26, 19, 15)),
            _cli.Revision(10, datetime(2026, 8, 26, 21, 30)),
            _cli.Revision(11, datetime(2026, 8, 26, 21, 45)),
            _cli.Revision(12, datetime(2026, 8, 26, 22, 0)),
        ]

    def test_ignores_duplicate_revisions(self) -> None:
        output = (
            "Snapshot vm revision 5 created at 2026-01-01 10:00\n"
            "Snapshot vm revision 5 created at 2026-01-01 10:00\n"
        )
        assert _cli.revisions(output) == [_cli.Revision(5, datetime(2026, 1, 1, 10, 0))]

    def test_returns_empty_list_without_snapshot_lines(self) -> None:
        assert _cli.revisions("Storage set to /tmp/storage\n") == []


class TestMain:
    def test_loads_dotenv_file_before_dispatching(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # main() is the one load_env() call of a run: the .env variables
        # (here DUPLICACY_CONFIG_DIR pointing at a sandbox config dir) must
        # be in the environment before any command — and so Config.load and
        # the executable resolution — reads them.
        monkeypatch.delenv("DUPLICACY_CONFIG_DIR", raising=False)
        config_dir = tmp_path / "sandbox-config"
        (tmp_path / ".env").write_text(f"DUPLICACY_CONFIG_DIR={config_dir}\n")
        monkeypatch.chdir(tmp_path)
        try:
            argv = ["config", "var", "duplicacy=/opt/duplicacy"]
            assert duplicacy.main(argv) == 0
            assert (config_dir / "config.yaml").read_text() == "duplicacy: /opt/duplicacy\n"
        finally:
            # load_dotenv() put the variable into os.environ directly; it
            # was absent before the test, so monkeypatch cannot restore
            # that state — pop it so it cannot leak into later tests.
            os.environ.pop("DUPLICACY_CONFIG_DIR", None)

    def test_initializes_config_and_repository(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            captured["args"] = args_list
            captured["cwd"] = cwd
            preferences = tmp_path / "repo" / ".duplicacy" / "preferences"
            preferences.parent.mkdir(parents=True)
            preferences.write_text("")
            return _cli.CliResult(args=args_list, returncode=0, stdout="Repository initialized\n", stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)

        argv = ["config", "init", "--config", str(tmp_path), "--storage", "/tmp/storage"]
        assert duplicacy.main(argv) == 0
        assert (tmp_path / "config.yaml").exists()
        assert (tmp_path / "repo").is_dir()
        assert captured["args"] == [fake_executable, "init", "duplicacy-py-dummy", "/tmp/storage"]
        assert captured["cwd"] == Path(str(tmp_path / "repo"))
        output = capsys.readouterr().out
        assert "Configuration initialized" in output
        assert "Repository initialized" in output

    def test_config_init_reports_existing_config(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "config.yaml").write_text("duplicacy: /opt/duplicacy\n")

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            preferences = tmp_path / "repo" / ".duplicacy" / "preferences"
            preferences.parent.mkdir(parents=True)
            preferences.write_text("")
            return _cli.CliResult(args=args_list, returncode=0, stdout="Repository initialized\n", stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)

        argv = ["config", "init", "--config", str(tmp_path), "--storage", "/tmp/storage"]
        assert duplicacy.main(argv) == 0
        assert (tmp_path / "config.yaml").read_text() == "duplicacy: /opt/duplicacy\n"
        assert "Configuration already exists" in capsys.readouterr().out

    def test_config_init_skips_existing_repository(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        preferences = tmp_path / "repo" / ".duplicacy" / "preferences"
        preferences.parent.mkdir(parents=True)
        preferences.write_text("")

        def fail_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            raise AssertionError("duplicacy init should not run for an already initialized repository")

        monkeypatch.setattr(_cli, "run_cli", fail_run_cli)

        argv = ["config", "init", "--config", str(tmp_path), "--storage", "/tmp/storage"]
        assert duplicacy.main(argv) == 0
        assert "Repository already initialized" in capsys.readouterr().out

    @pytest.mark.parametrize(
        ("argv", "expected_args", "output", "printed", "cwd_name"),
        [
            (
                ["backup", "--config", "/tmp/settings"],
                ["backup"],
                "Backup complete\n",
                "Backup complete\n",
                "/tmp/settings/repo",
            ),
            (
                ["prune", "--analyze", "--config", "/tmp/settings", "--snapshot-id", "vm"],
                ["list", "-id", "vm"],
                "Snapshot vm revision 5 created at 2026-01-01 10:00\n",
                "5 created at 2026-01-01 10:00\n",
                "/tmp/settings/repo",
            ),
        ],
    )
    def test_dispatches_command_in_config_repo(
        self,
        argv: list[str],
        expected_args: list[str],
        output: str,
        printed: str,
        cwd_name: str,
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        (Path(argv[argv.index("--config") + 1]) / "repo").mkdir(parents=True, exist_ok=True)
        captured: dict[str, object] = {}

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            captured["args"] = args_list
            captured["cwd"] = cwd
            return _cli.CliResult(args=args_list, returncode=0, stdout=output, stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)

        assert duplicacy.main(argv) == 0
        assert captured["args"] == [fake_executable, *expected_args]
        assert captured["cwd"] == Path(cwd_name)
        assert capsys.readouterr().out == printed

    def test_returns_one_on_cli_error(
        self,
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
        tmp_path: Path,
    ) -> None:
        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            raise _cli.CliError(args_list, 1, "Repository has not been initialized")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)

        # Pin the config directory so a machine's .env-configured sandbox
        # cannot change which error the command raises; the repo directory
        # must exist so prepare_repo passes and run_cli's error surfaces.
        config_dir = tmp_path / "settings"
        (config_dir / "repo").mkdir(parents=True)
        assert duplicacy.main(["backup", "--config", str(config_dir)]) == 1
        assert "Repository has not been initialized" in capsys.readouterr().err

    def test_returns_one_when_repo_directory_missing(
        self,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        def fail_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            raise AssertionError("the duplicacy CLI should not run without a repository directory")

        monkeypatch.setattr(_cli, "run_cli", fail_run_cli)

        config_dir = tmp_path / "settings"
        config_dir.mkdir()
        argv = ["backup", "--config", str(config_dir)]
        assert duplicacy.main(argv) == 1
        error = capsys.readouterr().err
        assert "Repository directory does not exist" in error
        assert "config init" in error

    @pytest.fixture()
    def snapshot_list_output(self) -> str:
        return (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 5 created at 2026-01-01 10:00\n"
            "Snapshot db revision 2 created at 2026-01-02 11:00\n"
        )

    def test_prune_with_picked_snapshot_id(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
        snapshot_list_output: str,
    ) -> None:
        (tmp_path / "repo").mkdir()
        captured: dict[str, object] = {}

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            captured["args"] = args_list
            captured["cwd"] = cwd
            if args_list[1:3] == ["list", "-all"]:
                return _cli.CliResult(args=args_list, returncode=0, stdout=snapshot_list_output, stderr="")
            return _cli.CliResult(
                args=args_list,
                returncode=0,
                stdout=(
                    "Storage set to /tmp/storage\n"
                    "Snapshot vm revision 12 created at 2026-01-01 10:00\n"
                    "Snapshot vm revision 5 created at 2026-01-01 09:45 -hash\n"
                ),
                stderr="",
            )

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(prune_command, "select_option", lambda message, choices: "vm")

        assert duplicacy.main(["prune", "--analyze", "--config", str(tmp_path)]) == 0
        assert captured["args"] == [fake_executable, "list", "-id", "vm"]
        assert captured["cwd"] == tmp_path / "repo"
        output = capsys.readouterr()
        assert output.out == "5 created at 2026-01-01 09:45\n12 created at 2026-01-01 10:00\n"
        # The retention summary and the picked id are informational only.
        assert "Retention policy: none (all revisions are kept)" in output.err
        assert "Retention anchor: latestRevision" in output.err
        assert "Snapshot id: vm" not in output.err

    def test_prune_forwards_duplicacy_stderr_to_stderr(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Regression test: prune parsed stdout only, so duplicacy's stderr
        # diagnostics (e.g. "Storage set to ...") never reached the user.
        (tmp_path / "repo").mkdir()
        stderr = "Storage set to /tmp/storage\n"

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            return _cli.CliResult(args=args_list, returncode=0, stdout="", stderr=stderr)

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)

        assert duplicacy.main(["prune", "--analyze", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        # The forwarded diagnostics follow the informational lines (the
        # retention summary printed before the duplicacy CLI runs).
        assert captured.err.endswith(stderr)
        assert "Retention policy:" in captured.err
        assert "Snapshot id: vm" in captured.err

    def test_prune_prints_policy_entries_before_picker(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
        snapshot_list_output: str,
    ) -> None:
        (tmp_path / "repo").mkdir()
        # The oldest age is listed first in the configuration on purpose:
        # the summary sorts the entries latest to earliest while keeping
        # each entry's configuration index.
        (tmp_path / "config.yaml").write_text(
            "retentionPolicy:\n- age: 1month\n  frequency: 1d\n- age: 7d\n  frequency: 1h\n"
            "retentionAnchor: today\n"
        )

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            if args_list[1:3] == ["list", "-all"]:
                return _cli.CliResult(args=args_list, returncode=0, stdout=snapshot_list_output, stderr="")
            raise AssertionError("the picker should not be offered after a cancelled selection")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(prune_command, "select_option", lambda message, choices: None)

        assert duplicacy.main(["prune", "--analyze", "--config", str(tmp_path)]) == 1
        error = capsys.readouterr().err
        # The policy and anchor are printed before the picker is offered,
        # latest entry (7d) first and earliest (1month) last, each with
        # its configuration-file index.
        assert error.startswith(
            "Retention anchor: today\n"
            "Retention policy: 2 entries\n"
            "  1: age=7d frequency=1h\n"
            "  0: age=1month frequency=1d\n"
        )

    def test_prune_prints_policy_and_cli_snapshot_id(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 1w\n  frequency: 1d\n")

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            if args_list[1:3] == ["list", "-all"]:
                raise AssertionError("the picker should not run when --snapshot-id is given")
            return _cli.CliResult(args=args_list, returncode=0, stdout="Storage set to /tmp/storage\n", stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        # Fixed now() keeps the empty-bucket headers deterministic: with
        # the 1w age anchored at midnight on 2026-09-01, the boundary is
        # 2026-08-25 00:00 and there are no revisions to fill either bucket.
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        assert duplicacy.main(["prune", "--analyze", "--config", str(tmp_path), "--snapshot-id", "db"]) == 0
        output = capsys.readouterr()
        assert output.out == (
            "Bucket 0: [the beginning, 2026-08-25 00:00)\n"
            "Bucket 1: [2026-08-25 00:00, now)\n"
        )
        assert output.err == (
            "Retention anchor: latestRevision\n"
            "Retention policy: 1 entry\n"
            "  0: age=1w frequency=1d\n"
            "Snapshot id: db\n"
        )

    def test_prune_dry_run_prints_the_commands_without_running(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # With --dry-run the command prints (to stderr, with stdout
        # staying empty) the duplicacy prune commands it would run (marked
        # "Would run:"), instead of running it: the same classification the
        # execute mode uses, and the duplicacy CLI only lists.
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text("retentionAnchor: today\nretentionPolicy:\n- age: 7d\n  frequency: 7d\n")
        list_output = (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 1 created at 2026-08-24 10:00\n"
            "Snapshot vm revision 2 created at 2026-08-24 11:00\n"
            "Snapshot vm revision 3 created at 2026-08-24 14:00\n"
        )
        seen: list[list[str]] = []

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            seen.append(args_list)
            return _cli.CliResult(args=args_list, returncode=0, stdout=list_output, stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        assert seen == [[fake_executable, "list", "-id", "vm"]]
        captured = capsys.readouterr()
        assert captured.out == ""
        # Revision 2 is the one the retention policy prunes (see
        # test_prune_marks_revisions_kept_or_pruned for the same scenario).
        assert captured.err == (
            "Retention anchor: today\n"
            "Retention policy: 1 entry\n"
            "  0: age=7d frequency=7d\n"
            "Snapshot id: vm\n"
            "Would run: /fake/duplicacy prune -id vm -r 2\n"
        )

    def test_prune_dry_run_prints_nothing_to_prune_when_all_kept(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # With no retention policy every revision is kept, so there is no
        # prune command to print (and in execute mode nothing would run).
        (tmp_path / "repo").mkdir()
        list_output = (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 1 created at 2026-08-24 10:00\n"
        )

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            return _cli.CliResult(args=args_list, returncode=0, stdout=list_output, stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            "Retention anchor: latestRevision\n"
            "Retention policy: none (all revisions are kept)\n"
            "Snapshot id: vm\n"
            "No revisions to prune\n"
        )

    def test_prune_dry_run_collapses_consecutive_revisions_into_ranges(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The upstream CLI's -r flag accepts single revisions and
        # start-end ranges (getRevisions in duplicacy_main.go), so a run
        # of consecutive pruned revisions collapses into one range and a
        # singleton stays a single number, all within one command.
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text("retentionAnchor: today\nretentionPolicy:\n- age: 7d\n  frequency: 7d\n")
        list_output = (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 1 created at 2026-08-19 09:00\n"
            "Snapshot vm revision 2 created at 2026-08-19 10:00\n"
            "Snapshot vm revision 3 created at 2026-08-19 11:00\n"
            "Snapshot vm revision 5 created at 2026-08-20 09:00\n"
            "Snapshot vm revision 6 created at 2026-08-20 10:00\n"
            "Snapshot vm revision 8 created at 2026-08-20 12:00\n"
            "Snapshot vm revision 9 created at 2026-08-25 10:00\n"
        )

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            return _cli.CliResult(args=args_list, returncode=0, stdout=list_output, stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        error = capsys.readouterr().err
        assert (
            "Would run: /fake/duplicacy prune -id vm -r 2-3 -r 5-6 -r 8\n"
        ) in error
        assert "-r 1" not in error and "-r 9" not in error

    def test_prune_dry_run_prints_no_prune_command_without_revisions(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # No revisions at all: nothing can be pruned, so only the
        # informational summary is printed.
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 7d\n  frequency: 7d\n")

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            return _cli.CliResult(args=args_list, returncode=0, stdout="Storage set to /tmp/storage\n", stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "Would run" not in captured.err
        assert "No revisions to prune" in captured.err

    def test_prune_dry_run_merges_all_ranges_into_one_command(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The upstream -r flag is a StringSlice (repeats delete the union),
        # so by default every pruned revision is one command with one -r
        # argument per consecutive range — one setup cost, no parallel runs.
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text("retentionAnchor: today\nretentionPolicy:\n- age: 7d\n  frequency: 7d\n")
        list_output = (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 1 created at 2026-08-20 09:00\n"
            "Snapshot vm revision 2 created at 2026-08-20 10:00\n"
            "Snapshot vm revision 3 created at 2026-08-20 11:00\n"
            "Snapshot vm revision 4 created at 2026-08-20 12:00\n"
            "Snapshot vm revision 6 created at 2026-08-20 14:00\n"
            "Snapshot vm revision 7 created at 2026-08-20 15:00\n"
            "Snapshot vm revision 9 created at 2026-08-20 17:00\n"
            "Snapshot vm revision 10 created at 2026-08-20 18:00\n"
        )

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            return _cli.CliResult(args=args_list, returncode=0, stdout=list_output, stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        error = capsys.readouterr().err
        assert error.endswith(
            "Snapshot id: vm\n"
            "Would run: /fake/duplicacy prune -id vm -r 2-4 -r 6-7 -r 9\n"
        )

    def test_prune_dry_run_splits_commands_at_the_range_limit(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # pruneMaxRangesPerCommand caps how many -r arguments merge into
        # one command: with a limit of 2, the third and fourth ranges move
        # to a second command.
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text(
            "retentionAnchor: today\npruneMaxRangesPerCommand: 2\nretentionPolicy:\n- age: 7d\n  frequency: 7d\n"
        )
        list_output = (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 1 created at 2026-08-20 09:00\n"
            "Snapshot vm revision 2 created at 2026-08-20 10:00\n"
            "Snapshot vm revision 3 created at 2026-08-20 11:00\n"
            "Snapshot vm revision 4 created at 2026-08-20 12:00\n"
            "Snapshot vm revision 6 created at 2026-08-20 14:00\n"
            "Snapshot vm revision 7 created at 2026-08-20 15:00\n"
            "Snapshot vm revision 9 created at 2026-08-20 17:00\n"
            "Snapshot vm revision 10 created at 2026-08-20 18:00\n"
        )

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            return _cli.CliResult(args=args_list, returncode=0, stdout=list_output, stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        error = capsys.readouterr().err
        assert error.endswith(
            "Snapshot id: vm\n"
            "Would run: /fake/duplicacy prune -id vm -r 2-4 -r 6-7\n"
            "Would run: /fake/duplicacy prune -id vm -r 9\n"
        )

    def test_prune_dry_run_rejects_zero_range_limit(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # The limit must be a positive integer: 0 or a negative value is
        # rejected before the duplicacy CLI runs.
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text("pruneMaxRangesPerCommand: 0\n")

        def fail_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            raise AssertionError("the duplicacy CLI should not run for an invalid range limit")

        monkeypatch.setattr(_cli, "run_cli", fail_run_cli)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 1
        assert "pruneMaxRangesPerCommand must be a positive integer" in capsys.readouterr().err

    def test_prune_dry_run_rejects_non_integer_range_limit(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text("pruneMaxRangesPerCommand: many\n")

        def fail_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            raise AssertionError("the duplicacy CLI should not run for an invalid range limit")

        monkeypatch.setattr(_cli, "run_cli", fail_run_cli)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 1
        assert "pruneMaxRangesPerCommand must be a positive integer" in capsys.readouterr().err

    def test_prune_without_flags_runs_the_prune_commands(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Without --dry-run (or --analyze) the command runs the duplicacy
        # prune command(s) that delete the pruned revisions: the same
        # classification --dry-run prints, one invocation per
        # up-to-the-limit batch of ranges.
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text("retentionAnchor: today\nretentionPolicy:\n- age: 7d\n  frequency: 7d\n")
        list_output = (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 1 created at 2026-08-19 09:00\n"
            "Snapshot vm revision 2 created at 2026-08-19 10:00\n"
            "Snapshot vm revision 3 created at 2026-08-19 11:00\n"
            "Snapshot vm revision 5 created at 2026-08-20 09:00\n"
            "Snapshot vm revision 6 created at 2026-08-20 10:00\n"
            "Snapshot vm revision 8 created at 2026-08-20 12:00\n"
            "Snapshot vm revision 9 created at 2026-08-25 10:00\n"
        )
        seen: list[list[str]] = []
        prune_stdout = "Storage set to /tmp/storage\nPruning snapshot vm\n"

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            seen.append(args_list)
            if args_list[1] == "prune":
                return _cli.CliResult(
                    args=args_list, returncode=0, stdout=prune_stdout, stderr="Storage set to /tmp/storage\n"
                )
            return _cli.CliResult(
                args=args_list, returncode=0, stdout=list_output, stderr="Storage set to /tmp/storage\n"
            )

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        assert duplicacy.main(["prune", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        # The duplicacy CLI runs once to list the revisions and once to
        # prune them (all ranges merged into one command by default).
        assert seen == [
            [fake_executable, "list", "-id", "vm"],
            [fake_executable, "prune", "-id", "vm", "-r", "2-3", "-r", "5-6", "-r", "8"],
        ]
        captured = capsys.readouterr()
        # The prune run's duplicacy output is forwarded to the streams
        # duplicacy writes: its stdout (where its diagnostics land) to
        # stdout, its stderr to stderr; the list run's raw output stays
        # parse-only and is not echoed.
        assert captured.out == prune_stdout
        # The forwarded diagnostics follow the informational lines (the
        # retention summary printed before the duplicacy CLI runs), once
        # for the list and once for the prune.
        assert captured.err.count("Storage set to /tmp/storage") == 2
        assert "Would run" not in captured.err

    def test_prune_without_flags_respects_the_range_limit(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # pruneMaxRangesPerCommand also caps how many -r arguments the
        # executed prune carries: with a limit of 2, the prune runs as two
        # sequential commands (never in parallel).
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text(
            "retentionAnchor: today\npruneMaxRangesPerCommand: 2\nretentionPolicy:\n- age: 7d\n  frequency: 7d\n"
        )
        list_output = (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 1 created at 2026-08-20 09:00\n"
            "Snapshot vm revision 2 created at 2026-08-20 10:00\n"
            "Snapshot vm revision 3 created at 2026-08-20 11:00\n"
            "Snapshot vm revision 4 created at 2026-08-20 12:00\n"
            "Snapshot vm revision 6 created at 2026-08-20 14:00\n"
            "Snapshot vm revision 7 created at 2026-08-20 15:00\n"
            "Snapshot vm revision 9 created at 2026-08-20 17:00\n"
            "Snapshot vm revision 10 created at 2026-08-20 18:00\n"
        )
        seen: list[list[str]] = []

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            seen.append(args_list)
            return _cli.CliResult(args=args_list, returncode=0, stdout=list_output, stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        assert duplicacy.main(["prune", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        assert seen == [
            [fake_executable, "list", "-id", "vm"],
            [fake_executable, "prune", "-id", "vm", "-r", "2-4", "-r", "6-7"],
            [fake_executable, "prune", "-id", "vm", "-r", "9"],
        ]

    def test_prune_without_flags_nothing_to_prune(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # With no retention policy every revision is kept, so the prune
        # command never runs.
        (tmp_path / "repo").mkdir()
        list_output = "Storage set to /tmp/storage\nSnapshot vm revision 1 created at 2026-08-24 10:00\n"
        seen: list[list[str]] = []

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            seen.append(args_list)
            return _cli.CliResult(args=args_list, returncode=0, stdout=list_output, stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        assert duplicacy.main(["prune", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        assert seen == [[fake_executable, "list", "-id", "vm"]]
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err.endswith("No revisions to prune\n")

    def test_prune_without_flags_failing_prune_command(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # A failing duplicacy prune command reports the error (to stderr)
        # and stops the run with exit code 1.
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text("retentionAnchor: today\nretentionPolicy:\n- age: 7d\n  frequency: 7d\n")
        list_output = (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 1 created at 2026-08-24 10:00\n"
            "Snapshot vm revision 2 created at 2026-08-24 11:00\n"
            "Snapshot vm revision 3 created at 2026-08-24 14:00\n"
        )
        seen: list[list[str]] = []

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            seen.append(args_list)
            if args_list[1] == "prune":
                raise _cli.CliError(args_list, 1, "prune failed\n")
            return _cli.CliResult(args=args_list, returncode=0, stdout=list_output, stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        assert duplicacy.main(["prune", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 1
        assert len(seen) == 2
        assert seen[1][1] == "prune"
        assert "prune failed" in capsys.readouterr().err

    def test_prune_analyze_still_only_lists(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # --analyze only prints the bucketed listing: the duplicacy CLI
        # runs once, to list the revisions.
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text("retentionAnchor: today\nretentionPolicy:\n- age: 7d\n  frequency: 7d\n")
        list_output = (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 1 created at 2026-08-24 10:00\n"
            "Snapshot vm revision 2 created at 2026-08-24 11:00\n"
            "Snapshot vm revision 3 created at 2026-08-24 14:00\n"
        )
        seen: list[list[str]] = []

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            seen.append(args_list)
            return _cli.CliResult(args=args_list, returncode=0, stdout=list_output, stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        assert duplicacy.main(["prune", "--analyze", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        assert seen == [[fake_executable, "list", "-id", "vm"]]
        captured = capsys.readouterr()
        # With a policy the bucketed kept/pruned listing prints to stdout.
        assert captured.out == (
            "Bucket 0: [the beginning, 2026-08-25 00:00)\n"
            "revision | created          | kept/pruned\n"
            "       1 | 2026-08-24 10:00 | kept\n"
            "       2 | 2026-08-24 11:00 | pruned\n"
            "       3 | 2026-08-24 14:00 | kept\n"
            "Bucket 1: [2026-08-25 00:00, now)\n"
        )
        assert "Would run" not in captured.err

    def test_prune_picker_cancelled_returns_one(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
        snapshot_list_output: str,
    ) -> None:
        (tmp_path / "repo").mkdir()

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            if args_list[1:3] == ["list", "-all"]:
                return _cli.CliResult(args=args_list, returncode=0, stdout=snapshot_list_output, stderr="")
            raise AssertionError("the duplicacy CLI should not run after a cancelled selection")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command.sys.stdin, "isatty", lambda: True)
        monkeypatch.setattr(prune_command, "select_option", lambda message, choices: None)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path)]) == 1
        assert capsys.readouterr().out == ""

    def test_prune_picker_requires_interactive_stdin(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
        snapshot_list_output: str,
    ) -> None:
        (tmp_path / "repo").mkdir()

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            if args_list[1:3] == ["list", "-all"]:
                return _cli.CliResult(args=args_list, returncode=0, stdout=snapshot_list_output, stderr="")
            raise AssertionError("the picker should not be offered when stdin is not interactive")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command.sys.stdin, "isatty", lambda: False)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path)]) == 1
        error = capsys.readouterr().err
        assert "--snapshot-id is required when stdin is not interactive" in error
        assert "db" in error and "vm" in error

    def test_prune_picker_with_no_snapshots(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "repo").mkdir()

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            return _cli.CliResult(args=args_list, returncode=0, stdout="Storage set to /tmp/storage\n", stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command.sys.stdin, "isatty", lambda: True)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path)]) == 1
        assert "No snapshots found in the repository" in capsys.readouterr().err

    def test_prune_classifies_revisions_into_retention_buckets(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text(
            "retentionPolicy:\n- age: 1d\n  frequency: 1h\n- age: 7d\n  frequency: 1h\n"
        )
        list_output = (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 1 created at 2026-08-20 10:00\n"
            "Snapshot vm revision 2 created at 2026-08-30 09:00\n"
            "Snapshot vm revision 3 created at 2026-09-01 11:00\n"
        )

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            return _cli.CliResult(args=args_list, returncode=0, stdout=list_output, stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        assert duplicacy.main(["prune", "--analyze", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        assert capsys.readouterr().out == (
            "Bucket 0: [the beginning, 2026-08-25 00:00)\n"
            "revision | created          | kept/pruned\n"
            "       1 | 2026-08-20 10:00 | kept\n"
            "Bucket 1: [2026-08-25 00:00, 2026-08-31 00:00)\n"
            "revision | created          | kept/pruned\n"
            "       2 | 2026-08-30 09:00 | kept\n"
            "Bucket 2: [2026-08-31 00:00, now)\n"
            "revision | created          | kept/pruned\n"
            "       3 | 2026-09-01 11:00 | kept\n"
        )

    def test_prune_prints_empty_buckets_without_revisions(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text("retentionAnchor: today\nretentionPolicy:\n- age: 1d\n  frequency: 1h\n")
        list_output = (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 5 created at 2026-08-01 10:00\n"
        )

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            return _cli.CliResult(args=args_list, returncode=0, stdout=list_output, stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        assert duplicacy.main(["prune", "--analyze", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        assert capsys.readouterr().out == (
            "Bucket 0: [the beginning, 2026-08-31 00:00)\n"
            "revision | created          | kept/pruned\n"
            "       5 | 2026-08-01 10:00 | kept\n"
            "Bucket 1: [2026-08-31 00:00, now)\n"
        )

    def test_prune_marks_revisions_kept_or_pruned(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "repo").mkdir()
        # retentionAnchor: today keeps the historical behaviour, where the
        # buckets are relative to today's midnight instead of the latest
        # revision's (the default). With now() fixed at noon on
        # 2026-09-01 but anchored at midnight, the 7d boundary falls at
        # 08-25 00:00 and the unbounded-past bucket's grid (frequency
        # 7d) ticks at 08-25 and 08-18 00:00; revisions 1 and 3 are
        # closest to those ticks and revision 2 is never the closest.
        (tmp_path / "config.yaml").write_text("retentionAnchor: today\nretentionPolicy:\n- age: 7d\n  frequency: 7d\n")
        list_output = (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 1 created at 2026-08-24 10:00\n"
            "Snapshot vm revision 2 created at 2026-08-24 11:00\n"
            "Snapshot vm revision 3 created at 2026-08-24 14:00\n"
        )

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            return _cli.CliResult(args=args_list, returncode=0, stdout=list_output, stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        assert duplicacy.main(["prune", "--analyze", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        assert capsys.readouterr().out == (
            "Bucket 0: [the beginning, 2026-08-25 00:00)\n"
            "revision | created          | kept/pruned\n"
            "       1 | 2026-08-24 10:00 | kept\n"
            "       2 | 2026-08-24 11:00 | pruned\n"
            "       3 | 2026-08-24 14:00 | kept\n"
            "Bucket 1: [2026-08-25 00:00, now)\n"
        )

    def test_prune_defaults_to_latest_revision_anchor(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 7d\n  frequency: 7d\n")
        list_output = (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 1 created at 2026-08-24 10:00\n"
            "Snapshot vm revision 2 created at 2026-08-24 11:00\n"
            "Snapshot vm revision 3 created at 2026-08-24 14:00\n"
        )

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            return _cli.CliResult(args=args_list, returncode=0, stdout=list_output, stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)
        monkeypatch.setattr(prune_command, "datetime", FixedDateTime)

        # The default anchor is midnight of the latest revision's day
        # (08-24 00:00), so the 7d boundary falls at 08-17 00:00 and all
        # 08-24 revisions land in the always-kept newest bucket — even
        # revision 2, which the today anchor would prune.
        assert duplicacy.main(["prune", "--analyze", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        assert capsys.readouterr().out == (
            "Bucket 0: [the beginning, 2026-08-17 00:00)\n"
            "Bucket 1: [2026-08-17 00:00, now)\n"
            "revision | created          | kept/pruned\n"
            "       1 | 2026-08-24 10:00 | kept\n"
            "       2 | 2026-08-24 11:00 | kept\n"
            "       3 | 2026-08-24 14:00 | kept\n"
        )

    def test_prune_returns_one_on_malformed_config_yaml(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Regression test: unparsable YAML used to escape as a raw
        # yaml.YAMLError traceback; the loaded Config now turns it into a
        # CliError, which main() reports with exit code 1 before anything runs.
        (tmp_path / "config.yaml").write_text("retentionPolicy: [unclosed\n")

        def fail_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            raise AssertionError("the duplicacy CLI should not run for malformed YAML")

        monkeypatch.setattr(_cli, "run_cli", fail_run_cli)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 1
        assert "config.yaml" in capsys.readouterr().err

    def test_prune_parses_config_yaml_once(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        # Regression test: prune used to re-read config.yaml for every
        # setting (policy, anchor, range limit, executable); loading the
        # Config once must parse the file a single time.
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text(
            "retentionPolicy:\n- age: 7d\n  frequency: 1d\nretentionAnchor: today\npruneMaxRangesPerCommand: 8\n"
        )
        list_output = (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 3 created at 2026-01-01 10:00\n"
            "Snapshot vm revision 2 created at 2026-01-01 09:30\n"
            "Snapshot vm revision 1 created at 2026-01-01 09:00\n"
        )
        parse_calls: list[int] = []
        real_safe_load = yaml.safe_load

        def counting_safe_load(stream: object) -> object:
            parse_calls.append(1)
            return real_safe_load(stream)

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            return _cli.CliResult(args=args_list, returncode=0, stdout=list_output, stderr="")

        monkeypatch.setattr(yaml, "safe_load", counting_safe_load)
        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 0
        assert len(parse_calls) == 1
        assert "Would run:" in capsys.readouterr().err

    def test_prune_returns_one_on_invalid_retention_anchor(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text("retentionAnchor: noon\nretentionPolicy:\n- age: 7d\n  frequency: 1h\n")

        def fail_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            raise AssertionError("the duplicacy CLI should not run for an invalid retention anchor")

        monkeypatch.setattr(_cli, "run_cli", fail_run_cli)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 1
        assert "retentionAnchor must be" in capsys.readouterr().err

    def test_prune_returns_one_on_invalid_retention_age(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 7x\n  frequency: 1h\n")

        def fail_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            raise AssertionError("the duplicacy CLI should not run for an invalid retention policy")

        monkeypatch.setattr(_cli, "run_cli", fail_run_cli)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 1
        assert "invalid duration" in capsys.readouterr().err

    def test_prune_returns_one_on_invalid_retention_frequency(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 7d\n  frequency: 1x\n")

        def fail_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            raise AssertionError("the duplicacy CLI should not run for an invalid retention policy")

        monkeypatch.setattr(_cli, "run_cli", fail_run_cli)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 1
        assert "invalid duration" in capsys.readouterr().err

    def test_prune_returns_one_on_duplicate_retention_age(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "repo").mkdir()
        (tmp_path / "config.yaml").write_text(
            "retentionPolicy:\n- age: 7d\n  frequency: 1h\n- age: 1w\n  frequency: 1h\n"
        )

        def fail_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            raise AssertionError("the duplicacy CLI should not run for an invalid retention policy")

        monkeypatch.setattr(_cli, "run_cli", fail_run_cli)

        assert duplicacy.main(["prune", "--dry-run", "--config", str(tmp_path), "--snapshot-id", "vm"]) == 1
        assert "ages must be unique" in capsys.readouterr().err

    def test_lists_snapshot_ids(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "repo").mkdir()
        captured: dict[str, object] = {}
        list_output = (
            "Storage set to /tmp/storage\n"
            "Snapshot vm revision 5 created at 2026-01-01 10:00\n"
            "Snapshot db revision 2 created at 2026-01-02 11:00\n"
            "Snapshot db revision 3 created at 2026-01-03 12:00\n"
            "Snapshot vm revision 6 created at 2026-01-04 13:00\n"
        )

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            captured["args"] = args_list
            captured["cwd"] = cwd
            return _cli.CliResult(args=args_list, returncode=0, stdout=list_output, stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)

        argv = ["list", "--config", str(tmp_path)]
        assert duplicacy.main(argv) == 0
        assert captured["args"] == [fake_executable, "list", "-all"]
        assert captured["cwd"] == tmp_path / "repo"
        assert capsys.readouterr().out == "db\nvm\n"

    def test_list_returns_one_on_cli_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "repo").mkdir()

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            raise _cli.CliError(args_list, 1, "Storage is not reachable")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)

        argv = ["list", "--config", str(tmp_path)]
        assert duplicacy.main(argv) == 1
        assert "Storage is not reachable" in capsys.readouterr().err

    def test_list_with_no_snapshots_prints_nothing(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        (tmp_path / "repo").mkdir()

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            return _cli.CliResult(args=args_list, returncode=0, stdout="Storage set to /tmp/storage\n", stderr="")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)

        argv = ["list", "--config", str(tmp_path)]
        assert duplicacy.main(argv) == 0
        assert capsys.readouterr().out == ""

    def test_config_init_returns_one_on_cli_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            raise _cli.CliError(args_list, 1, "Storage is not reachable")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)

        argv = ["config", "init", "--config", str(tmp_path), "--storage", "/tmp/storage"]
        assert duplicacy.main(argv) == 1
        assert "Storage is not reachable" in capsys.readouterr().err

    def test_saves_config(self, tmp_path, capsys) -> None:
        assert duplicacy.main(["config", "var", "--config", str(tmp_path), "duplicacy=/opt/duplicacy"]) == 0
        assert (tmp_path / "config.yaml").read_text() == "duplicacy: /opt/duplicacy\n"
        assert "Configuration saved" in capsys.readouterr().out

    def test_rejects_invalid_config_variable(self, tmp_path, capsys) -> None:
        assert duplicacy.main(["config", "var", "--config", str(tmp_path), "duplicacy"]) == 1
        assert "NAME=VALUE" in capsys.readouterr().err

    def test_rejects_unsupported_variable_name(self, tmp_path, capsys) -> None:
        # A typo used to be saved and silently ignored; save_variable
        # validates the name against the supported variables now.
        assert duplicacy.main(["config", "var", "--config", str(tmp_path), "duplicaty=/opt/duplicacy"]) == 1
        err = capsys.readouterr().err
        assert "unsupported configuration variable 'duplicaty'" in err
        assert "supported variables: duplicacy, retentionAnchor, pruneMaxRangesPerCommand" in err
        assert not (tmp_path / "config.yaml").exists()

    def test_rejects_retention_policy_variable(self, tmp_path, capsys) -> None:
        # The retention policy is managed by `config retention-policy`, not
        # `config var`; saving it would bypass entry validation.
        assert duplicacy.main(["config", "var", "--config", str(tmp_path), "retentionPolicy=whatever"]) == 1
        err = capsys.readouterr().err
        assert "managed by 'config retention-policy add'" in err
        assert not (tmp_path / "config.yaml").exists()

    def test_rejects_non_positive_prune_max_ranges(self, tmp_path, capsys) -> None:
        # The value must be a positive integer; the save path validates it
        # so no later prune run fails on the stored value.
        assert duplicacy.main(["config", "var", "--config", str(tmp_path), "pruneMaxRangesPerCommand=zero"]) == 1
        assert "must be a positive integer" in capsys.readouterr().err
        assert not (tmp_path / "config.yaml").exists()

    def test_saves_prune_max_ranges_as_int(self, tmp_path, capsys) -> None:
        assert duplicacy.main(["config", "var", "--config", str(tmp_path), "pruneMaxRangesPerCommand=32"]) == 0
        assert (tmp_path / "config.yaml").read_text() == "pruneMaxRangesPerCommand: 32\n"
        assert "Configuration saved" in capsys.readouterr().out

    def test_var_help_lists_supported_variables(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as excinfo:
            duplicacy.parse_args(["config", "var", "--help"])
        assert excinfo.value.code == 0
        # argparse re-wraps the help text at the terminal width, so match
        # the list on whitespace-normalized output.
        out = " ".join(capsys.readouterr().out.split())
        assert (
            "supported variables: duplicacy, retentionAnchor, pruneMaxRangesPerCommand" in out
        )

    def test_rejects_whitespace_only_variable_name(self, tmp_path, capsys) -> None:
        # Regression test: a whitespace-only or padded name could never be
        # resolved deliberately afterwards, but it used to be saved anyway.
        assert duplicacy.main(["config", "var", "--config", str(tmp_path), " =value"]) == 1
        assert "surrounding whitespace" in capsys.readouterr().err
        assert not (tmp_path / "config.yaml").exists()

    def test_rejects_padded_variable_name(self, tmp_path, capsys) -> None:
        assert duplicacy.main(["config", "var", "--config", str(tmp_path), " duplicacy=/opt/duplicacy"]) == 1
        assert "surrounding whitespace" in capsys.readouterr().err
        assert not (tmp_path / "config.yaml").exists()

    def test_rejects_newlines_in_variable_name(self, tmp_path, capsys) -> None:
        assert duplicacy.main(["config", "var", "--config", str(tmp_path), "dup\nlicacy=/opt/duplicacy"]) == 1
        assert "newlines" in capsys.readouterr().err
        assert not (tmp_path / "config.yaml").exists()


class TestRetention:
    def test_parses_retention_add(self) -> None:
        args = duplicacy.parse_args(
            ["config", "retention-policy", "add", "--age", "7d", "--frequency", "1h"]
        )
        assert args.command == "config"
        assert args.config_command == "retention-policy"
        assert args.retention_command == "add"
        assert args.age == "7d"
        assert args.frequency == "1h"

    def test_parses_retention_list_and_remove(self) -> None:
        args = duplicacy.parse_args(["config", "retention-policy", "list"])
        assert args.config_command == "retention-policy"
        assert args.retention_command == "list"
        args = duplicacy.parse_args(["config", "retention-policy", "remove", "2"])
        assert args.retention_command == "remove"
        assert args.index == 2

    def test_retention_requires_subcommand(self) -> None:
        with pytest.raises(SystemExit):
            duplicacy.parse_args(["config", "retention-policy"])

    def test_add_appends_entry(self, tmp_path: Path, capsys) -> None:
        argv = ["config", "retention-policy", "add", "--config", str(tmp_path), "--age", "7d", "--frequency", "1h"]
        assert duplicacy.main(argv) == 0
        argv2 = ["config", "retention-policy", "add", "--config", str(tmp_path), "--age", "30d", "--frequency", "1h"]
        assert duplicacy.main(argv2) == 0
        assert (tmp_path / "config.yaml").read_text() == (
            "retentionPolicy:\n- age: 7d\n  frequency: 1h\n- age: 30d\n  frequency: 1h\n"
        )
        assert "Retention policy saved" in capsys.readouterr().out

    def test_add_rejects_duplicate_age(self, tmp_path: Path, capsys) -> None:
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 7d\n  frequency: 1h\n")
        argv = ["config", "retention-policy", "add", "--config", str(tmp_path), "--age", "1w", "--frequency", "1h"]
        assert duplicacy.main(argv) == 1
        assert "ages must be unique" in capsys.readouterr().err
        assert (tmp_path / "config.yaml").read_text() == "retentionPolicy:\n- age: 7d\n  frequency: 1h\n"

    def test_add_rejects_invalid_duration(self, tmp_path, capsys) -> None:
        argv = ["config", "retention-policy", "add", "--config", str(tmp_path), "--age", "7x", "--frequency", "1h"]
        assert duplicacy.main(argv) == 1
        assert "invalid duration" in capsys.readouterr().err
        assert not (tmp_path / "config.yaml").exists()

    def test_list_prints_indexed_entries(self, tmp_path, capsys) -> None:
        (tmp_path / "config.yaml").write_text(
            "retentionPolicy:\n- age: 7d\n  frequency: 1h\n- age: 30d\n  frequency: 1d\n"
        )
        assert duplicacy.main(["config", "retention-policy", "list", "--config", str(tmp_path)]) == 0
        assert capsys.readouterr().out == "0: age=7d frequency=1h\n1: age=30d frequency=1d\n"

    def test_list_empty_policy(self, tmp_path, capsys) -> None:
        assert duplicacy.main(["config", "retention-policy", "list", "--config", str(tmp_path)]) == 0
        assert "No retention policy entries" in capsys.readouterr().out

    def test_remove_deletes_entry_by_index(self, tmp_path, capsys) -> None:
        (tmp_path / "config.yaml").write_text(
            "retentionPolicy:\n- age: 7d\n  frequency: 1h\n- age: 30d\n  frequency: 1d\n"
        )
        assert duplicacy.main(["config", "retention-policy", "remove", "0", "--config", str(tmp_path)]) == 0
        assert (tmp_path / "config.yaml").read_text() == "retentionPolicy:\n- age: 30d\n  frequency: 1d\n"
        assert "Removed entry 0" in capsys.readouterr().out

    def test_remove_rejects_out_of_range_index(self, tmp_path, capsys) -> None:
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 7d\n  frequency: 1h\n")
        assert duplicacy.main(["config", "retention-policy", "remove", "1", "--config", str(tmp_path)]) == 1
        assert "index out of range" in capsys.readouterr().err

    def test_preserves_other_variables(self, tmp_path, capsys) -> None:
        (tmp_path / "config.yaml").write_text("duplicacy: /opt/duplicacy\n")
        argv = ["config", "retention-policy", "add", "--config", str(tmp_path), "--age", "1w", "--frequency", "1d"]
        assert duplicacy.main(argv) == 0
        assert (tmp_path / "config.yaml").read_text() == (
            "duplicacy: /opt/duplicacy\nretentionPolicy:\n- age: 1w\n  frequency: 1d\n"
        )

    def test_list_rejects_duplicate_ages(self, tmp_path, capsys) -> None:
        (tmp_path / "config.yaml").write_text(
            "retentionPolicy:\n- age: 7d\n  frequency: 1h\n- age: 1w\n  frequency: 1d\n"
        )
        assert duplicacy.main(["config", "retention-policy", "list", "--config", str(tmp_path)]) == 1
        assert "ages must be unique" in capsys.readouterr().err

    def test_remove_rejects_duplicate_ages(self, tmp_path, capsys) -> None:
        (tmp_path / "config.yaml").write_text(
            "retentionPolicy:\n- age: 7d\n  frequency: 1h\n- age: 1w\n  frequency: 1d\n"
        )
        assert duplicacy.main(["config", "retention-policy", "remove", "0", "--config", str(tmp_path)]) == 1
        assert "ages must be unique" in capsys.readouterr().err
        assert (tmp_path / "config.yaml").read_text() == (
            "retentionPolicy:\n- age: 7d\n  frequency: 1h\n- age: 1w\n  frequency: 1d\n"
        )

    def test_add_rejects_unsupported_frequency(self, tmp_path, capsys) -> None:
        argv = ["config", "retention-policy", "add", "--config", str(tmp_path), "--age", "7d", "--frequency", "5m"]
        assert duplicacy.main(argv) == 1
        assert "unsupported retention policy frequency" in capsys.readouterr().err
        assert not (tmp_path / "config.yaml").exists()
