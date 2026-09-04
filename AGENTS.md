# AGENTS.md

Guidance for AI agents (and humans) working in this repository.

## What this repo is

Python code that drives the [Duplicacy](https://github.com/gilbertchen/duplicacy) CLI.
It is a **uv-based, `src/`-layout** project: the common CLI and shared helpers
are in `src/duplicacy_scripts/`, with tests in `tests/`.

- Python `>= 3.12`; runtime dependency is `python-dotenv`; dev dependency is
  `pytest>=8.3`.
- Python 3.12.3 is installed system-wide; `uv` is at `~/.local/bin/uv`.
- The real Duplicacy executable IS installed on this machine at
  `${HOME}/.local/bin/duplicacy_linux_x64_3.2.5`. It is not on PATH as
  `duplicacy`; it is resolved via the repo's gitignored `.env` file, which
  contains `DUPLICACY_EXECUTABLE=${HOME}/.local/bin/duplicacy_linux_x64_3.2.5`.
- Real test storage for end-to-end runs: `${HOME}/tmp/duplicacy-savegames.storage/`,
  configured in `sandbox-config/repo/.duplicacy/preferences` (snapshot
  id `duplicacy-py-dummy`). `uv run duplicacy-py prune --dry-run --snapshot-id
  Jenna3_user_savegames` from the repo root exercises the full path for real
  (--dry-run only prints the duplicacy prune command(s) that would delete
  the pruned revisions, to stderr — one command with one `-r <start-end>`
  per consecutive revision run, capped by the `pruneMaxRangesPerCommand`
  config key, default 64; --analyze only prints the bucketed kept/pruned
  listing; with no flag those commands actually run).
- If a stub is still needed, create a shell script that echoes plausible output
  and point the script at it via `DUPLICACY_EXECUTABLE` or a `.env` file (the
  stub receives the CLI args as `$*`; its stdout is what the script prints).
- Dev sandbox configuration: `sandbox-config/` in the repo root is a copy of
  the user config at `~/.config/duplicacy-py` (`config.yaml` plus the
  `repo/.duplicacy/preferences` repository definition and the duplicacy
  cache). The gitignored `.env` file also sets
  `DUPLICACY_CONFIG_DIR=${HOME}/git/duplicacy-scripts/sandbox-config`, which
  `duplicacy_scripts._cli.default_config_dir()` honors when no `--config` is
  given, so development runs inside this checkout always use the sandbox and
  never touch the user's config. Only pass `--config` explicitly when a
  different directory is genuinely needed. The sandbox is gitignored
  (regenerable from the user config; copy it with
  `cp -a ~/.config/duplicacy-py/. sandbox-config/`).

## Commands

```sh
uv sync              # create/refresh the venv (run from repo root)
uv run pytest -q     # run tests
uv run duplicacy-py <command>  # run a command
```

`uv` commands must be run from the repo root — there is no `pyproject.toml`
above it. Tests cover `src/duplicacy_scripts/_cli.py` and the common CLI.

## Conventions (follow existing code)

- Python modules start with a module docstring explaining purpose, then
  `from __future__ import annotations`, then imports.
- The common CLI entry point is `src/duplicacy_scripts/main.py`, exposed as
  `duplicacy-py` through the `[project.scripts]` table in `pyproject.toml`; it
  only assembles the parser, dispatches, and handles `CliError` (printing to
  stderr and returning exit code 1). Each subcommand lives in its own internal
  module under `src/duplicacy_scripts/commands/` (`backup.py`, `list.py`,
  `prune.py`, `config.py`) and must expose `add_parser(subparsers)` and
  `run(args) -> int`; new commands are registered in that package's `COMMANDS`
  table in `src/duplicacy_scripts/commands/__init__.py`. Command modules call
  shared helpers through the internal `duplicacy_scripts._cli` module (e.g.
  `_cli.run_cli(...)`), never via `from ... import run_cli`.
- Subprocesses are only invoked through `duplicacy_scripts._cli.run_cli` with a
  list of args (never a shell), so paths with spaces work on Windows and POSIX.
- A non-zero CLI exit raises `CliError`; scripts catch it, print to stderr,
  and return exit code 1.
- Configuration is loaded **once per command run** into
  `duplicacy_scripts._cli.Config` (`_cli.Config.load(args.config)`), a dataclass
  holding the resolved `config_dir`, the `config.yaml` path, and the parsed
  mapping. Its accessors (`executable`, `retention_policy`,
  `retention_anchor`, `prune_max_ranges_per_command`) read that mapping; write
  helpers (`save_variable`, `save_retention_policy`, `init`) update it and dump
  it back. A malformed `config.yaml` raises `CliError` at load time. Do not
  re-read the config file inside a command — add a `Config` accessor instead.
- Executable resolution precedence: `load_env()` (a real `DUPLICACY_EXECUTABLE`
  env var, else the value from the working-directory `.env` file), then the
  `duplicacy` key in the loaded `Config`, then `'duplicacy'` on PATH (fails with
  `CliError` if none found).
- `duplicacy_scripts._cli.load_env()` loads a `.env` file (cwd by default,
  `override=False`); `Config.executable()` calls it before reading the env var,
  so `DUPLICACY_EXECUTABLE=/path/to/duplicacy` in a `.env` file works. `.env` is
  gitignored and must never hold committed secrets.
- `uv add <pkg>` updates both `pyproject.toml` and `uv.lock` in one step.
- Tests use plain `pytest` classes (`TestX`), `monkeypatch`/`capsys` fixtures.
  To stub the common CLI's CLI call, monkeypatch `run_cli` **on the
  `duplicacy_scripts._cli` module**; if the test would otherwise fail on a
  missing binary, stub `Config.executable` on the `duplicacy_scripts._cli`
  module (`_cli.Config`). Stub the interactive prune picker (`select_option`)
  on `duplicacy_scripts.commands.prune`.

## Duplicacy CLI notes (verified against upstream source)

Upstream repo: `github.com/gilbertchen/duplicacy`. The CLI entry point is
`duplicacy/duplicacy_main.go`; storage/snapshot logic is in
`src/duplicacy_snapshotmanager.go`. Useful facts learned while writing the
prune script:

- `duplicacy list -id <snapshot id>` lists that snapshot's revisions, one line
  per revision (`Snapshot <id> revision <n> created at <YYYY-MM-DD HH:MM> ...`).
  Add `-all` for any id, `-r <n>` to filter revisions, `-t <tag>` by tag,
  `-files`/`-chunks` for per-snapshot detail.
- Revisions on the storage are numbered files under `snapshots/<id>/<n>`;
  `ListSnapshotRevisions` parses filenames as integers and sorts them.
- The `duplicacy` CLI always logs to **stderr** (e.g. `Storage set to ...`);
  snapshot lines go to stdout. Any script that parses `list` output should
  capture stdout only, or split on stderr.
- `prune` accepts the same id selection flags (`-all` / `-id <id>` / `-r <n>`)
  plus `-keep <interval>`, `-exclusive` (mandatory for storages that cannot
  move files), `-dry-run`, `-delete-only`, `-collect-only`, `-ignore <id>`,
  `-exhaustive`. Fossils from interrupted prunes are collected before deletion;
  a prune that crashes mid-way leaves `fossils`/`caches` to clean up.
- Repository discovery: the CLI walks up from the cwd until it finds a
  `.duplicacy` directory, then loads preferences from it; commands pass
  `cwd=config.repo_dir()` (the `repo` subdirectory of the configuration
  directory) to `run_cli`.
- Source files worth consulting (fetch raw from GitHub `master` branch):
  - `duplicacy/duplicacy_main.go` — all commands and their flags
    (`listSnapshots`, `pruneSnapshots`, `getRevisions` accept `N` and `N-M`
    revision ranges).
  - `src/duplicacy_snapshotmanager.go` — `ListSnapshotIDs`,
    `ListSnapshotRevisions`, `ListSnapshots`, `PruneSnapshots`.