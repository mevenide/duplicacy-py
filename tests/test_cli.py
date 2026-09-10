"""Tests for the internal CLI helpers in `src/duplicacy_scripts/_cli.py`."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import pytest
import yaml

from duplicacy_scripts._cli import (
    CliError,
    Config,
    ConfigVariable,
    RetentionAnchor,
    default_config_dir,
    load_env,
    run_cli,
    settable_config_variables,
)


class TestConfig:
    def test_default_config_dir_uses_platformdirs(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # chdir away from the checkout and clear the variable so a real
        # .env-configured sandbox cannot shadow the platformdirs fallback.
        monkeypatch.chdir(tmp_path)
        monkeypatch.delenv("DUPLICACY_CONFIG_DIR", raising=False)
        monkeypatch.setattr("duplicacy_scripts._cli.user_config_dir", lambda app_name: str(tmp_path / app_name))
        assert default_config_dir() == tmp_path / "duplicacy-py"

    def test_env_var_points_default_config_dir_at_sandbox(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sandbox = tmp_path / "sandbox-config"
        sandbox.mkdir()
        monkeypatch.setenv("DUPLICACY_CONFIG_DIR", str(sandbox))
        assert default_config_dir() == sandbox

    def test_env_var_expands_user_home(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DUPLICACY_CONFIG_DIR", "~/sandbox-config")
        assert default_config_dir() == Path.home() / "sandbox-config"

    def test_env_var_flows_into_config_file_and_repo(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sandbox = tmp_path / "sandbox-config"
        sandbox.mkdir()
        monkeypatch.setenv("DUPLICACY_CONFIG_DIR", str(sandbox))
        config = Config.load()
        assert config.path == sandbox / "config.yaml"
        assert config.repo_dir() == sandbox / "repo"

    def test_explicit_config_dir_beats_env_var(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        sandbox = tmp_path / "sandbox-config"
        sandbox.mkdir()
        monkeypatch.setenv("DUPLICACY_CONFIG_DIR", str(sandbox))
        explicit = tmp_path / "explicit"
        explicit.mkdir()
        assert Config.load(explicit).path == explicit / "config.yaml"
        assert Config.load(explicit).repo_dir() == explicit / "repo"
        assert Config.load(sandbox).path == sandbox / "config.yaml"

    def test_saves_and_resolves_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "")
        Config.load(tmp_path).save_variable("duplicacy", "/opt/tools/duplicacy")
        assert (tmp_path / "config.yaml").read_text() == "duplicacy: /opt/tools/duplicacy\n"
        assert Config.load(tmp_path).executable() == "/opt/tools/duplicacy"

    def test_initializes_config(self, tmp_path: Path) -> None:
        path = Config.load(tmp_path).init()
        assert path == tmp_path / "config.yaml"
        assert path.exists()
        assert path.read_text() == ""

    def test_init_preserves_existing_config(self, tmp_path: Path) -> None:
        Config.load(tmp_path).save_variable("duplicacy", "/opt/tools/duplicacy")
        assert Config.load(tmp_path).init() == tmp_path / "config.yaml"
        assert (tmp_path / "config.yaml").read_text() == "duplicacy: /opt/tools/duplicacy\n"

    def test_updates_existing_config(self, tmp_path: Path) -> None:
        Config.load(tmp_path).save_variable("pruneMaxRangesPerCommand", "32")
        Config.load(tmp_path).save_variable("duplicacy", "/opt/tools/duplicacy")
        assert (tmp_path / "config.yaml").read_text() == (
            "duplicacy: /opt/tools/duplicacy\npruneMaxRangesPerCommand: 32\n"
        )

    def test_saves_prune_max_ranges_as_int(self, tmp_path: Path) -> None:
        # The read accessor requires an int (bools and strings fail), so the
        # save path coerces and validates the value it stores.
        Config.load(tmp_path).save_variable("pruneMaxRangesPerCommand", "32")
        assert (tmp_path / "config.yaml").read_text() == "pruneMaxRangesPerCommand: 32\n"
        assert Config.load(tmp_path).prune_max_ranges_per_command() == 32

    def test_load_returns_mapping(self, tmp_path: Path) -> None:
        Config.load(tmp_path).save_variable("duplicacy", "/opt/tools/duplicacy")
        assert Config.load(tmp_path).data == {"duplicacy": "/opt/tools/duplicacy"}

    def test_missing_file_yields_empty_mapping(self, tmp_path: Path) -> None:
        config = Config.load(tmp_path)
        assert config.data == {}
        assert config.path == tmp_path / "config.yaml"
        assert config.config_dir == tmp_path

    def test_variables_empty_when_no_config(self, tmp_path: Path) -> None:
        assert Config.load(tmp_path).variables() == {}

    def test_variables_returns_saved_variables(self, tmp_path: Path) -> None:
        config = Config.load(tmp_path)
        config.save_variable("duplicacy", "/opt/tools/duplicacy")
        config.save_variable("pruneMaxRangesPerCommand", "32")
        assert config.variables() == {
            "duplicacy": "/opt/tools/duplicacy",
            "pruneMaxRangesPerCommand": 32,
        }

    def test_variables_excludes_retention_policy(self, tmp_path: Path) -> None:
        config = Config.load(tmp_path)
        config.save_variable("duplicacy", "/opt/tools/duplicacy")
        config.save_retention_policy([{"age": "7d", "frequency": "1h"}])
        assert config.variables() == {"duplicacy": "/opt/tools/duplicacy"}

    def test_variables_excludes_unsupported_variables(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("duplicacy: /opt/tools/duplicacy\nunsupportedKey: foo\n")
        assert Config.load(tmp_path).variables() == {"duplicacy": "/opt/tools/duplicacy"}

    def test_rejects_non_mapping(self, tmp_path: Path) -> None:
        # A file that is not a mapping is a broken configuration file: it
        # surfaces as a CliError (reported by main(), exit code 1) instead
        # of a TypeError each command handler would have to know about.
        (tmp_path / "config.yaml").write_text("- just\n- a\n- list\n")
        with pytest.raises(CliError) as excinfo:
            Config.load(tmp_path)
        # The message names the offending configuration file.
        assert str(tmp_path / "config.yaml") in str(excinfo.value)
        assert "configuration must contain a YAML mapping" in str(excinfo.value)

    def test_rejects_unparsable_yaml(self, tmp_path: Path) -> None:
        # Regression test: unparsable YAML used to escape as a raw
        # yaml.YAMLError traceback; it is a CliError now.
        (tmp_path / "config.yaml").write_text("retentionPolicy: [unclosed\n")
        with pytest.raises(CliError) as excinfo:
            Config.load(tmp_path)
        assert str(tmp_path / "config.yaml") in str(excinfo.value)

    def test_parses_the_file_once_per_load(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Regression test: every accessor used to re-read config.yaml (the
        # executable resolution even had its own yaml call); one load must
        # parse the file exactly once and serve every accessor from memory.
        (tmp_path / "config.yaml").write_text(
            "duplicacy: /opt/tools/duplicacy\nretentionPolicy:\n- age: 7d\n  frequency: 1d\n"
        )
        parse_calls: list[int] = []
        real_safe_load = yaml.safe_load

        def counting_safe_load(stream: object) -> object:
            parse_calls.append(1)
            return real_safe_load(stream)

        monkeypatch.setattr(yaml, "safe_load", counting_safe_load)
        config = Config.load(tmp_path)
        assert config.data["duplicacy"] == "/opt/tools/duplicacy"
        assert config.retention_policy() == [{"age": "7d", "frequency": "1d"}]
        assert config.retention_anchor() is RetentionAnchor.LATEST_REVISION
        assert config.prune_max_ranges_per_command() == 64
        assert len(parse_calls) == 1


class TestRetentionPolicy:
    def test_missing_file_yields_empty_policy(self, tmp_path: Path) -> None:
        assert Config.load(tmp_path).retention_policy() == []

    def test_round_trips_entries(self, tmp_path: Path) -> None:
        entries = [{"age": "7d", "frequency": "1h"}, {"age": "30d", "frequency": "1d"}]
        path = Config.load(tmp_path).save_retention_policy(entries)
        assert path == tmp_path / "config.yaml"
        assert Config.load(tmp_path).retention_policy() == entries

    def test_preserves_other_variables(self, tmp_path: Path) -> None:
        Config.load(tmp_path).save_variable("duplicacy", "/opt/tools/duplicacy")
        Config.load(tmp_path).save_retention_policy([{"age": "7d", "frequency": "1h"}])
        assert Config.load(tmp_path).data == {
            "duplicacy": "/opt/tools/duplicacy",
            "retentionPolicy": [{"age": "7d", "frequency": "1h"}],
        }

    def test_rejects_non_list_policy(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("retentionPolicy: 7d\n")
        with pytest.raises(TypeError) as excinfo:
            Config.load(tmp_path).retention_policy()
        # The message names the offending configuration file.
        assert str(tmp_path / "config.yaml") in str(excinfo.value)

    def test_rejects_entry_without_frequency(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 7d\n")
        with pytest.raises(TypeError) as excinfo:
            Config.load(tmp_path).retention_policy()
        assert str(tmp_path / "config.yaml") in str(excinfo.value)

    def test_load_rejects_duplicate_ages(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text(
            "retentionPolicy:\n- age: 7d\n  frequency: 1h\n- age: 1w\n  frequency: 1d\n"
        )
        with pytest.raises(ValueError) as excinfo:
            Config.load(tmp_path).retention_policy()
        assert "ages must be unique" in str(excinfo.value)

    def test_load_rejects_unparsable_age(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 7x\n  frequency: 1h\n")
        with pytest.raises(ValueError):
            Config.load(tmp_path).retention_policy()

    def test_load_rejects_non_positive_age(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 0s\n  frequency: 1h\n")
        with pytest.raises(ValueError):
            Config.load(tmp_path).retention_policy()

    def test_load_rejects_non_positive_frequency(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("retentionPolicy:\n- age: 7d\n  frequency: 0s\n")
        with pytest.raises(ValueError):
            Config.load(tmp_path).retention_policy()

    def test_save_rejects_duplicate_ages(self, tmp_path: Path) -> None:
        entries = [{"age": "7d", "frequency": "1h"}, {"age": "1w", "frequency": "1d"}]
        with pytest.raises(ValueError) as excinfo:
            Config.load(tmp_path).save_retention_policy(entries)
        assert "ages must be unique" in str(excinfo.value)
        assert not (tmp_path / "config.yaml").exists()

    def test_save_rejects_non_positive_age(self, tmp_path: Path) -> None:
        entries = [{"age": "0s", "frequency": "1h"}]
        with pytest.raises(ValueError):
            Config.load(tmp_path).save_retention_policy(entries)
        assert not (tmp_path / "config.yaml").exists()

    @pytest.mark.parametrize("frequency", ["5m", "45m", "1h30m", "5h", "7h", "20h"])
    def test_load_rejects_unsupported_frequencies(self, tmp_path: Path, frequency: str) -> None:
        (tmp_path / "config.yaml").write_text(f"retentionPolicy:\n- age: 7d\n  frequency: {frequency}\n")
        with pytest.raises(ValueError) as excinfo:
            Config.load(tmp_path).retention_policy()
        assert "unsupported retention policy frequency" in str(excinfo.value)

    def test_save_rejects_unsupported_frequency(self, tmp_path: Path) -> None:
        entries = [{"age": "7d", "frequency": "5m"}]
        with pytest.raises(ValueError) as excinfo:
            Config.load(tmp_path).save_retention_policy(entries)
        assert "unsupported retention policy frequency" in str(excinfo.value)
        assert not (tmp_path / "config.yaml").exists()


class TestRetentionAnchor:
    def test_missing_file_yields_latest_revision_default(self, tmp_path: Path) -> None:
        assert Config.load(tmp_path).retention_anchor() is RetentionAnchor.LATEST_REVISION

    def test_missing_key_yields_latest_revision_default(self, tmp_path: Path) -> None:
        Config.load(tmp_path).save_variable("duplicacy", "/opt/tools/duplicacy")
        assert Config.load(tmp_path).retention_anchor() is RetentionAnchor.LATEST_REVISION

    def test_round_trips_today(self, tmp_path: Path) -> None:
        Config.load(tmp_path).save_variable("retentionAnchor", "today")
        assert Config.load(tmp_path).retention_anchor() is RetentionAnchor.TODAY

    def test_rejects_unknown_value(self, tmp_path: Path) -> None:
        (tmp_path / "config.yaml").write_text("retentionAnchor: noon\n")
        with pytest.raises(ValueError) as excinfo:
            Config.load(tmp_path).retention_anchor()
        # The message names the offending configuration file and the
        # only allowed values.
        assert str(tmp_path / "config.yaml") in str(excinfo.value)
        assert "must be 'latestRevision', 'today'" in str(excinfo.value)

    def test_rejects_unhashable_value(self, tmp_path: Path) -> None:
        # A YAML list is unhashable; the enum lookup rejects it just
        # like an unknown string instead of raising a raw TypeError.
        (tmp_path / "config.yaml").write_text("retentionAnchor: [today]\n")
        with pytest.raises(ValueError) as excinfo:
            Config.load(tmp_path).retention_anchor()
        assert "must be 'latestRevision', 'today' (got ['today'])" in str(excinfo.value)


class TestConfigExecutable:
    def test_explicit_argument_wins(self, tmp_path: Path) -> None:
        assert Config.load(tmp_path).executable("Duplicacy.exe") == "Duplicacy.exe"

    def test_env_var_used_when_no_argument(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "/opt/tools/duplicacy")
        assert Config.load(tmp_path).executable() == "/opt/tools/duplicacy"

    def test_env_var_wins_over_yaml_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        Config.load(tmp_path).save_variable("duplicacy", "/from/config")
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "/from/env")
        assert Config.load(tmp_path).executable() == "/from/env"

    def test_default_falls_back_to_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "")
        # `python` is guaranteed to be on PATH inside the test venv.
        assert Config.load(tmp_path).executable(default=sys.executable) == sys.executable

    def test_raises_when_nothing_found(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "")
        monkeypatch.setattr(shutil, "which", lambda name: None)
        with pytest.raises(CliError):
            Config.load(tmp_path).executable(default="definitely-not-a-real-binary-xyz")

    def test_raises_message_mentions_env_var_and_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "")
        monkeypatch.setattr(shutil, "which", lambda name: None)
        with pytest.raises(CliError) as excinfo:
            Config.load(tmp_path).executable(default="definitely-not-a-real-binary-xyz")
        assert "DUPLICACY_EXECUTABLE" in str(excinfo.value)
        assert "config.yaml" in str(excinfo.value)

    def test_raises_message_shows_unset_variable_values(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # The error must show what the config variables actually hold, so a
        # .env that was never loaded is visible at a glance.
        monkeypatch.delenv("DUPLICACY_EXECUTABLE", raising=False)
        monkeypatch.setattr(shutil, "which", lambda name: None)
        with pytest.raises(CliError) as excinfo:
            Config.load(tmp_path).executable(default="definitely-not-a-real-binary-xyz")
        assert "DUPLICACY_EXECUTABLE is currently None" in str(excinfo.value)
        assert "config.yaml 'duplicacy' is currently None" in str(excinfo.value)

    def test_raises_message_shows_empty_variable_values(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # An empty value does not count as configured either; the message
        # shows it so an empty .env or config.yaml entry is not mistaken
        # for a working setting.
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "")
        (tmp_path / "config.yaml").write_text("duplicacy: ''\n")
        monkeypatch.setattr(shutil, "which", lambda name: None)
        with pytest.raises(CliError) as excinfo:
            Config.load(tmp_path).executable(default="definitely-not-a-real-binary-xyz")
        assert "DUPLICACY_EXECUTABLE is currently ''" in str(excinfo.value)
        assert "config.yaml 'duplicacy' is currently ''" in str(excinfo.value)


class TestConfigVariable:
    def test_members_are_the_config_yaml_key_names(self) -> None:
        # The enum values are the YAML keys; the docstring of
        # ConfigVariable promises these are the only recognized names.
        assert [member.value for member in ConfigVariable] == [
            "duplicacy",
            "retentionAnchor",
            "retentionPolicy",
            "pruneMaxRangesPerCommand",
        ]

    def test_members_are_strings(self) -> None:
        # StrEnum members must work as `data` mapping keys on reads.
        assert ConfigVariable.DUPLICACY == "duplicacy"

    def test_settable_variables_exclude_retention_policy(self) -> None:
        # `config var` cannot save the retention policy; that is what
        # `config retention-policy add/remove` is for. The listed names are
        # exactly what save_variable accepts.
        assert settable_config_variables() == (
            "duplicacy, retentionAnchor, pruneMaxRangesPerCommand"
        )

    def test_save_rejects_unknown_name(self, tmp_path: Path) -> None:
        # A typo (e.g. duplicaty) used to be saved and silently ignored
        # by every later run; it is rejected now, listing the supported
        # variables.
        with pytest.raises(ValueError) as excinfo:
            Config.load(tmp_path).save_variable("duplicaty", "/opt/tools/duplicacy")
        assert "unsupported configuration variable 'duplicaty'" in str(excinfo.value)
        assert "duplicacy, retentionAnchor, pruneMaxRangesPerCommand" in str(excinfo.value)
        assert not (tmp_path / "config.yaml").exists()

    def test_save_rejects_retention_policy(self, tmp_path: Path) -> None:
        # The retention policy has its own validated management commands;
        # saving it with `config var` would bypass the validation.
        with pytest.raises(ValueError) as excinfo:
            Config.load(tmp_path).save_variable("retentionPolicy", "value")
        assert "managed by 'config retention-policy add'" in str(excinfo.value)
        assert not (tmp_path / "config.yaml").exists()

    def test_save_rejects_non_positive_prune_max_ranges(self, tmp_path: Path) -> None:
        entries = ("0", "-1", "x", "1.5")
        for value in entries:
            with pytest.raises(ValueError) as excinfo:
                Config.load(tmp_path).save_variable("pruneMaxRangesPerCommand", value)
            assert "must be a positive integer" in str(excinfo.value)
            assert not (tmp_path / "config.yaml").exists()

    def test_save_round_trips_retention_anchor(self, tmp_path: Path) -> None:
        Config.load(tmp_path).save_variable("retentionAnchor", "today")
        assert Config.load(tmp_path).retention_anchor() is RetentionAnchor.TODAY


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
        assert Config.load(tmp_path).executable() == "/opt/tools/duplicacy"

    def test_env_file_wins_over_yaml_config(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        Config.load(tmp_path).save_variable("duplicacy", "/from/config")
        monkeypatch.delenv("DUPLICACY_EXECUTABLE", raising=False)
        env_file = tmp_path / ".env"
        env_file.write_text("DUPLICACY_EXECUTABLE=/from/env/file\n")
        load_env(env_file)
        assert Config.load(tmp_path).executable() == "/from/env/file"

    def test_real_env_wins_over_env_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DUPLICACY_EXECUTABLE", "/from/real/env")
        env_file = tmp_path / ".env"
        env_file.write_text("DUPLICACY_EXECUTABLE=/from/env/file\n")
        monkeypatch.chdir(tmp_path)
        load_env()
        assert Config.load(tmp_path).executable() == "/from/real/env"

    def test_no_argument_uses_working_directory_env_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        # Regression test: load_dotenv's implicit find_dotenv() anchors at
        # this module's location, so the installed console script skipped a
        # .env in the working directory and loaded an unrelated one instead.
        monkeypatch.delenv("DUPLICACY_EXECUTABLE", raising=False)
        (tmp_path / ".env").write_text("DUPLICACY_EXECUTABLE=/from/cwd/env\n")
        monkeypatch.chdir(tmp_path)
        load_env()
        assert Config.load(tmp_path).executable() == "/from/cwd/env"

    def test_no_argument_walks_up_to_nearest_parent_env_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DUPLICACY_EXECUTABLE", raising=False)
        (tmp_path / ".env").write_text("DUPLICACY_EXECUTABLE=/from/parent/env\n")
        subdir = tmp_path / "data" / "repo"
        subdir.mkdir(parents=True)
        monkeypatch.chdir(subdir)
        load_env()
        assert Config.load(tmp_path).executable() == "/from/parent/env"

    def test_no_argument_without_any_env_file_leaves_environment_alone(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("DUPLICACY_EXECUTABLE", raising=False)
        monkeypatch.chdir(tmp_path)
        load_env()
        assert os.environ.get("DUPLICACY_EXECUTABLE") is None
