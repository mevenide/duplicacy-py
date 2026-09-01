# AGENTS.md

Guidance for AI agents (and humans) working in this repository.

## What this repo is

Python code that drives the [Duplicacy](https://github.com/gilbertchen/duplicacy) CLI.
It is a **uv-based, `src/`-layout** project: the common CLI and shared helpers
are in `src/duplicacy_scripts/`, with tests in `tests/`.

- Python `>= 3.12`; runtime dependency is `python-dotenv`; dev dependency is
  `pytest>=8.3`.
- Python 3.12.3 is installed system-wide; `uv` is at `~/.local/bin/uv`.
- The configured Duplicacy executable is at `${HOME}/.local/bin/duplicacy_linux_x64_3.2.5`.
- **The `duplicacy` executable is NOT installed on this machine.** To exercise a
  script end-to-end, create a stub shell script that echoes plausible output and
  point the script at it via `DUPLICACY_EXECUTABLE` or a `.env` file (the stub
  receives the CLI args as `$*`; its stdout is what the script prints).

## Commands

```sh
uv sync              # create/refresh the venv (run from repo root)
uv run pytest -q     # run tests
uv run duplicacy-py <command>  # run a command
```

`uv` commands must be run from the repo root — there is no `pyproject.toml`
above it. Tests cover `src/duplicacy_scripts/cli.py` and the common CLI.

## Conventions (follow existing code)

- Python modules start with a module docstring explaining purpose, then
  `from __future__ import annotations`, then imports.
- The common CLI entry point is `src/duplicacy_scripts/main.py`, exposed as
  `duplicacy-py` through the `[project.scripts]` table in `pyproject.toml`; it
  uses `argparse` subcommands with `main(argv: list[str] | None = None) -> int`
  and `if __name__ == "__main__": sys.exit(main())`.
- Subprocesses are only invoked through `duplicacy_scripts.cli.run_cli` with a
  list of args (never a shell), so paths with spaces work on Windows and POSIX.
- A non-zero CLI exit raises `CliError`; scripts catch it, print to stderr,
  and return exit code 1.
- Executable resolution precedence: `load_env()` (a real `DUPLICACY_EXECUTABLE`
  env var, else the value from the working-directory `.env` file), then the
  `duplicacy` key in `config.yaml`, then `'duplicacy'` on PATH (fails with
  `CliError` if none found).
- `duplicacy_scripts.cli.load_env()` loads a `.env` file (cwd by default,
  `override=False`); `resolve_executable` calls it before reading the env var,
  so `DUPLICACY_EXECUTABLE=/path/to/duplicacy` in a `.env` file works. `.env` is
  gitignored and must never hold committed secrets.
- `uv add <pkg>` updates both `pyproject.toml` and `uv.lock` in one step.
- Tests use plain `pytest` classes (`TestX`), `monkeypatch`/`capsys` fixtures.
  To stub the common CLI's CLI call, monkeypatch `run_cli` (and
  `resolve_executable` if the test would otherwise fail on a missing binary) on
  the module under test, since it binds them at import time.

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
  `cwd=repo_dir(config_dir)` (the `repo` subdirectory of the configuration
  directory) to `run_cli`.
- Source files worth consulting (fetch raw from GitHub `master` branch):
  - `duplicacy/duplicacy_main.go` — all commands and their flags
    (`listSnapshots`, `pruneSnapshots`, `getRevisions` accept `N` and `N-M`
    revision ranges).
  - `src/duplicacy_snapshotmanager.go` — `ListSnapshotIDs`,
    `ListSnapshotRevisions`, `ListSnapshots`, `PruneSnapshots`.