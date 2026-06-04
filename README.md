# BaseApp

BaseApp is a reusable Python application template for configuration-driven runtime loading, logging, manifest-based app instantiation, and base synchronization.

## Version

- Base version: 26.06.04.03
- App template version: A26.06.04.03

## Highlights (26.06.04.03)

- `store_outputs` now writes artifacts concurrently via `ThreadPoolExecutor`. Set `CONFIG.COMMON.OUTPUT_WORKERS` (default 8) to control the worker count; 0 or 1 retains sequential behaviour.
- Per-worker exceptions are caught and logged as `BASEW06` without aborting other writes.

## Highlights (26.06.04.02)

- Extended `pullbase.py` to syncs `once`-manifest items to existing apps if they don't exist.

## Highlights (26.06.04.01)

- Improved Logger performance to append log entries rather than rewrite the log file with each log entry. 
- Improved App Manager performance by avoiding warnings for string to JSON parsing while saving outputs.
- Add supports in `DataFrameConverter.apply` for a `custom` scope for expression-based transformations that return raw results.


## Highlights (26.06.03.03)

- Added config-driven task report generation with status-based grouping via the built-in `PROCESS` mechanism.
- Tasks are grouped by status field through custom data conversion expressions and rendered to separate JSON and HTML files.
- New outputs: `tasks_by_status_split` (JSON) and `tasks_by_status_html` (HTML) stored under `tasks/reports/` folder.

## Highlights (26.06.03.02)

- Added `requirements` and `tasks` as BaseApp runtime inputs.
- Added HTML outputs for requirements/tasks catalogs under the run output path.

## Highlights (26.06.03.01)

- Added a Task Management framework. Every task gets added to the taks list and progress is tracked.
- Added a Requirements Engineering framework. Every requirement is specified and a through spec-driven-development is adhered to (breakdown, solution design, implementation, verification, and deployment)

## Highlights (26.06.02.03)

- Full architecture alignment: comparator now reports `MissingInCode=0, MissingInArch=0, Mismatches=0` against `docs/architecture/base.json` and `docs/architecture/app.json`.
- Expanded base architecture inventory to cover all public functions/methods in `scripts/`, `utils/baseutils.py`, `utils/datautils.py`, `utils/testutils.py`, plus leaf docs files, the `updates/` folder, and `README.md`.
- Architecture compliance comparator now skips `__init__.py`, dotfiles, and dot-folders (e.g. `.git`, `.vscode`), and resolves JSON-pointer paths of the form `file.json::dotted.key`.
- `Logger.log` records caller lineage (file, module, class, function, line) on every entry via inspect-based stack walking, surfaced as a new `caller` column in the log CSV/HTML.
- Test outputs route to a dedicated `TESTS_RESULTS` subfolder via a test-config `OUTPUT_PATH` override, so `TestManager` no longer overwrites the main app's `config.json` / `config.html`.

## Highlights (26.06.02.02)

- Added an output-level `delta` flag in `_save_output_artifact` backed by a sha256 content-checksum manifest.
- `AppManager` holds an in-memory `output_manifest` (loaded from `<OUTPUT_PATH>/manifest.json` at init) keyed by full artifact path with `{sha256, mtime}` values.
- Saves are skipped only when the new content checksum matches the stored sha256 AND the on-disk file mtime matches the manifest entry; the skip is logged via `BASE003` with `skipped: true, delta: true, sha256: <hash>`.
- After every write the manifest entry is refreshed and the manifest is flushed to disk so it survives process restarts.
- Applies per leaf for split outputs. Restored the missing `save_kwargs` initialization so output saves work end-to-end.

## Highlights (26.06.02.01)

- Added an input-level `delta` flag in `DataLoader`: when enabled, the loader rescans the input surface on every call and only loads files that are new or whose on-disk mtime changed since the previous load.
- `AppManager` persists a per-input `last_modified` map (`self.input_meta`) across cycles, enabling fast cyclic / periodic scanning of large input stores and automatic pickup of modified files.
- `BASE012` now reports `delta`, `loaded`, and `skipped` counters for observability.

## Highlights (26.05.28.02)

- Moved suggested architecture change JSON files and the compliance report into `{$OUTPUT_PATH$}/tests/results/architecture_changes`.
- Kept the architecture review HTML artifacts in the dedicated review output folder.

## Highlights (26.05.28.01)

- Added path metadata to `docs/architecture/base.json` and `docs/architecture/app.json`.
- Added a class-based architecture compliance prep test in `test/tests/base/base.py`.
- Added combined-architecture and code-tree HTML review artifacts for architecture compliance runs.
- Updated the test runner so class-based tests can construct from normal input bindings.
- Added a shebang to `app/app.py` for direct interpreter execution.

## Highlights (26.05.26.03)

- Added a built-in test framework with config-driven prep, live, and post phases.
- Added a dedicated PASS/WARN/FAIL test logger and persisted test results/log artifacts.
- Derived test config from the integrated runtime config so app-specific COMMON overrides flow into test outputs.
- Switched monitoring to the main logger `data_map`, allowing the app to publish lightweight runtime state without direct test coupling.
- Added richer test reporting with explicit failure ids, failure counts, and traceback context.
- Fixed app exit handling so the process returns an integer code instead of a structured object.

## Highlights (26.05.26.02)

- Added unified input loading for file and folder sources under `config.input`.
- Moved BaseApp changelog entries into `updates/base.json` while keeping them loadable as input data.
- Added `config.process` support with `DataFrameConverter` for sequential result transformations.
- Added recursive JSON field materialization for stored outputs.
- Improved HTML rendering with item/type columns and content-width nested tables.
- Added split output support so dict outputs can be written as separate artifacts by key.
- Added multi-layer split support and dotted source resolution for nested output sources such as `results.message_codes`.

## Core Scripts

- `scripts/instantiate.py`: instantiate a new app from BaseApp manifests.
- `scripts/pullbase.py`: pull base updates into an instantiated app.
- `app/app.py`: run the app.
