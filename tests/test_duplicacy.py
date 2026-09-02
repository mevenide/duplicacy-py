"""Tests for the `duplicacy-py` entry point and its command modules.

The command modules call the shared helpers through the internal
`duplicacy_scripts._cli` module, so tests stub them there; the interactive
prune picker is stubbed on `duplicacy_scripts.commands.prune`.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from duplicacy_scripts import _cli
from duplicacy_scripts import main as duplicacy
from duplicacy_scripts.commands import prune as prune_command


FAKE_DUPLICACY = "/fake/duplicacy"


@pytest.fixture()
def fake_executable(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(_cli, "resolve_executable", lambda config_dir=None: FAKE_DUPLICACY)
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
            (["backup", "--config", "/tmp/settings"], ["backup"], "Backup complete\n", "Backup complete\n", "/tmp/settings/repo"),
            (
                ["prune", "--config", "/tmp/settings", "--snapshot-id", "vm"],
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
    ) -> None:
        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> _cli.CliResult:
            raise _cli.CliError(args_list, 1, "Repository has not been initialized")

        monkeypatch.setattr(_cli, "run_cli", fake_run_cli)

        assert duplicacy.main(["backup"]) == 1
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

        assert duplicacy.main(["prune", "--config", str(tmp_path)]) == 0
        assert captured["args"] == [fake_executable, "list", "-id", "vm"]
        assert captured["cwd"] == tmp_path / "repo"
        assert capsys.readouterr().out == "5 created at 2026-01-01 09:45\n12 created at 2026-01-01 10:00\n"

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

        assert duplicacy.main(["prune", "--config", str(tmp_path)]) == 1
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

        assert duplicacy.main(["prune", "--config", str(tmp_path)]) == 1
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

        assert duplicacy.main(["prune", "--config", str(tmp_path)]) == 1
        assert "No snapshots found in the repository" in capsys.readouterr().err

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
        assert duplicacy.main(argv) == 0
        assert (tmp_path / "config.yaml").read_text() == (
            "retentionPolicy:\n- age: 7d\n  frequency: 1h\n- age: 7d\n  frequency: 1h\n"
        )
        assert "Retention policy saved" in capsys.readouterr().out

    def test_add_rejects_invalid_duration(self, tmp_path, capsys) -> None:
        argv = ["config", "retention-policy", "add", "--config", str(tmp_path), "--age", "7x", "--frequency", "1h"]
        assert duplicacy.main(argv) == 1
        assert "invalid duration" in capsys.readouterr().err
        assert not (tmp_path / "config.yaml").exists()

    def test_list_prints_indexed_entries(self, tmp_path, capsys) -> None:
        (tmp_path / "config.yaml").write_text(
            "retentionPolicy:\n- age: 7d\n  frequency: 1h\n- age: 1w\n  frequency: 5m\n"
        )
        assert duplicacy.main(["config", "retention-policy", "list", "--config", str(tmp_path)]) == 0
        assert capsys.readouterr().out == "0: age=7d frequency=1h\n1: age=1w frequency=5m\n"

    def test_list_empty_policy(self, tmp_path, capsys) -> None:
        assert duplicacy.main(["config", "retention-policy", "list", "--config", str(tmp_path)]) == 0
        assert "No retention policy entries" in capsys.readouterr().out

    def test_remove_deletes_entry_by_index(self, tmp_path, capsys) -> None:
        (tmp_path / "config.yaml").write_text(
            "retentionPolicy:\n- age: 7d\n  frequency: 1h\n- age: 1w\n  frequency: 5m\n"
        )
        assert duplicacy.main(["config", "retention-policy", "remove", "0", "--config", str(tmp_path)]) == 0
        assert (tmp_path / "config.yaml").read_text() == "retentionPolicy:\n- age: 1w\n  frequency: 5m\n"
        assert "Removed entry 0" in capsys.readouterr().out

    def test_remove_rejects_out_of_range_index(self, tmp_path, capsys) -> None:
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 7d\n  frequency: 1h\n")
        assert duplicacy.main(["config", "retention-policy", "remove", "1", "--config", str(tmp_path)]) == 1
        assert "index out of range" in capsys.readouterr().err

    def test_preserves_other_variables(self, tmp_path, capsys) -> None:
        (tmp_path / "config.yaml").write_text("duplicacy: /opt/duplicacy\n")
        argv = ["config", "retention-policy", "add", "--config", str(tmp_path), "--age", "1w", "--frequency", "5m"]
        assert duplicacy.main(argv) == 0
        assert (tmp_path / "config.yaml").read_text() == (
            "duplicacy: /opt/duplicacy\nretentionPolicy:\n- age: 1w\n  frequency: 5m\n"
        )