# BaseApp V-26.05.28.02

BaseApp is a reusable Python foundation for app projects that need:

- COMMON-driven layered JSON configuration with expression evaluation and placeholder expansion
- structured logging (console + CSV + HTML)
- standard output artifacts (JSON + HTML) via template-based rendering
- a clean workflow to instantiate new apps from the base
- a manifest-driven way to pull base updates into already-instantiated apps

## Current Structure

```text
BaseApp/
  app/
    base.py
    app.py
  config/
    base.json
    app.json
    requirements.txt
  test/
    config/
      app.json
      base.json
    tests/
      app/
      base/
  updates/
    base.json
  docs/
    architecture/
      base.json
      app.json
    message_codes/
      base.csv
      app.csv
      logger.csv
      test.csv
    manifests/
      pull.json
      once.json
    instructions/
      base.md
      app.md
    readme/
      base.md
      app.md
    templates/
      dataset_table.html
    version/
      app.txt
      base.txt
  scripts/
    instantiate.py
    pullbase.py
  utils/
    baseutils.py
    apputils.py
    datautils.py
    testutils.py
```

## Core Runtime Features

### 1. COMMON-driven config with evaluate → populate

`base.json` has a `COMMON` node that acts as the single source of truth for all
shared values and computed paths.

`AppManager.__init__` runs this pipeline once:

```
config.evaluate(["COMMON"])   # eval Python expressions in COMMON values
config.populate(["COMMON"])   # replace {$KEY$} placeholders everywhere
```

`evaluate` — runs `tryeval()` on every string value inside COMMON.
Expressions like `dt.datetime.utcnow().strftime('%Y%m%d')` resolve to their
computed value; plain strings are kept as-is.

`populate` — collects all COMMON key/value pairs as replacement tokens,
resolves inter-token references (e.g. `OUTPUT_PATH` referencing `OUTPUT_PREFIX`),
then does a single JSON-level string substitution across the entire config tree.

Placeholder syntax is configured via `CONFIG_WRAPPERS` (default `["{$", "$}"]`).

Example COMMON keys:

| Key | Value |
|---|---|
| `APP_NAME` | `"BaseApp"` |
| `OUTPUT_PREFIX` | `"C:/data/{$APP_NAME$}"` |
| `OUTPUT_VERSION` | `"dt.datetime.utcnow().strftime('%Y%m%d')"` |
| `OUTPUT_PATH` | `"{$OUTPUT_PREFIX$}/{$OUTPUT_VERSION$}"` |
| `START_TIME` | `"dt.datetime.utcnow().strftime('%Y%m%dT%H%M%SZ')"` |
| `RUN_ID` | `"str(uuid.uuid4().hex[:6]).upper()"` |

### 2. Recursive partial config override

`Params.set()` deep-merges incoming dicts into existing ones at every nesting
level. An app's `config/app.json` can override only the keys it cares about;
all other base values are inherited unchanged.

Example `app.json` — sets only `COMMON.APP_NAME`:

```json
{
    "COMMON": {
        "APP_NAME": "MyApp"
    }
}
```

Everything else (`OUTPUT_PREFIX`, `OUTPUT_PATH`, paths, timestamps, etc.)
resolves automatically from that single override.

### 3. Declarative output artifacts

Outputs are declared in the `output` node of `base.json`. Each entry controls:

| Field | Purpose |
|---|---|
| `store` | `true` / `false` — whether to write the file |
| `source` | attribute on `AppManager` to read data from |
| `path` | output directory (supports `{$…$}` placeholders) |
| `file` | output filename |
| `format` | `json`, `csv`, or `html` |
| `split` | `false`, `true`, or a depth integer for split artifact output |
| `kwargs` | extra args passed to `save()` (e.g. `template`) |
| `open` | `true` — auto-open the file after saving |

Default outputs: `results.json`, `results.html`, `config.json`, `config.html`,
`log_html`.

When `split` is enabled and the output value is a dict:

- `true` is treated as a split depth of `1`
- numeric values like `2` split recursively across additional dict layers
- with `file`: `{path}/{k1}/{k2}/.../{file}`
- without `file`: `{path}/{k1}/{k2}/.../{last_key}.{format}`

Output `source` also supports dotted path resolution such as `results.message_codes`.

CSV split output expects each leaf value to be DataFrame-compatible.

### 4. HTML rendering via HtmlDoc

All HTML output goes through `HtmlDoc(data, template, title, wrappers)`.

