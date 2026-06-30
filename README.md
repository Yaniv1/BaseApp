# BaseApp

BaseApp is a reusable Python application framework for building configuration-driven
data applications. It gives every project a common foundation — layered JSON
configuration, structured logging, template-based output artifacts, a built-in test
harness, and a managed task/requirements workflow — so that new apps can be spun up
quickly and kept in sync with the framework as it evolves.

You use BaseApp in two roles:

- **As a base** — the canonical framework that you run, test, and extend.
- **As an instantiated app** — a new project created *from* the base that inherits the
  framework, adds its own logic, and pulls future base improvements on demand.

## What it does

- **Layered configuration.** Configuration is assembled from all JSON files in `config/`
  (loaded alphabetically and deep-merged), driven by a shared `COMMON` block that
  supports placeholder expansion and expression evaluation. Files can be disabled by
  naming convention (`_`/`.` prefix) or a `LOADME: false` flag, and a gitignored
  `local.json` lets you override settings per machine without touching tracked files.
- **Structured logging.** A built-in logger writes to the console, CSV, and HTML using
  message codes (defined in `resources/message_codes/`) rather than free text, records
  caller lineage on every entry, and prints an error summary on close.
- **Data loading & transformation.** Inputs are loaded from file or folder sources
  (with optional delta/incremental scanning) and passed through a configurable
  sequence of `DataConverter` transformation steps.
- **Output artifacts.** Results are rendered as JSON and template-based HTML, with
  support for split outputs, content-aware change detection, and concurrent saving.
- **Built-in testing.** A config-driven test framework runs `prep`, `live`, `post`, and
  `build` phases, reporting per-criterion PASS/WARN/FAIL results with traceback context.
- **App lifecycle tooling.** Scripts to instantiate new apps from the base and to pull
  base updates into existing apps, both manifest-driven.
- **Task & requirements management.** A local web-based Task Manager and a spec-driven
  workflow (requirements → architecture → design → implementation → tests → deploy)
  that integrates with git and GitHub Copilot to drive work task-by-task.

## Project structure

| Path | Purpose |
| --- | --- |
| `app/` | Application entry point (`app.py`) and its base (`base.py`). |
| `config/` | Layered JSON configuration (`base.json`, `app.json`, optional `local.json`). |
| `utils/` | Framework code: `baseutils.py` (Config, Logger, output rendering), `datautils.py` (data loading/conversion), `testutils.py` (test helpers), `apputils.py` (app overrides). |
| `scripts/` | Lifecycle tooling: instantiate, pullbase, environment setup, deploy, and the Task Manager. |
| `resources/` | Runtime data: `manifests/`, `message_codes/`, `templates/`, `version/`. |
| `dependencies/` | Python requirements: `base.txt` (core) and `app.txt` (variant-specific). |
| `build/` | Engineering ledgers: `requirements/`, `architecture/`, `tasks/`, `updates/`, `instructions/`. |
| `test/` | The test suite and the `build.py` build-phase runner. |
| `docs/readme/` | Detailed, implementation-level release notes (`base.md`, `app.md`). |

> `README.md` (this file) is a high-level overview. The per-release implementation
> history lives in `docs/readme/base.md` and `build/updates/base.json`.

## Getting started

### 1. Set up the environment

`setup_env.ps1` creates (or reuses) a Python virtual environment and installs only the
missing packages from `dependencies/base.txt` and `dependencies/app.txt`:

```powershell
scripts/setup_env.ps1
```

### 2. Run the app

The app reads its configuration, initializes the logger, and executes a tracked run:

```powershell
python app/app.py
```

### 3. Instantiate a new app from the base

Create a brand-new app that inherits the framework. The target gets the base files plus
the one-time placeholders, and its environment is set up automatically:

```powershell
python scripts/instantiate.py <target-folder>
# optionally seed the new app's config without manual edits:
python scripts/instantiate.py <target-folder> --overrides overrides.json
```

- `--source` points at a specific BaseApp root (defaults to this checkout).
- `--overrides` takes a JSON file path or inline JSON that is deep-merged into the new
  app's `config/app.json`.

### 4. Pull base updates into an instantiated app

From within an instantiated app, refresh the framework files to the latest base. This
copies the `pull` manifest items, adds any missing `once` items, and removes retired
paths listed in the `drop` manifest:

```powershell
python scripts/pullbase.py
python scripts/pullbase.py --hard   # also overwrite app-specific placeholders
```

App-specific artifacts (e.g. `app.py`, `apputils.py`, `config/app.json`) are preserved
on a normal pull — customize the framework by overriding in these app files rather than
editing base files.

### 5. Deploy / verify

`deploy.ps1` runs the full deployment ceremony — environment setup, the
instantiate/pullbase deployment test, and the build-phase tests — reporting per-criterion
PASS/FAIL:

```powershell
scripts/deploy.ps1
```

## Building with the Task Manager

Work on BaseApp is organized as **tasks** that move through a managed, spec-driven
lifecycle: `ToDo → InProgress → Specified → Ready → Deployed → Approved → Done`. The
Task Manager is a local, zero-dependency web UI for creating, tracking, and driving
these tasks.

```powershell
python scripts/task_manager.py            # serves the UI and opens a browser
python scripts/task_manager.py --browser-off
```

How a task flows through the system:

- **Browseable task list.** Tasks are presented in a sortable, filterable list with a
  search box and tabs: an **`ALL`** tab (the default) shows every task in one view, and
  one tab per status narrows the list to that status. Each column (ID, Title, Type,
  Priority — plus Status in the `ALL` tab) can be sorted and filtered, and the search box
  jumps straight to a task by id.
- **Isolation per task.** The repository uses a bare shared object store with every
  branch — including `main` — checked out as its own git worktree under one container.
  Each task is worked on a short-lived `task/<id>` branch in its own dedicated worktree,
  so several tasks can run in parallel without their changes mixing.
- **Copilot integration.** From the UI you can launch a GitHub Copilot worker for a
  task; it works the task on its branch, and a focused review session can be opened when
  the task is `Ready`.
- **Durable status channel.** Workers never edit the task ledger directly. They request
  status changes and progress comments through a durable file-based **task status
  store** (via an `enqueue_status_update` MCP tool), and the Task Manager is the sole
  writer that applies those requests to the ledger on `main`. A server restart never
  blocks a worker — pending requests wait and are applied exactly once.
- **Reviewed promotion.** A task is set to `Ready` for engineer review, `Deployed` once
  finalized and pushed to its own branch, and `Done` only after it is merged into `main`
  and its branch/worktree are dissolved under the integration engineer's supervision. Branch pruning keeps the repository tidy and clean, with a few short-lived task branches that reflect the current state of development at any given moment.

Useful Task Manager flags:

- `--port` / `--host` — choose the bind address.
- `--no-startup-sync` — skip auto-syncing each app's task file with its git repo on start.
- `--no-status-inbox` — disable the watcher that applies queued status-update requests.

## Versioning

The current base and app template versions are recorded in `resources/version/base.txt`
and `resources/version/app.txt` (the app version mirrors the base version with an `A`
prefix), and surfaced in the `COMMON` block of `config/base.json`.
