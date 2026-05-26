# BaseApp

BaseApp is a reusable Python application template for configuration-driven runtime loading, logging, manifest-based app instantiation, and base synchronization.

## Version

- Base version: 26.05.22.16
- App template version: A26.05.22.03

## Highlights (26.05.22.16)

- Added generic input loading flow to process every configured node under `config.input` where `load=true`.
- Loaded input data is stored in results using each node's `target` value.
- Input `path` supports both single files and folders (folder loads all files recursively).

## Core Scripts

- `scripts/instantiate.py`: instantiate a new app from BaseApp manifests.
- `scripts/pullbase.py`: pull base updates into an instantiated app.
- `app/app.py`: run the app.