- `data` — `dict`, `list`, or `DataFrame`
- `template` — path to an HTML template file (configurable via `HTML_TEMPLATE`)
- Tokens in the template: `{$title$}`, `{$title_colspan$}`, `{$thead$}`, `{$tbody$}`
- Nested dicts and lists render recursively as inner tables
- Every rendered table includes a leading item enumeration column
- Dict tables include a `type` column after `key`
- Array values show item counts in the `type` column and dict values show key counts
- Nested tables size to their content instead of stretching to the full parent width
- Arrays of dicts render as proper tabular rows (keys → columns, items → rows)

Default template: `docs/templates/dataset_table.html`

### 5. Logger

- Type-based logging: `NONE`, `INFO`, `GOOD`, `WARN`, `ERROR`
- Console colors driven by `log.colors` in config
- Each run writes a timestamped CSV log and an HTML log
- `max_items` cleanup enforced on both `.csv` and `.html` log folders
- `Logger.log(..., data={...})` supports per-entry key/value payloads stored in
  a single `data` CSV column; rows without payload keep `data` empty

### 6. Auto-open outputs

Any output entry with `"open": true` is opened in the default associated
application (browser for HTML, etc.) immediately after being saved.
On Windows `os.startfile` is used; on other platforms `webbrowser.open`.

### 7. Runtime tracking decorator

`utils/baseutils.py` now includes a reusable `trackit` decorator used on
`execute()` in `app/base.py`.
It tracks execution with `time.perf_counter()` and returns a structured dict:

- `function`
- `result`
- `metrics` (currently includes `duration_seconds`, and supports additional
  metrics later)

The caller (`run()`) is responsible for logging this tracking output via
message code `BASE005`.

### 8. Generic input loading (`config.input`)

`AppManager.load_data()` iterates every node under `config.input`.

BaseApp defaults include `message_codes` and `updates` as input sources.

- If `load` is `true`, the node is loaded.
- Loaded data is stored under `results` using the node's `target` value.
- Input loading builds a file-key map first, then uses the same concurrent load
  path for any number of files.
- If `path` is a file, its basename is used as the key.
- If `path` is a folder, all files in the folder tree are loaded recursively.
- Folder loads are keyed by relative file path under the target.
- Relative and absolute paths are supported.
- Relative paths are resolved from the app/project root directory.

#### Delta mode

Each input node accepts an optional `delta` flag (default `false`) handled at
the input level by `DataLoader`:

- When `delta` is `false`, every discovered file is (re)loaded on each call.
- When `delta` is `true`, the loader rescans the source surface on every call
  and only loads files that are either missing from the previously loaded
  results or whose on-disk modification time has changed since the last load.
- A per-input `last_modified` map (file key → mtime) is maintained by
  `AppManager` in `self.input_meta[input_key]` and passed back into the loader
  on subsequent cycles, so files modified after their initial load are picked
  up automatically.
- This makes cyclic / periodic scanning of large input stores cheap: unchanged
  files are skipped, and newly appearing files on the input surface are
  detected and loaded on the next call.
- `BASE012` reports `delta`, `loaded`, and `skipped` counters per call for
  observability.

### 9. Config-driven processing (`config.process`)

`AppManager.process_data()` runs after `load_data()` and before outputs are stored.

- Each key under `config.process` is a step id.
- `source` points to an item already loaded into `results`.
- `context` maps aliases to other `results` items that should be exposed to
  conversion expressions.
- The selected source is also exposed to expressions as `source_data`.
- Only DataFrame sources are used directly as the working DataFrame; other
  source shapes stay explicit so conversions can decide how to transform them.
- `conversions` is applied sequentially through `DataFrameConverter`.
- `DataFrameConverter` supports an injectable logging function and defaults to
  `print` when verbose logging is enabled.
- `df`-scope conversions do not need their own `target`; the step-level
  `target` controls where the final processed DataFrame is stored.
- Process outputs are stored back on `results` using the step id unless a
  `target` override is provided.

BaseApp includes an example step, `message_codes_df`, which concatenates all
loaded message code CSVs from the `message_codes` context mapping into a single
DataFrame and adds a `source_file` column.

### 10. Output JSON materialization and parse diagnostics

`store_outputs()` now converts nested output structures into JSON-safe primitives
and materializes JSON-like string fields into native objects.

- Applies recursively across dict/list/object values.
- Applies to DataFrame rows before serialization.
- Converts stringified JSON objects/arrays (for example `ExtendedProps`,
  `ResourceProps`, `Tags`) into dict/list values.
