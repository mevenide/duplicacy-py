# Code review: the common CLI (`duplicacy-py`)

Reviewed: 2026-09-03, commit `63ab15b` (main).
Scope: `src/duplicacy_scripts/` (`main.py`, `_cli.py`, `duration.py`, `retention.py`,
`commands/{backup,list,prune,config}.py`), `tests/`, `pyproject.toml`, `README.md`.

## Verdict

The codebase is in good shape. It follows its own documented conventions exactly
(one module per command, `_cli.run_cli` for every subprocess, list args never a
shell, `CliError` -> exit code 1), the module docstrings are accurate and
unusually detailed, and the test suite is strong (172 tests pass in ~0.6s).
Findings below are ranked; F1 is a real, reproducible behavior bug, F2 is a
robustness gap worth fixing, the rest are minor or informational. No blocking
issues. ✅ F1 has since been fixed (see its Resolved note).

## Strengths

- **Subprocess hygiene.** All subprocess calls go through `_cli.run_cli` with a
  list of args and `capture_output=True`; no shell interpolation anywhere, so
  paths with spaces behave on Windows and POSIX.
- **Error path design.** `CliError` carries `args_list`/`returncode`/`output`;
  `main()` is the single choke point that converts it to exit code 1 with the
  message on stderr. Verified end to end: a failing `duplicacy list` prints the
  message and exits 1.
- **Validation before side effects.** Retention policies are fully validated
  (positive/unique/parsable ages, supported frequencies) on load *and* save, so
  an invalid policy never reaches the file and never triggers a `duplicacy` run
  (verified: `prune` with a bad policy fails before the CLI subprocess).
- **Parser layering.** `main.py` only assembles/dispatches; each command module
  exposes `add_parser`/`run` and is registered in the `COMMANDS` table — adding
  a command is a two-line change.
- **Tests.** Command modules are stubbed at the right seam (on
  `duplicacy_scripts._cli`, picker on `commands.prune`), the retention grid has
  dedicated boundary/tie tests, and the `FixedDateTime` trick verifies the
  midnight anchoring.

## Findings

### ✅ F1. Working-directory `.env` is not loaded by the installed console script (High) — RESOLVED

`_cli.load_env()` calls `load_dotenv(None)`. When no path is given,
python-dotenv's `find_dotenv()` walks up **from the caller's file location**
(unless `usecwd=True`, and only falling back to cwd when interactive/debugging).
Because the console script imports `_cli.py` from this repo's `src/` tree, the
"working-directory `.env`" in the README is actually discovered relative to the
package location — not the user's cwd.

Reproduced (venv script run from a scratch cwd containing `.env` with
`DUPLICACY_EXECUTABLE=/bin/false`, `--config` pointing elsewhere):

- The cwd `.env` was **ignored**; resolution fell through to the repo-root
  `.env` and ran the real binary
  (`/home/chrisng/.local/bin/duplicacy_linux_x64_3.2.5 ... exit code 100`).

