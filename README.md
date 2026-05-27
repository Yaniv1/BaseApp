# BaseApp

BaseApp is a reusable Python application template for configuration-driven runtime loading, logging, manifest-based app instantiation, and base synchronization.

## Version

- Base version: 26.05.26.02
- App template version: A26.05.26.02

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
