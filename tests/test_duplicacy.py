"""Tests for `duplicacy_scripts.main`."""

from __future__ import annotations

import pytest

from duplicacy_scripts import cli
from duplicacy_scripts import main as duplicacy


FAKE_DUPLICACY = "/fake/duplicacy"


@pytest.fixture()
def fake_executable(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(duplicacy, "resolve_executable", lambda explicit: FAKE_DUPLICACY)
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


class TestMain:
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