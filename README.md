# BaseApp

BaseApp is a reusable Python application template for configuration-driven runtime loading, logging, manifest-based app instantiation, and base synchronization.

## Version

- Base version: 26.06.22.01
- App template version: A26.06.22.01

## Highlights (26.06.22.01)

- **Task Manager Copilot Integration**.

## Highlights (26.06.19.01)

- **Local task manager web UI.** `scripts/task_manager.py` provides a zero-dependency local web interface (stdlib HTTP server + single-page JS) for task management. All changes persist immediately to disk. Run with `python scripts/task_manager.py --open-browser`.
- **HTML template path fix.** Relative `HTML_TEMPLATE` values in config are now resolved against the project root (`base_dir`) in all three call sites (`utils/baseutils.py`, `scripts/instantiate.py`, `scripts/pullbase.py`). The config value is now the clean project-root-relative path `resources/templates/dataset_table.html`.

## Highlights (26.06.18.01)

- **Config file filtering by name and flag.** Config now skips config JSON files based on naming convention (underscore `_` or dot `.` prefix) and explicit flag (`LOADME: false` key). This allows users to disable config overrides without deleting files and follow naming conventions for temporary/development configs. Alphabetical load order is preserved for active files.

## Highlights (26.06.17.01)

- **Error-summary console mode.** Logger now prints ERROR summary to console.
- **Coverage added for grouped logs.** Post test `test_logs_by_type` validates grouped runtime structure and per-type output file creation.

## Highlights (26.06.16.01)

- **Logger message text placeholders.** `Logger.log()` now accepts `populate=False`. When `True`, `{key}` placeholders in the looked-up message text are replaced with values from the `data` dict before storing or printing. Wrappers are configurable via `message_wrappers` on `Logger` (default `("{","}")`). Message texts updated across `logger.csv` and `base.csv`; all relevant call sites updated with `populate=True`.

## Highlights (26.06.11.02)

- **`drop.json` entries without a `target` now delete the source.** `pullbase` (`load_drop_entries` / `drop_deprecated_paths`) accepts entries where `target` is absent or null and removes the source file/directory outright. The `build/_workflow` delete entry in `drop.json` now uses this form.

## Highlights (26.06.11.01)

- **Removed `build/_workflow/`.** The folder and its architecture entry (`10.5`) have been removed. Added `build/_workflow` to `drop.json` so instantiated apps clean it up on next pull.

## Highlights (26.06.10.06)

- **Architecture base.json corrected.** Fixed mismatches between `build/architecture/base.json` and the codebase
## Highlights (26.06.10.04)

- **Local config override support.** `config/local.json` is now a gitignored placeholder that is deployed once via `once.json` manifest. Values in it are deep-merged on top of `base.json` + `app.json` at load time (no code changes — `Config` already processes all JSON files in `config/` sorted alphabetically).

## Highlights (26.06.10.03)

- **Bug fix: log file removal no longer trips the app.** `Logger._remove_old_files()` now silently skips files that cannot be deleted due to permission or in-use errors.

## Highlights (26.06.10.02)

- **More Readable plain text in HTML outputs.**.

## Highlights (26.06.10.01)

- **Config overrides during instantiation.** `instantiate.py` now accepts `--overrides` (a JSON file path or inline JSON string) so the new app's `config/app.json` is pre-configured at creation time without manual edits. Overrides are deep-merged, so only specified keys are changed and all other keys are preserved.

## Highlights (26.06.09.03)

- **Tasks grouped by type and status.** A new `tasks_by_type_status` PROCESS step produces a nested `{type: {status: [tasks]}}` dict; tasks without an explicit type fall under `Feature`. Per-type HTML reports are written via `tasks_by_type_status_split` OUTPUT. New post test validates the nested structure.

## Highlights (26.06.09.02)

- **`DataConverter` now isolates failing conversion steps.** Each step in `DataConverter.apply()` is wrapped in a try/except; any failure (including referencing a missing source column) is caught, logged as `DATAE01`, and collected in `self.errors` — without aborting the remaining steps.
- **Dict sources passed correctly to `DataConverter`.** When a process step's source is a `dict`, it is now forwarded intact instead of being replaced with an empty DataFrame.

## Highlights (26.06.09.01)

- The user can control the `caller_depth` stack size for logged messages (default is 2, WARN/ERROR/FAIL = 4).
- The **`instantiate.py`** script supports optional instantiation from a different `--source` folder of the original BaseApp root; improved same-path error message.
- Task report is grouped by task type and status.
- HTML outputs now support file paths and URLs as **HTML hyperlinks**.

## Highlights (26.06.08.02)

- Renamed `RESULT_MAP` → `OUTPUT_MAP`; initialized in `AppManager.__init__`; deep-update semantics; `test_manager` isolation.

## Highlights (26.06.08.01)

- `store_outputs` populates `OUTPUT_MAP` with `{output_key: [file_paths]}` and returns it; `result_map` output entry added.

## Highlights (26.06.06.05)

