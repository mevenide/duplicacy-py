"""Tests for the shared CLI helpers in `src/duplicacy_scripts/cli.py`."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest

from duplicacy_scripts.cli import CliError, load_env, resolve_executable, run_cli


class TestResolveExecutable:
    def test_explicit_argument_wins(self) -> None:
        assert resolve_executable("Duplicacy.exe") == "Duplicacy.exe"

    def test_env_var_used_when_no_argument(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "/opt/tools/duplicacy")
        assert resolve_executable(None) == "/opt/tools/duplicacy"

    def test_default_falls_back_to_path(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DUPLICACY_EXECUTABLE", raising=False)
        # `python` is guaranteed to be on PATH inside the test venv.
        assert resolve_executable(None, default=sys.executable) == sys.executable

    def test_raises_when_nothing_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "")
        monkeypatch.setattr(shutil, "which", lambda name: None)
        with pytest.raises(CliError):
            resolve_executable(None, default="definitely-not-a-real-binary-xyz")

    def test_raises_message_mentions_env_var_and_env_file(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "")
        monkeypatch.setattr(shutil, "which", lambda name: None)
        with pytest.raises(CliError) as excinfo:
            resolve_executable(None, default="definitely-not-a-real-binary-xyz")
        assert "DUPLICACY_EXECUTABLE" in str(excinfo.value)
        assert ".env" in str(excinfo.value)


class TestRunCli:
    def test_returns_result_on_success(self) -> None:
        result = run_cli([sys.executable, "-c", "print('hello')"])
        assert result.returncode == 0
        assert result.stdout == "hello\n"
        assert result.output == "hello\n"

    def test_args_are_stringified(self) -> None:
        # A path that only exists as a Path object must be passed through cleanly.
        tmp = Path("/") / "tmp"
        result = run_cli([sys.executable, "-c", "import sys; print(len(sys.argv))", tmp])
        assert result.stdout == "2\n"

    def test_raises_on_nonzero_exit(self) -> None:
        with pytest.raises(CliError) as excinfo:
            run_cli([sys.executable, "-c", "raise SystemExit(3)"])
        assert excinfo.value.returncode == 3

    def test_check_false_returns_result(self) -> None:
        result = run_cli([sys.executable, "-c", "raise SystemExit(3)"], check=False)
        assert result.returncode == 3

    def test_cwd_is_respected(self, tmp_path: Path) -> None:
        result = run_cli([sys.executable, "-c", "import os; print(os.getcwd())"], cwd=tmp_path)
        assert result.stdout.strip() == str(tmp_path)

    def test_stderr_is_captured(self) -> None:
        result = run_cli([sys.executable, "-c", "import sys; print('oops', file=sys.stderr)"])
        assert result.stderr == "oops\n"
        assert "oops" in result.output


class TestLoadEnv:
    def test_loads_env_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DUPLICACY_EXECUTABLE", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("DUPLICACY_EXECUTABLE=/opt/tools/duplicacy\n")
        load_env(env_file)
        assert resolve_executable(None) == "/opt/tools/duplicacy"

    def test_real_env_wins_over_env_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "/from/real/env")
        env_file = tmp_path / ".env"
        env_file.write_text("DUPLICACY_EXECUTABLE=/from/env/file\n")
        load_env(env_file)
        assert resolve_executable(None) == "/from/real/env"