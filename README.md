# duplicacy-scripts

Python scripts for driving the [Duplicacy](https://github.com/gilbertchen/duplicacy) CLI.

## Requirements

- [uv](https://docs.astral.sh/uv/)

## Usage

Run a script with:

```sh
uv run scripts/<script>.py
```

## Adding scripts

Add new scripts to `scripts/`. Common CLI-driving helpers live in
`src/duplicacy_scripts/cli.py`; import them with
`from duplicacy_scripts.cli import ...` (the `src/` layout is on the path when
running via `uv run`).

## Development

```sh
uv sync                  # create/refresh the venv
uv run pytest            # run tests
```

## Notes for Windows

Scripts are written to work on Windows as well as POSIX. Prefer passing the
duplicacy executable explicitly (`--duplicacy Duplicacy.exe` or the
`DUPLICACY` environment variable) rather than relying on `PATH`.