Consequences: a user's per-project `.env` next to their data can be silently
skipped, while an unrelated `.env` (this repo's own, or one higher up in the
package path) wins. That contradicts the README ("a `.env` file in the working
directory") and the AGENTS.md contract for `load_env()`.

Resolved 2026-09-03: `load_env()` now calls `find_dotenv(usecwd=True)` and
passes the resolved path to `load_dotenv`. The originally suggested one-liner
(`load_dotenv(env_file, override=False, usecwd=True)`) was not possible as
written: `load_dotenv()` has no `usecwd` parameter and calls `find_dotenv()`
internally with its defaults, so the explicit `find_dotenv` call is required.
Discovery now anchors at the working directory and walks up
to the nearest parent `.env` (an explicit path argument is still forwarded
unchanged), and the README documents the nearest-parent walk-up.

Guarded by three regression tests in `tests/test_cli.py` (`TestLoadEnv`) that
exercise the real, unstubbed `load_env()` — per the note above, stubbing at
`_cli.load_env` cannot catch this class of bug: the cwd `.env` is used, the
walk-up finds the nearest parent's `.env`, and no `.env` anywhere leaves the
environment untouched. Verified end to end with the console script: from a
scratch cwd whose `.env` points at `/bin/false`, `duplicacy-py list` now
fails with `/bin/false failed with exit code 1` (previously it ignored the
cwd `.env` and ran the real binary); walk-up from a nested subdirectory
works; and `uv run duplicacy-py list` from the repo root still resolves the
real binary. Full suite: 175 passed.

### F2. `FileNotFoundError` from `subprocess.run` escapes as a traceback (High)

`_cli.run_cli` handles non-zero exits via `CliError`, but if the resolved
executable path does not exist, `subprocess.run` raises `FileNotFoundError`
before any result exists. Reproduced:

```
$ DUPLICACY_EXECUTABLE=/nonexistent/bin uv run duplicacy-py list
Traceback (most recent call last):
  ...
  File "/usr/lib/python3.12/subprocess.py", line 1026, in _execute_child ...
FileNotFoundError: [Errno 2] No such file or directory: '/nonexistent/bin'
```

Every user-facing path (`resolve_executable` accepts any string: env var,
config.yaml `duplicacy` key, PATH lookup) can yield a stale/broken path, so
this is reachable in normal use. `main()` catches only `CliError`, so the user
gets a raw traceback and the process exits via the Python default instead of
the documented "exit code 1 with stderr message".

Suggested fix: catch `OSError` in `run_cli` and re-raise as
`CliError(args, None, str(exc))` (exit code `None` already has precedent in
`CliError` for "could not run" cases). Alternatively catch
`(CliError, OSError)` in `main()` — but wrapping in `run_cli` keeps the
invariant "any CLI invocation failure is a `CliError`".

### F3. `prune` never prints the duplicacy stderr banner (Low)

Upstream `duplicacy` logs diagnostics like `Storage set to ...` to **stderr**;
`_cli.run_cli` captures stdout and stderr separately, and `prune` prints only
`result.stdout`. This is by design for parsing, but as a user-facing command it
means `prune` shows no storage context, while `backup`/`run_and_print` (also
stdout-only, but backed by live streaming) and raw CLI runs do. Harmless today;
worth a one-line note in the README, or forward captured stderr when the
command finishes (e.g. print it after parsing) if users ever ask where their
storage went.

### F4. `load_retention_policy` maps a non-list policy to `TypeError` with a generic message (Low)

`_cli.load_retention_policy` raises `TypeError` for a non-list
`retentionPolicy`, but the error message ("retentionPolicy must contain a list
of {age, frequency} mappings") does not tell the user *where* the offending
file is. Compare `load_config` ("configuration must contain a YAML mapping"),
which also lacks the path. Since all command handlers print these exceptions
verbatim, including the config path in the message would make misconfiguration
self-explanatory:

```python
raise TypeError(f"{path}: retentionPolicy must contain a list of {{age, frequency}} mappings")
```

### F5. `config var` accepts empty-ish names/values inconsistently (Low)

`_run_var` rejects `NAME=` and `=VALUE` and a missing `=`, but a name of
`" "` (whitespace) or a value containing newlines is accepted and written to
YAML. Values are safe (YAML quoting handles them), but a whitespace name
creates keys that can never be resolved deliberately. A `.strip()`-based check
on the name (and maybe rejecting newlines in the name) would close it. Cosmetic
severity; the current behavior never corrupts the file.

### F6. `revision` timestamps are parsed as naive local times (Informational)

`_cli.revisions` parses `duplicacy list` output with
`datetime.strptime(..., "%Y-%m-%d %H:%M")` — naive local time. The whole
`retention.py` design correctly documents and requires this (midnight-anchored
buckets), and the real CLI output confirmed the format matches. This is only a
future-portability note: if `duplicacy` ever emits timezone-aware or UTC
timestamps, the retention math shifts. No action needed now; the docstring in
`retention.py` already captures the assumption.

### F7. Type-checking imports and minor polish (Informational)

- `retention.py` guards the `_cli.Revision` import under `TYPE_CHECKING` — good
  — but `select_revisions` accepts any duck-typed revision (tests pass
  `SimpleNamespace`); the runtime contract is only implied. Fine as-is.
- `commands/__init__.py` types `COMMANDS` as
  `dict[str, tuple[Callable, Callable]]`; a `tuple[Callable[[argparse.Namespace], int], ...]`-style
  annotation would be slightly tighter, but there is no type checker configured
  in the project (no ruff/mypy config), so this is cosmetic.
- No linters/type checkers are configured at all. Adding `ruff` (and optionally
  `pyright`) to the dev group with the existing conventions would catch the
  F5-class issues mechanically.

## Verified behavior (evidence)

- `uv run pytest -q` → **172 passed** in 0.58s (Python 3.12.3, no warnings).
- Real end-to-end runs against the configured storage
  (`~/.config/duplicacy-py/repo`, snapshot `Jenna3_user_savegames`):
  - `uv run duplicacy-py list` → prints `Jenna3_user_savegames`, exit 0.
  - `uv run duplicacy-py prune --snapshot-id Jenna3_user_savegames` → prints
    bucket headers and kept/pruned marks correctly (15-minute grid visible:
    revision 1 kept, 2–4 pruned, 5 kept, ...), exit 0.
- Raw `duplicacy list -all` confirmed stdout/stderr split (snapshot lines on
  stdout, `Storage set to ...` on stderr) — matches the parsing design.
- `resolve_executable` precedence and failure modes exercised: env var →
  config.yaml `duplicacy` key → PATH; missing binary surfaces as `CliError`
  when the path exists but the repo is uninitialized (exit 100 path), and as
  the F2 traceback when the path does not exist at all.
- Retention edge probes: `7d` vs `1w` duplicate-age rejection, unsupported
  frequencies (`5m`, `5h`, `90m`, `36h` rejected; `48h`, `72h`, `1y`,
  `0.5h→30m` accepted), boundary-tick dedup between adjacent buckets behaved as
  documented in all probes.

## Suggestions (non-blocking, ordered)

1. F1 was fixed on 2026-09-03 (see its Resolved note). Remaining: F2
   (`OSError` → `CliError` in `run_cli`), which is small and testable.
2. Optionally forward captured stderr in `prune` (F3) or document the omission.
3. Add `ruff` to the dev dependencies and a minimal config; the codebase is
   already consistent enough that it will pass with few suppressions.
4. Consider a short "Troubleshooting" section in the README covering the F2
   failure mode ("resolved executable does not exist") since it is the most
   likely first-run failure for new users.