- Logs parse summary with `BASE011` (attempted/parsed/failed counts).
- Logs parse failures with `BASEW11` including error and value sample.

### 11. Built-in testing framework

BaseApp now includes a config-driven testing framework that runs alongside the
normal application lifecycle.

- `test/config/base.json` defines test phases, dedicated logger settings, and test outputs.
- `TestManager` in `utils/testutils.py` coordinates three phases:
  `prep`, `live`, and `post`.
- Prep tests run before `execute(app)`.
- Live tests run on a dedicated background thread and schedule themselves by
  `frequency_seconds` while the app is executing.
- Post tests run during shutdown after elapsed time has been computed.
- Test config is built from the integrated runtime config, so app-level COMMON
  overrides such as `APP_NAME` and `OUTPUT_PATH` flow into test outputs.
- The app remains test-agnostic and exposes lightweight runtime state through
  the main logger `data_map`; tests read that monitor state or inspect stored
  outputs instead of reaching directly into app internals.
- Test definitions can target functions, classes, or class methods via dotted
  import paths.
- Each test writes to a dedicated test result logger configured with `PASS`,
  `WARN`, and `FAIL` message types.
- Detailed test results persist under `results.prep`, `results.live`, and
  `results.post`.
- Aggregate summaries persist under `results.summary` and `results.report`,
  including explicit `failures` lists and `n_failures` counts.
- Failed test records also include traceback metadata such as failing callable,
  file, line, function, and formatted traceback text.

Default test artifacts are written as:

- `{$OUTPUT_PATH$}/tests/results/{START_TIME}.json`
- `{$OUTPUT_PATH$}/tests/results/test_results.html`
- `{$OUTPUT_PATH$}/tests/summary/summary.html`
- `{$OUTPUT_PATH$}/tests/logs/test_log.html`

BaseApp also includes a prep-time architecture compliance test in
`test/tests/base/base.py`.

- It deep-merges `docs/architecture/base.json` first, then additional
  architecture files such as `docs/architecture/app.json`.
- Architecture items can now include a `path` field for exact matching.
- Paths are relative to the app root and file members use `file::symbol`
  syntax such as `utils/baseutils.py::dict_merge` and
  `utils/baseutils.py::Logger.log`.
- The test compares architecture items to code items by `path` when available,
  then falls back to `name` matching.
- Missing architecture items, missing code items, and feature id mismatches are
  logged as `WARN` results.
- The test writes HTML review artifacts to `{$OUTPUT_PATH$}/tests/architecture/`:
  `combined_architecture.html` and `code_tree.html`.
- Suggested architecture change JSON files and the compliance report are written
  to `{$OUTPUT_PATH$}/tests/results/architecture_changes/`.

## Configuration

Primary base config: `config/base.json`

BaseApp changelog entries are stored in `updates/base.json` and merged into the
runtime config as `updates`.

Top-level nodes:

- `COMMON` — source of truth for all shared values; drives evaluate + populate
- `app` — name, version, dirs
- `log` — path, verbose, types, colors, max_items
- `input` — load, path, target, format, delta
- `process` — sequential DataFrame conversion steps over `results` data
- `output` — declarative output entries (see above)
- `updates` — changelog loaded from `updates/base.json` (newest first)

Architecture inventories are maintained under `docs/architecture`.

- `docs/architecture/base.json` stores the reusable base feature inventory.
- `docs/architecture/app.json` stores variant-app feature inventory and app-owned placeholders.
- Complex feature nodes include `name`, `path`, `description`, `type`, and nested `features`.

Testing config is maintained separately in `test/config/base.json` and is
flattened into a runtime test config using:

- `COMMON` from the integrated app config
- the value dictionary under `testing` from `test/config/base.json`

### Layering rule

`Params.set()` recursively merges dicts at every depth.  App config only needs
to supply the keys it wants to change — the rest are inherited from base.

## Running BaseApp

From `BaseApp/app`:

```powershell
python app.py
```

