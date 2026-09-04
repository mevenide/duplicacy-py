"""Tests for the internal CLI helpers in `src/duplicacy_scripts/_cli.py`."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest

from duplicacy_scripts._cli import (
    CliError,
    RetentionAnchor,
    config_file,
    default_config_dir,
    init_config,
    load_config,
    load_env,
    load_retention_anchor,
    load_retention_policy,
    repo_dir,
    resolve_executable,
    run_cli,
    save_config,
    save_retention_policy,
)


class TestConfig:
    def test_default_config_dir_uses_platformdirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # chdir away from the checkout and clear the variable so a real
        # .env-configured sandbox cannot shadow the platformdirs fallback.
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DUPLICACY_CONFIG_DIR", raising=False)
        monkeypatch.setattr("duplicacy_scripts._cli.user_config_dir", lambda app_name: str(tmp_path / app_name))
        assert default_config_dir() == tmp_path / "duplicacy-py"

    def test_env_var_points_default_config_dir_at_sandbox(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sandbox = tmp_path / "sandbox-config"
        sandbox.mkdir()
        monkeypatch.setenv("DUPLICACY_CONFIG_DIR", str(sandbox))
        assert default_config_dir() == sandbox

    def test_env_var_from_dotenv_file_points_default_config_dir_at_sandbox(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # The checkout's gitignored .env sets DUPLICACY_CONFIG_DIR so that
        # development runs use the sandbox configuration directory.
        monkeypatch.delenv("DUPLICACY_CONFIG_DIR", raising=False)
        sandbox = tmp_path / "sandbox-config"
        sandbox.mkdir()
        (tmp_path / ".env").write_text(f"DUPLICACY_CONFIG_DIR={sandbox}\n")
        monkeypatch.chdir(tmp_path)
        try:
            assert default_config_dir() == sandbox
        finally:
            # load_dotenv() put the variable into os.environ directly; it
            # was absent before the test, so monkeypatch cannot restore
            # that state — pop it so it cannot leak into later tests.
            os.environ.pop("DUPLICACY_CONFIG_DIR", None)

    def test_env_var_expands_user_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DUPLICACY_CONFIG_DIR", "~/sandbox-config")
        assert default_config_dir() == Path.home() / "sandbox-config"

    def test_env_var_flows_into_config_file_and_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sandbox = tmp_path / "sandbox-config"
        sandbox.mkdir()
        monkeypatch.setenv("DUPLICACY_CONFIG_DIR", str(sandbox))
        assert config_file() == sandbox / "config.yaml"
        assert repo_dir() == sandbox / "repo"

    def test_explicit_config_dir_beats_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sandbox = tmp_path / "sandbox-config"
        sandbox.mkdir()
        monkeypatch.setenv("DUPLICACY_CONFIG_DIR", str(sandbox))
        explicit = tmp_path / "explicit"
        explicit.mkdir()
        assert config_file(explicit) == explicit / "config.yaml"
        assert repo_dir(explicit) == explicit / "repo"

    def test_saves_and_resolves_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "")
        save_config("duplicacy", "/opt/tools/duplicacy", tmp_path)
        assert (tmp_path / "config.yaml").read_text() == "duplicacy: /opt/tools/duplicacy\n"
        assert resolve_executable(config_dir=tmp_path) == "/opt/tools/duplicacy"

    def test_initializes_config(self, tmp_path: Path) -> None:
        path = init_config(tmp_path)
        assert path == tmp_path / "config.yaml"
        assert path.exists()
        assert path.read_text() == ""

    def test_init_preserves_existing_config(self, tmp_path: Path) -> None:
        save_config("duplicacy", "/opt/tools/duplicacy", tmp_path)
        assert init_config(tmp_path) == tmp_path / "config.yaml"
        assert (tmp_path / "config.yaml").read_text() == "duplicacy: /opt/tools/duplicacy\n"

    def test_updates_existing_config(self, tmp_path: Path) -> None:
        save_config("other", "value", tmp_path)
        save_config("duplicacy", "/opt/tools/duplicacy", tmp_path)
        assert (tmp_path / "config.yaml").read_text() == "duplicacy: /opt/tools/duplicacy\nother: value\n"

    def test_load_config_returns_mapping(self, tmp_path: Path) -> None:
        save_config("duplicacy", "/opt/tools/duplicacy", tmp_path)
        assert load_config(tmp_path) == {"duplicacy": "/opt/tools/duplicacy"}

    def test_load_config_missing_file_yields_empty_mapping(self, tmp_path: Path) -> None:
        assert load_config(tmp_path) == {}

    def test_load_config_rejects_non_mapping(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("- just\n- a\n- list\n")
        with pytest.raises(TypeError) as excinfo:
            load_config(tmp_path)
        # The message names the offending configuration file.
        assert str(tmp_path / "config.yaml") in str(excinfo.value)


class TestRetentionPolicy:
    def test_missing_file_yields_empty_policy(self, tmp_path: Path) -> None:
        assert load_retention_policy(tmp_path) == []

    def test_round_trips_entries(self, tmp_path: Path) -> None:
        entries = [{"age": "7d", "frequency": "1h"}, {"age": "30d", "frequency": "1d"}]
        path = save_retention_policy(entries, tmp_path)
        assert path == tmp_path / "config.yaml"
        assert load_retention_policy(tmp_path) == entries

    def test_preserves_other_variables(self, tmp_path: Path) -> None:
        save_config("duplicacy", "/opt/tools/duplicacy", tmp_path)
        save_retention_policy([{"age": "7d", "frequency": "1h"}], tmp_path)
        assert load_config(tmp_path) == {
            "duplicacy": "/opt/tools/duplicacy",
            "retentionPolicy": [{"age": "7d", "frequency": "1h"}],
        }

    def test_rejects_non_list_policy(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("retentionPolicy: 7d\n")
        with pytest.raises(TypeError) as excinfo:
            load_retention_policy(tmp_path)
        # The message names the offending configuration file.
        assert str(tmp_path / "config.yaml") in str(excinfo.value)

    def test_rejects_entry_without_frequency(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 7d\n")
        with pytest.raises(TypeError) as excinfo:
            load_retention_policy(tmp_path)
        assert str(tmp_path / "config.yaml") in str(excinfo.value)

    def test_load_rejects_duplicate_ages(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text(
            "retentionPolicy:\n- age: 7d\n  frequency: 1h\n- age: 1w\n  frequency: 1d\n"
        )
        with pytest.raises(ValueError) as excinfo:
            load_retention_policy(tmp_path)
        assert "ages must be unique" in str(excinfo.value)

    def test_load_rejects_unparsable_age(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 7x\n  frequency: 1h\n")
        with pytest.raises(ValueError):
            load_retention_policy(tmp_path)

    def test_load_rejects_non_positive_age(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 0s\n  frequency: 1h\n")
        with pytest.raises(ValueError):
            load_retention_policy(tmp_path)

    def test_load_rejects_non_positive_frequency(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 7d\n  frequency: 0s\n")
        with pytest.raises(ValueError):
            load_retention_policy(tmp_path)

    def test_save_rejects_duplicate_ages(self, tmp_path: Path) -> None:
        entries = [{"age": "7d", "frequency": "1h"}, {"age": "1w", "frequency": "1d"}]
        with pytest.raises(ValueError) as excinfo:
            save_retention_policy(entries, tmp_path)
        assert "ages must be unique" in str(excinfo.value)
        assert not (tmp_path / "config.yaml").exists()

    def test_save_rejects_non_positive_age(self, tmp_path: Path) -> None:
        entries = [{"age": "0s", "frequency": "1h"}]
        with pytest.raises(ValueError):
            save_retention_policy(entries, tmp_path)
        assert not (tmp_path / "config.yaml").exists()

    @pytest.mark.parametrize("frequency", ["5m", "45m", "1h30m", "5h", "7h", "20h"])
    def test_load_rejects_unsupported_frequencies(self, tmp_path: Path, frequency: str) -> None:
        (tmp_path / "config.yaml").write_text(f"retentionPolicy:\n- age: 7d\n  frequency: {frequency}\n")
        with pytest.raises(ValueError) as excinfo:
            load_retention_policy(tmp_path)
        assert "unsupported retention policy frequency" in str(excinfo.value)

    def test_save_rejects_unsupported_frequency(self, tmp_path: Path) -> None:
        entries = [{"age": "7d", "frequency": "5m"}]
        with pytest.raises(ValueError) as excinfo:
            save_retention_policy(entries, tmp_path)
        assert "unsupported retention policy frequency" in str(excinfo.value)
        assert not (tmp_path / "config.yaml").exists()


class TestRetentionAnchor:
    def test_missing_file_yields_latest_revision_default(self, tmp_path: Path) -> None:
        assert load_retention_anchor(tmp_path) is RetentionAnchor.LATEST_REVISION

    def test_missing_key_yields_latest_revision_default(self, tmp_path: Path) -> None:
        save_config("duplicacy", "/opt/tools/duplicacy", tmp_path)
        assert load_retention_anchor(tmp_path) is RetentionAnchor.LATEST_REVISION

    def test_round_trips_today(self, tmp_path: Path) -> None:
        save_config("retentionAnchor", "today", tmp_path)
        assert load_retention_anchor(tmp_path) is RetentionAnchor.TODAY

    def test_rejects_unknown_value(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("retentionAnchor: noon\n")
        with pytest.raises(ValueError) as excinfo:
            load_retention_anchor(tmp_path)
        # The message names the offending configuration file and the
        # only allowed values.
        assert str(tmp_path / "config.yaml") in str(excinfo.value)
        assert "must be 'latestRevision', 'today'" in str(excinfo.value)

    def test_rejects_unhashable_value(self, tmp_path: Path) -> None:
        # A YAML list is unhashable; the enum lookup rejects it just
        # like an unknown string instead of raising a raw TypeError.
        (tmp_path / "config.yaml").write_text("retentionAnchor: [today]\n")
        with pytest.raises(ValueError) as excinfo:
            load_retention_anchor(tmp_path)
        assert "must be 'latestRevision', 'today' (got ['today'])" in str(excinfo.value)


class TestResolveExecutable:
    def test_explicit_argument_wins(self) -> None:
        assert resolve_executable("Duplicacy.exe") == "Duplicacy.exe"

    def test_env_var_used_when_no_argument(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "/opt/tools/duplicacy")
        assert resolve_executable() == "/opt/tools/duplicacy"

    def test_env_var_wins_over_yaml_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        save_config("duplicacy", "/from/config", tmp_path)
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "/from/env")
        assert resolve_executable(config_dir=tmp_path) == "/from/env"

    def test_default_falls_back_to_path(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "")
        # `python` is guaranteed to be on PATH inside the test venv.
        assert resolve_executable(default=sys.executable, config_dir=tmp_path) == sys.executable

    def test_raises_when_nothing_found(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "")
        monkeypatch.setattr(shutil, "which", lambda name: None)
        with pytest.raises(CliError):
            resolve_executable(default="definitely-not-a-real-binary-xyz", config_dir=tmp_path)

    def test_raises_message_mentions_env_var_and_config(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "")
        monkeypatch.setattr(shutil, "which", lambda name: None)
        with pytest.raises(CliError) as excinfo:
            resolve_executable(default="definitely-not-a-real-binary-xyz", config_dir=tmp_path)
        assert "DUPLICACY_EXECUTABLE" in str(excinfo.value)
        assert "config.yaml" in str(excinfo.value)


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

    def test_raises_when_executable_does_not_exist(self) -> None:
        # Regression test: subprocess.run raises FileNotFoundError before any
        # result exists when the resolved executable path does not exist, and
        # that OSError used to escape as a raw traceback instead of a CliError.
        missing = "/nonexistent/bin/definitely-not-a-real-binary-xyz"
        with pytest.raises(CliError) as excinfo:
            run_cli([missing, "list"])
        assert excinfo.value.returncode is None
        assert excinfo.value.args_list == [missing, "list"]
        assert missing in str(excinfo.value)

    def test_raises_when_executable_is_not_executable(self, tmp_path: Path) -> None:
        # PermissionError is the other common OSError from subprocess.run.
        script = tmp_path / "not-executable.sh"
        script.write_text("#!/bin/sh\ntrue\n")
        script.chmod(0o644)  # readable but no execute bit
        with pytest.raises(CliError) as excinfo:
            run_cli([str(script)])
        assert excinfo.value.returncode is None


class TestLoadEnv:
    def test_loads_env_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("DUPLICACY_EXECUTABLE", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("DUPLICACY_EXECUTABLE=/opt/tools/duplicacy\n")
        load_env(env_file)
        assert resolve_executable() == "/opt/tools/duplicacy"

    def test_env_file_wins_over_yaml_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        save_config("duplicacy", "/from/config", tmp_path)
        monkeypatch.delenv("DUPLICACY_EXECUTABLE", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("DUPLICACY_EXECUTABLE=/from/env/file\n")
        load_env(env_file)
        assert resolve_executable(config_dir=tmp_path) == "/from/env/file"

    def test_real_env_wins_over_env_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "/from/real/env")
        env_file = tmp_path / ".env"
        env_file.write_text("DUPLICACY_EXECUTABLE=/from/env/file\n")
        monkeypatch.chdir(tmp_path)
        load_env()
        assert resolve_executable() == "/from/real/env"

    def test_no_argument_uses_working_directory_env_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Regression test: load_dotenv's implicit find_dotenv() anchors at
        # this module's location, so the installed console script skipped a
        # .env in the working directory and loaded an unrelated one instead.
        monkeypatch.delenv("DUPLICACY_EXECUTABLE", raising=False)
        (tmp_path / ".env").write_text("DUPLICACY_EXECUTABLE=/from/cwd/env\n")
        monkeypatch.chdir(tmp_path)
        load_env()
        assert resolve_executable() == "/from/cwd/env"

    def test_no_argument_walks_up_to_nearest_parent_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DUPLICACY_EXECUTABLE", raising=False)
        (tmp_path / ".env").write_text("DUPLICACY_EXECUTABLE=/from/parent/env\n")
        subdir = tmp_path / "data" / "repo"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)
        load_env()
        assert resolve_executable() == "/from/parent/env"

    def test_no_argument_without_any_env_file_leaves_environment_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DUPLICACY_EXECUTABLE", raising=False)
        monkeypatch.chdir(tmp_path)
        load_env()
        assert os.environ.get("DUPLICACY_EXECUTABLE") is None
