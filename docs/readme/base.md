# BaseApp

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
  docs/
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
```

## Core Runtime Features

### 1. COMMON-driven config with evaluate → populate

`base.json` has a `COMMON` node that acts as the single source of truth for all
shared values and computed paths.

`Main.__init__` runs this pipeline once:

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
| `source` | attribute on `Main` to read data from |
| `path` | output directory (supports `{$…$}` placeholders) |
| `file` | output filename |
| `format` | `json`, `csv`, or `html` |
| `kwargs` | extra args passed to `save()` (e.g. `template`) |
| `open` | `true` — auto-open the file after saving |

Default outputs: `results.json`, `results.html`, `config.json`, `config.html`,
`log_html`.

### 4. HTML rendering via HtmlDoc

All HTML output goes through `HtmlDoc(data, template, title, wrappers)`.

- `data` — `dict`, `list`, or `DataFrame`
- `template` — path to an HTML template file (configurable via `HTML_TEMPLATE`)
- Tokens in the template: `{$title$}`, `{$title_colspan$}`, `{$thead$}`, `{$tbody$}`
- Nested dicts and lists render recursively as inner tables
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

`Main.load_data()` iterates every node under `config.input`.

- If `load` is `true`, the node is loaded.
- Loaded data is stored under `results` using the node's `target` value.
- If `path` is a file, that file is loaded.
- If `path` is a folder, all files in the folder tree are loaded recursively.
- Relative and absolute paths are supported; paths are normalized to absolute
  paths before load.

## Configuration

Primary base config: `config/base.json`

Top-level nodes:

- `COMMON` — source of truth for all shared values; drives evaluate + populate
- `app` — name, version, dirs
- `log` — path, verbose, types, colors, max_items
- `input` — load, path, target, format
- `output` — declarative output entries (see above)
- `updates` — changelog (newest first)

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
- Instantiate now resolves `log.messages_dir` relative to `config/`, so
  message code text/type values from `docs/messages/*.csv` are available in
  logs.

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
- `docs/manifests/pull.json`
- `scripts/pullbase.py`
- `utils/baseutils.py`
- `docs/instructions/base.md`
- `docs/instructions/app.md`
- `docs/version/base.txt`
- `docs/readme/base.md`
- `docs/templates/dataset_table.html`
- `config/requirements.txt`

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
- CLI arg overrides are supported (`key=value` pairs passed to `Main`).

## Versioning

BaseApp version is tracked in `COMMON.VERSION` inside `config/base.json` and
mirrored in `docs/version/base.txt`.

App-specific version is tracked in `docs/version/app.txt` and is created once
during instantiation so it remains app-owned during future base pulls.

- Version format: `YY.MM.DD.NN` (two-digit year, month, day, sequence number)
- Rule: every BaseApp modification increments the version.
- Changelog: `updates` array in `base.json`, newest entry first.
- Latest: `26.05.22.16` adds generic `config.input` loading into
  `results.{target}` with folder recursion and absolute path resolution.

## Agent Guidance

Agent-specific development instructions are stored in:

- `docs/instructions/base.md` for base app rules
- `docs/instructions/app.md` for generic variant-app rules