Install dependencies first:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r config/requirements.txt
```

## Instantiate a New App

Script: `scripts/instantiate.py`

Usage from `BaseApp` root:

```powershell
python scripts/instantiate.py ../MyNewApp
```

Behavior:

- Reads all `.json` files under `docs/manifests`.
- Applies entries under `pull` by copying on every instantiate run.
- Overwrites core/base files on repeat runs.
- Applies entries under `once` only when destination files do not already
  exist, including `docs/version/app.txt`.
- Creates `docs/instructions/{APP_NAME}.md` only if it does not already exist.
- When `config/app.json` is created, it sets only `COMMON.APP_NAME` to the
  target folder name. All dependent values (paths, filenames, etc.) resolve
  automatically through the placeholder pipeline at runtime.
- Writes instantiate logs under
  `C:/data/BaseApp/instantiations/{date}/{target_app}_{timestamp}.csv` and
  renders a matching HTML file at
  `C:/data/BaseApp/instantiations/{date}/{target_app}_{timestamp}.html`.
- Instantiate resolves `log.messages_dir` from the app/project root, so
  message code text/type values from `docs/message_codes/*.csv` are available
  in logs.

## Pull Base Updates into an Instantiated App

Script in the instantiated app: `scripts/pullbase.py`

```powershell
python scripts/pullbase.py                      # normal: manifest-driven
python scripts/pullbase.py --hard               # hard: overwrite everything
python scripts/pullbase.py --source ../../BaseApp  # explicit source path
```

### Normal mode

Syncs BaseApp `docs/manifests` to local `docs/manifests`, then applies all
entries under the `pull` key from all manifest files in that folder.
To add or adjust pull behavior, update manifest files in `docs/manifests`.
- Writes pullbase logs under
  `C:/data/{local_app}/{date}/basepulls/{local_app}_{timestamp}.csv` and
  renders a matching HTML file at
  `C:/data/{local_app}/{date}/basepulls/{local_app}_{timestamp}.html`.
- If pullbase cannot initialize logging, it now prints an explicit
  "Pullbase logging disabled: ..." message so the failure reason is visible.

Manifest behavior is key-driven, not filename-driven.

Each section is a list of objects:

- `source` (required)
- `target` (optional, defaults to `source`)

If `source` points to a folder, all files under that folder are synced
recursively. For folder entries, `target` is treated as the destination folder
root (or defaults to the same relative folder path).

Current manifest (`docs/manifests/pull.json`):

The following are current `pull` sources:

- `app/base.py`
- `config/base.json`
- `updates/base.json`
- `docs/architecture/base.json`
- `docs/manifests/pull.json`
- `scripts/pullbase.py`
- `utils/baseutils.py`
- `utils/datautils.py`
- `utils/testutils.py`
- `docs/instructions/base.md`
- `docs/instructions/app.md`
- `docs/version/base.txt`
- `docs/readme/base.md`
- `docs/templates/dataset_table.html`
- `docs/message_codes/base.csv`
- `docs/message_codes/logger.csv`
- `docs/message_codes/test.csv`
- `config/requirements.txt`
- `scripts`
- `test/config/base.json`
- `test/tests/base`

### Hard mode (`--hard`)

Copies every file from BaseApp (excluding `__pycache__`, `.git`, `.venv`,
`.pyc`, `.pyo`), overwriting all local files including app-specific placeholders.
Use this to fully reset an app to the base.

## Builder Workflow (Recommended)

1. Instantiate once: `python scripts/instantiate.py ../MyNewApp`
2. Edit only app-dedicated files: `app/app.py`, `config/app.json`,
   `utils/apputils.py`, `docs/readme/app.md`
3. In `config/app.json` override only `COMMON.APP_NAME` (and any other keys
   that differ from base); let the placeholder pipeline derive everything else.
4. Pull base updates periodically: `python scripts/pullbase.py`
5. Re-run and review generated JSON/HTML artifacts.

## Notes

- Windows-style paths are used by default (`C:/data/…`).
- CLI arg overrides are supported (`key=value` pairs passed to `AppManager`).

## Versioning

BaseApp version is tracked in `COMMON.VERSION` inside `config/base.json` and
mirrored in `docs/version/base.txt`.

App-specific version is tracked in `docs/version/app.txt` and is created once
during instantiation so it remains app-owned during future base pulls.

- Version format: `YY.MM.DD.NN` (two-digit year, month, day, sequence number)
- Rule: every BaseApp modification increments the version.
- Changelog: `updates` array in `updates/base.json`, newest entry first.
- Latest: `26.05.28.01` adds path-aware architecture inventories, a class-based
  architecture compliance prep test with HTML review artifacts, class-target
  test construction from normal input bindings, and a shebang on `app/app.py`
  for direct interpreter execution.

## Agent Guidance

Agent-specific development instructions are stored in:

- `docs/instructions/base.md` for base app rules
- `docs/instructions/app.md` for generic variant-app rules
