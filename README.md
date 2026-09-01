# duplicacy-scripts

Python scripts for driving the [Duplicacy](https://github.com/gilbertchen/duplicacy) CLI.

## Requirements

- [uv](https://docs.astral.sh/uv/)

## Usage

Run the installed CLI with a command:

```sh
uv run duplicacy-py backup --repository /path/to/repo
uv run duplicacy-py prune --repository /path/to/repo --id <snapshot id>
uv run duplicacy-py config init --storage <storage url>
uv run duplicacy-py config var duplicacy=/path/to/duplicacy
```

The available commands are:

- `backup` runs `duplicacy backup` in the repository.
- `prune` lists revisions for the supplied snapshot id.
- `config init --storage <storage url>` creates the configuration file and
  initializes a duplicacy repository in the `repo` subdirectory of the
  configuration directory (snapshot id: `duplicacy-py-dummy`). An existing
  configuration file is reported and left untouched, and an already
  initialized `repo` directory is left untouched as well.
- `config var NAME=VALUE` saves a named configuration variable for future commands.

Configuration is stored as `config.yaml` in the per-user configuration directory.
Use `--config /path/to/config-dir` on any command to select a different directory.
The default follows the platform conventions provided by the `platformdirs`
package: `~/.config/duplicacy-py` on Linux, `%APPDATA%\\duplicacy-py` on
Windows, and `~/Library/Application Support/duplicacy-py` on macOS. Linux also
honors `XDG_CONFIG_HOME` when it is set.

The console command is declared in `pyproject.toml` and points to the
`duplicacy_scripts.main:main` entry point. The implementation lives in
`src/duplicacy_scripts/main.py`, with shared CLI-driving helpers in
`src/duplicacy_scripts/cli.py`.

## Development

```sh
uv sync                  # create/refresh the venv
uv run pytest            # run tests
```

## Notes for Windows

Scripts are written to work on Windows as well as POSIX. Prefer passing the
duplicacy executable explicitly (`--duplicacy Duplicacy.exe` or the
`DUPLICACY_EXECUTABLE` environment variable) rather than relying on `PATH`.

The executable location can also be configured in `config.yaml`, e.g.:

```
duplicacy: C:\\Tools\\Duplicacy.exe
```

Real environment variables take precedence over `.env` values. `.env` is
gitignored, so keep credentials there rather than committing them.