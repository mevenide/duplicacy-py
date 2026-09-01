"""Tests for `duplicacy_scripts.main`."""

from __future__ import annotations

from pathlib import Path

import pytest

from duplicacy_scripts import cli
from duplicacy_scripts import main as duplicacy


FAKE_DUPLICACY = "/fake/duplicacy"


@pytest.fixture()
def fake_executable(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(duplicacy, "resolve_executable", lambda explicit, config_dir=None: FAKE_DUPLICACY)
    return FAKE_DUPLICACY


class TestParseArgs:
    def test_requires_command(self) -> None:
        with pytest.raises(SystemExit):
            duplicacy.parse_args([])

    def test_parses_backup(self) -> None:
        args = duplicacy.parse_args(["backup", "--repository", "/tmp/repo"])
        assert args.command == "backup"
        assert args.repository == "/tmp/repo"

    def test_parses_prune(self) -> None:
        args = duplicacy.parse_args(["prune", "--repository", "/tmp/repo", "--id", "vm"])
        assert args.command == "prune"
        assert args.id == "vm"

    def test_parses_config_var(self) -> None:
        args = duplicacy.parse_args(["config", "var", "--config", "/tmp/settings", "duplicacy=/opt/duplicacy"])
        assert args.command == "config"
        assert args.config_command == "var"
        assert args.config == "/tmp/settings"
        assert args.assignment == "duplicacy=/opt/duplicacy"

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


class TestMain:
    def test_initializes_config_and_repository(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        captured: dict[str, object] = {}

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> cli.CliResult:
            captured["args"] = args_list
            captured["cwd"] = cwd
            preferences = tmp_path / "repo" / ".duplicacy" / "preferences"
            preferences.parent.mkdir(parents=True)
            preferences.write_text("")
            return cli.CliResult(args=args_list, returncode=0, stdout="Repository initialized\n", stderr="")

        monkeypatch.setattr(duplicacy, "run_cli", fake_run_cli)

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

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> cli.CliResult:
            preferences = tmp_path / "repo" / ".duplicacy" / "preferences"
            preferences.parent.mkdir(parents=True)
            preferences.write_text("")
            return cli.CliResult(args=args_list, returncode=0, stdout="Repository initialized\n", stderr="")

        monkeypatch.setattr(duplicacy, "run_cli", fake_run_cli)

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

        def fail_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> cli.CliResult:
            raise AssertionError("duplicacy init should not run for an already initialized repository")

        monkeypatch.setattr(duplicacy, "run_cli", fail_run_cli)

        argv = ["config", "init", "--config", str(tmp_path), "--storage", "/tmp/storage"]
        assert duplicacy.main(argv) == 0
        assert "Repository already initialized" in capsys.readouterr().out

    def test_config_init_returns_one_on_cli_error(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> cli.CliResult:
            raise cli.CliError(args_list, 1, "Storage is not reachable")

        monkeypatch.setattr(duplicacy, "run_cli", fake_run_cli)

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

    @pytest.mark.parametrize(
        ("argv", "expected_args", "output"),
        [
            (["backup", "--repository", "/tmp/repo"], ["backup"], "Backup complete\n"),
            (["prune", "--repository", "/tmp/repo", "--id", "vm"], ["list", "-id", "vm"], "revision 5\n"),
        ],
    )
    def test_dispatches_command(
        self,
        argv: list[str],
        expected_args: list[str],
        output: str,
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        captured: dict[str, object] = {}

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> cli.CliResult:
            captured["args"] = args_list
            captured["cwd"] = cwd
            return cli.CliResult(args=args_list, returncode=0, stdout=output, stderr="")

        monkeypatch.setattr(duplicacy, "run_cli", fake_run_cli)

        assert duplicacy.main(argv) == 0
        assert captured["args"] == [fake_executable, *expected_args]
        assert captured["cwd"] == "/tmp/repo"
        assert capsys.readouterr().out == output

    def test_returns_one_on_cli_error(
        self,
        fake_executable: str,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> cli.CliResult:
            raise cli.CliError(args_list, 1, "Repository has not been initialized")

        monkeypatch.setattr(duplicacy, "run_cli", fake_run_cli)

        assert duplicacy.main(["backup", "--repository", "/tmp/repo"]) == 1
        assert "Repository has not been initialized" in capsys.readouterr().err