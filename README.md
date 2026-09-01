# duplicacy-scripts

Python scripts for driving the [Duplicacy](https://github.com/gilbertchen/duplicacy) CLI.

## Requirements

- [uv](https://docs.astral.sh/uv/)

## Usage

Run the installed CLI with a command:

```sh
uv run duplicacy-py backup --repository /path/to/repo
uv run duplicacy-py prune --repository /path/to/repo --id <snapshot id>
```

The available commands are:

- `backup` runs `duplicacy backup` in the repository.
- `prune` lists revisions for the supplied snapshot id.

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

The executable location can also be configured in a `.env` file in the working
directory, e.g.:

```
DUPLICACY_EXECUTABLE=C:\Tools\Duplicacy.exe
```

Real environment variables take precedence over `.env` values. `.env` is
gitignored, so keep credentials there rather than committing them.