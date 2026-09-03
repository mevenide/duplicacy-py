# duplicacy-scripts

Python scripts for driving the [Duplicacy](https://github.com/gilbertchen/duplicacy) CLI.

## Requirements

- [uv](https://docs.astral.sh/uv/)

## Usage

Run the installed CLI with a command:

```sh
uv run duplicacy-py backup
uv run duplicacy-py list
uv run duplicacy-py prune --snapshot-id <snapshot id>
uv run duplicacy-py config init --storage <storage url>
uv run duplicacy-py config var duplicacy=/path/to/duplicacy
uv run duplicacy-py config retention-policy add --age 7d --frequency 1h
```

The available commands are:

- `backup` runs `duplicacy backup` in the `repo` subdirectory of the
  configuration directory.
- `list` prints the snapshot ids found in the repository (one per line,
  sorted, duplicates removed).
- `prune [--snapshot-id <snapshot id>]` lists the revisions for the supplied
  snapshot id, parsed from the `duplicacy list` output, running in the `repo`
  subdirectory of the configuration directory. When the `retentionPolicy`
  configuration key contains ages, the revisions are grouped under retention
  buckets (see below): one chronological header per bucket,
  `Bucket <index>: [<start>, <end>)` (ends exclusive; `the beginning` and
  `now` for the unbounded outermost buckets), with the revisions of that
  bucket printed as `<revision> created at <YYYY-MM-DD HH:MM> kept|pruned`
  underneath: the retention policy selects one revision per frequency
  timestamp (marked `kept`) and the rest are marked `pruned`; empty buckets
  print only their header. Without a retention policy, the revisions are
  printed as before (one per line, sorted by revision number). An invalid
  retention policy (unparsable or non-positive age, duplicate ages, or an
  unparsable, non-positive, or unsupported frequency) exits with an error
  before the duplicacy CLI runs. When
  `--snapshot-id` is omitted and stdin is
  interactive, an up/down arrow picker (with Enter to select) offers the
  snapshot ids found in the repository; when stdin is not interactive, it
  lists the available ids and exits with an error.
- `config init --storage <storage url>` creates the configuration file and
  initializes a duplicacy repository in the `repo` subdirectory of the
  configuration directory (snapshot id: `duplicacy-py-dummy`). An existing
  configuration file is reported and left untouched, and an already
  initialized `repo` directory is left untouched as well.
- `config var NAME=VALUE` saves a named configuration variable for future commands.
- `config retention-policy add --age <duration> --frequency <duration>` appends
  an entry to the retention policy, a list of `{age, frequency}` entries saved
  under the `retentionPolicy` key in `config.yaml`. Both durations are parsed
  with the [`pytimeparse2`](https://pypi.org/project/pytimeparse2/) library and
  accept strings such as `30s`, `5m`, `1h`, `1d`, `1w`, `1y`, spelled-out units
  (`1 month`), and compounds like `1h30m`. The whole policy is validated when
  it is loaded and when it is saved: every age must be a positive, parsable
  duration and unique as a parsed duration (so `7d` and `1w` cannot both be
  present, since they are the same duration), and every frequency must be
  positive, parsable, and from the supported set (see below). An invalid policy
  is reported and the configuration
  file is left unchanged.
- `config retention-policy list` prints the retention policy entries, one
  indexed line per entry.
- `config retention-policy remove INDEX` removes the entry at the zero-based
  index shown by `config retention-policy list`.

Configuration is stored as `config.yaml` in the per-user configuration directory.
Use `--config /path/to/config-dir` on any command to select a different directory.
The default follows the platform conventions provided by the `platformdirs`
package: `~/.config/duplicacy-py` on Linux, `%APPDATA%\\duplicacy-py` on
Windows, and `~/Library/Application Support/duplicacy-py` on macOS. Linux also
honors `XDG_CONFIG_HOME` when it is set.

The `retentionPolicy` key is a list of `{age, frequency}` entries, for example:

```yaml
retentionPolicy:
- age: 7d
  frequency: 1h
- age: 1month
  frequency: 1d
```

Frequency strings are parsed with the same duration library as ages, but only
a restricted set is supported: `15m` and `30m`, whole hours that divide 24
(`1h`, `2h`, `3h`, `4h`, `6h`, `8h`, `12h`, `24h`), and multiples of 24 hours
(`1d`, `2d`, `1w`, `1month`, `1y`, ...). This keeps the frequency ticks of the
midnight-anchored retention grids at consistent times of day; other durations
(finer granularities like `5m`, hours that do not divide 24 like `5h` or `7h`,
and non-whole-hour compounds like `1h30m`) are rejected as unsupported.

Duration strings are parsed with [`pytimeparse2`](https://pypi.org/project/pytimeparse2/)
through `src/duplicacy_scripts/duration.py` (`parse_duration` returns seconds,
`DurationError` signals invalid input). Suffixes are case-insensitive, so `1M`
parses as one minute, not one month; spell months as `1month`.

The `prune` command uses the retention policy ages to classify revisions into
buckets relative to midnight (the start of today,
`src/duplicacy_scripts/retention.py`), so bucket boundaries and frequency
timestamps fall exactly on calendar days and the results do not depend on the
time of day the command runs. The ages divide time into `n + 1` chronological
buckets for the `n` policy ages: the earliest bucket holds everything older than
the oldest age (unbounded past), the latest holds everything newer than the
newest age up to now (unbounded future), and each remaining bucket spans the
gap between two consecutive ages. Buckets are half-open intervals — inclusive
start, exclusive end. The ages must be unique as parsed durations (`7d` and
`1w` are the same duration, so the policy with both is rejected when loaded,
saved, or used); for the policy above with ages `7d` and `1month` this yields
three buckets.

Each bucket is thinned by the frequency of the entry whose age matches the
bucket's end boundary: ideal timestamps are laid out one frequency apart,
anchored at the bucket's end (midnight) and stepping backwards down to the
bucket's start (for the unbounded-past bucket, down to the first tick at or
below the oldest revision), and the revision closest to each timestamp is
kept — the latest revision wins ties — while the rest are pruned. The
end-boundary tick needs no entry of its own when the next later bucket
keeps a revision within half a frequency of the boundary — the newest
bucket keeps everything it holds, and a thinned bucket keeps its
start-boundary revision — so that revision serves the slot and the
earlier bucket's entry nearest the boundary (e.g. 23:45 against a later
00:00 entry on an hourly grid) is pruned. Revisions newer than the
smallest age (the newest bucket) are always kept, matching the Duplicacy
CLI's prune behaviour. With no retention policy everything is kept. Because
the supported frequencies (15m/30m, whole hours dividing 24, and multiples of
24 hours) all divide or align with whole days, every tick falls at the same
time of day (or every 15/30 minutes past the hour) across runs.

The console command is declared in `pyproject.toml` and points to the
`duplicacy_scripts.main:main` entry point. The entry point assembles the
parser and dispatches; each subcommand lives in its own internal module under
`src/duplicacy_scripts/commands/` (`backup.py`, `list.py`, `prune.py`,
`config.py`), registered in that package's `COMMANDS` table, with shared
CLI-driving helpers in the internal module `src/duplicacy_scripts/_cli.py`.

## Development

```sh
uv sync                  # create/refresh the venv
uv run pytest            # run tests
```

## Duplicacy executable

Scripts are written to work on Windows as well as POSIX. The duplicacy
executable is resolved in this order:

1. The `DUPLICACY_EXECUTABLE` environment variable, or the same variable loaded
   from a `.env` file in the working directory (real environment variables win
   over `.env` values).
2. The `duplicacy` key in `config.yaml`, e.g.:

   ```
   duplicacy: C:\\Tools\\Duplicacy.exe
   ```

3. `duplicacy` on `PATH`.

`.env` is gitignored, so keep credentials there rather than committing them.