# BaseApp

BaseApp is a reusable Python application template for configuration-driven runtime loading, logging, manifest-based app instantiation, and base synchronization.

## Version

- Base version: 26.05.28.01
- App template version: A26.05.28.01

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
