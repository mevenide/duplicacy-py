"""Tests for `scripts/prune.py`."""

from __future__ import annotations

import sys

import pytest

from duplicacy_scripts import cli

sys.path.insert(0, "scripts")

import prune  # noqa: E402
from prune import main, parse_args  # noqa: E402

FAKE_DUPLICACY = "/fake/duplicacy"


@pytest.fixture()
def fake_executable(monkeypatch: pytest.MonkeyPatch) -> str:
    # Resolve the executable without depending on a real duplicacy install.
    monkeypatch.setattr(prune, "resolve_executable", lambda explicit: FAKE_DUPLICACY)
    return FAKE_DUPLICACY


class TestParseArgs:
    def test_requires_repository_and_id(self) -> None:
        with pytest.raises(SystemExit):
            parse_args([])
        with pytest.raises(SystemExit):
            parse_args(["--repository", "/tmp/repo"])
        with pytest.raises(SystemExit):
            parse_args(["--id", "vm"])

    def test_parses_repository_and_id(self) -> None:
        args = parse_args(["--repository", "/tmp/repo", "--id", "vm"])
        assert args.repository == "/tmp/repo"
        assert args.id == "vm"


class TestMain:
    def test_lists_revisions_for_snapshot_id(
        self, fake_executable: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        captured: dict[str, object] = {}

        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> cli.CliResult:
            captured["args"] = args_list
            captured["cwd"] = cwd
            return cli.CliResult(args=args_list, returncode=0, stdout="Snapshot vm revision 5\n", stderr="")

        monkeypatch.setattr(prune, "run_cli", fake_run_cli)

        exit_code = main(["--repository", "/tmp/repo", "--id", "vm"])

        assert exit_code == 0
        assert captured["args"] == [fake_executable, "list", "-id", "vm"]
        assert captured["cwd"] == "/tmp/repo"
        assert capsys.readouterr().out == "Snapshot vm revision 5\n"

    def test_returns_one_on_cli_error(
        self, fake_executable: str, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        def fake_run_cli(args_list: list[str], cwd: str | None = None, check: bool = True) -> cli.CliResult:
            raise cli.CliError(args_list, 1, "Repository has not been initialized")

        monkeypatch.setattr(prune, "run_cli", fake_run_cli)

        exit_code = main(["--repository", "/tmp/repo", "--id", "vm"])

        assert exit_code == 1
        assert "Repository has not been initialized" in capsys.readouterr().err