- Enabled capability to drop retired files and folders by adding `resources/manifests/drop.json` (Feature 11.3.3): lists deprecated paths removed from variant apps on every base pull (`docs/instructions`, `docs/tasks`, `docs/requirements`, `docs/architecture`, `docs/message_codes`, `docs/templates`, `docs/manifests`, `docs/version`, `updates`).
- `scripts/pullbase.py`: new `load_drop_entries()` (Feature 3.2.15) and `drop_deprecated_paths()` (Feature 3.2.16) clean up old locations automatically after each pull. Logged with `PULL009`/`PULL010`.

## Highlights (26.06.06.04)

- `scripts/setup_env.ps1`: replaced `pip.exe` with `python -m pip` to fix *Unable to create process* failures when the venv's Python path has changed.

## Highlights (26.06.06.03)

- `utils/baseutils.py`: Unicode stdout/stderr reconfiguration for safe output on Windows cp1252 consoles.

## Highlights (26.06.06.02)

- Moved `docs/version/` to `resources/version/`, completing the consolidation of shared runtime data under `resources/`.

## Highlights (26.06.06.01)

- Refactored the workspace file tree: build-related assets moved to `build/` (instructions, tasks, requirements, architecture, updates) and runtime data resources moved to `resources/` (message_codes, templates, manifests, version). `docs/` now holds only `readme/`.

## Highlights (26.06.05.01)

- `to_json_compatible` is now a standalone module-level function (Feature 6.1.17) in `utils/baseutils.py`, importable from anywhere in the codebase.
- Fixed the build-phase deployment test to pass `sys.executable` as the Python interpreter, ensuring the venv Python is used. Build test now 25/25 PASS.

## Highlights (26.06.04.07)

- Added `scripts/setup_env.ps1` (Feature 3.4): creates or reuses the Python virtual environment and installs only the missing packages from `dependencies/base.txt` and `dependencies/app.txt` via pip.
- Added `scripts/deploy.ps1` (Feature 3.5): deployment ceremony script that calls `setup_env.ps1` → `scripts/test_deployment.ps1` → `test/tests/build.py` in three sequential phases and reports per-criterion PASS/FAIL with an overall summary.
- `scripts/instantiate.py` and `scripts/pullbase.py` now invoke `setup_env.ps1` automatically after their file operations (Features 3.1.11 and 3.2.14), so every fresh instantiation and every base pull ensures the virtual environment is up to date.
- Merged `BASE-REQ-011` into `BASE-REQ-012` as sub-requirement `012.8` (deployment validation via `test_deployment.ps1`).

## Highlights (26.06.04.06)

- Refactored `config/requirements.txt` into `dependencies/base.txt` (core runtime packages, pulled on every base update) and `dependencies/app.txt` (variant-specific additions, copied once on instantiation).
- Updated `docs/manifests/pull.json` and `docs/manifests/once.json` to reference the new dependency files.
- Added `dependencies` folder as Feature 9 in the base architecture; removed stale Feature 2.3 (`config.requirements.txt`).

## Highlights (26.06.04.05)

- Added `test/tests/build.py` (Feature 5.3.2): a standalone build-phase test runner that loads runtime and test configuration, executes the `build` phase via `TestManager`, stores outputs, and returns exit code 0 (pass) or 1 (fail).
- Added `test_deployment` (Feature 6.3.5) to `utils/testutils.py`: runs a PowerShell deployment script non-interactively, parses each `[PASS]`/`[FAIL]`/`[WARN]` output line as a structured criterion, and returns a result dict compatible with `TestManager`.
- Fixed `TestManager.run_a_test` to dynamically initialize the results slot for any phase name, enabling the extensible `build` phase.
- Added requirement `BASE-REQ-007.5` and message code `TST008`.

## Highlights (26.06.04.04)

- Added a standalone PowerShell integration test that verifies the end-to-end deployment pipeline (instantiate + pullbase) with 25 per-criterion PASS/FAIL checks.

## Highlights (26.06.04.03)

- Added concurrent saving capability to `store_outputs`, to improve output storing performance.

## Highlights (26.06.04.02)

- Extended `pullbase.py` to syncs `once`-manifest items to existing apps if they don't exist.

## Highlights (26.06.04.01)

- Improved Logger performance to append log entries rather than rewrite the log file with each log entry. 
- Improved App Manager performance by avoiding warnings for string to JSON parsing while saving outputs.
- Add supports in `DataConverter.apply` for a `custom` scope for expression-based transformations that return raw results.


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
- Added `config.process` support with `DataConverter` for sequential result transformations.
- Added recursive JSON field materialization for stored outputs.
- Improved HTML rendering with item/type columns and content-width nested tables.
- Added split output support so dict outputs can be written as separate artifacts by key.
- Added multi-layer split support and dotted source resolution for nested output sources such as `results.message_codes`.

## Core Scripts

- `scripts/instantiate.py`: instantiate a new app from BaseApp manifests.
- `scripts/pullbase.py`: pull base updates into an instantiated app.
- `app/app.py`: run the app.
