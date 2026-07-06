# BaseApp V-26.07.06.01

BaseApp is a reusable Python foundation for app projects that need:

- COMMON-driven layered JSON configuration with expression evaluation and placeholder expansion
- structured logging (console + CSV + HTML)
- standard output artifacts (JSON + HTML) via template-based rendering
- a clean workflow to instantiate new apps from the base
- a manifest-driven way to pull base updates into already-instantiated apps

## Release Highlights (26.07.06.01)

- **Task Manager tracks the Copilot model and token consumption per task and per lifecycle state, and the approval lifecycle is expanded** (Feature 3.6.17; Requirement BASE-REQ-014.22): Each task's `worker_session` in the ledger now records which Copilot **model** the worker ran and how many **tokens** it consumed — both a cumulative `TOTAL` and a `per_state` breakdown by lifecycle status. Because the Copilot CLI worker does not reliably self-report usage, the Task Manager (`scripts/task_manager.py`, the sole ledger writer) populates these fields by reading the CLI's per-session `events.jsonl` under `<COPILOT_HOME>/session-state` (honouring the `COPILOT_HOME` env override, default `~/.copilot`; no separate config key), matching a session to a task by the `session.start` working directory against the task's worktree, summing `assistant.message` output tokens for the `TOTAL`, and taking the model from the latest message. On each applied status-update it adds the cumulative-token **delta** to `per_state[<current status>]` (never overwriting, so revisiting a state accrues on top) and refreshes `TOTAL`/model; reading usage is best-effort and non-blocking. The **approval lifecycle** was expanded to cleanly separate "finalizing" from "approved": `SpecApproved` (between `Specified` and `Ready`), `CodeApproved` (between `Ready` and `Deployed`), and `Approved`→`BuildApproved`, giving `ToDo → InProgress → Specified → SpecApproved → Ready → CodeApproved → Deployed → BuildApproved → Done` with three review gates. The Task Manager UI (`resources/templates/task_manager.html`) surfaces usage: a numeric, sortable **Tokens** column (shown in every tab and the ALL tab) rendered **blank** — not `0` — when no usage is recorded, and **heat-coloured** green (lowest) → red (highest) with the colour scale anchored to the **whole ledger** so a given count keeps the same colour regardless of the active tab/filter/sort; and a per-phase **usage table** in the detail pane that aggregates `per_state` into the three accounting phases mirroring the gates — **Design** (InProgress/Specified/SpecApproved), **Develop** (Ready/CodeApproved), and **Deploy** (Deployed/BuildApproved/Done) — plus a TOTAL row and the model. This release also: makes the default ledger file resolve to **`base.json` for BaseApp** (identified by `COMMON.APP_NAME`, so it is correct even inside a task worktree whose folder is a task id) while instance apps keep `app.json`; **persists the selected tasks directory across reloads** (via `localStorage`, authoritative even across server restarts) alongside the already-persisted file selection; **syncs a newly created task to the remote immediately** on `POST /api/tasks`; and adds a read-only built-in **"By Tokens"** view (`config/base.json`) that ranks tasks by token consumption (descending). Tests in `test/tests/base/task_manager.py` cover the null/blank rendering, the ledger-anchored heat colouring, the `APP_NAME`-based default-file resolution, the tasks-dir persistence, the immediate git-sync on create, and the built-in "By Tokens" view.

## Release Highlights (26.07.02.01)

- **User-controlled config file selection and order via `COMMON.CONFIG_FILES`** (Feature 6.1.4; Requirement BASE-REQ-001.6): Configuration overlay is now driven by the config itself instead of a fixed alphabetical folder scan. After the base config file is loaded, an ordered overlay work-list is built: by default it is the base config's own folder expanded alphabetically (the base file overlaid first, then skipped via a visited guard), reproducing the previous behavior. Any loaded config file may declare `COMMON.CONFIG_FILES` — an ordered dict of files and/or folders whose truthy/falsy value enables/disables each entry. When present it **replaces** the default seed (an explicit empty dict means "base only"), and each overlaid file's own `COMMON.CONFIG_FILES` **extends** the work-list, so the ramp-up continues while files remain (a missing/empty value ends that thread). Folders are expanded alphabetically; a referenced file that does not exist is skipped and reported as a **WARN log message** (new message code `BASEW07`, `Config file not found: {config_file}`) rather than raising — because `Config` is constructed before a logger exists, missing-file warnings are buffered in `Config.load_warnings` during load and flushed via the new `Config.log_warnings(logger)` (Architecture 6.1.4.5) once the app logger is created (wired into `app/base.py` `run()` and `test/tests/build.py` `run()`); name-prefixed (`_`/`.`) and `LOADME:false` files are still skipped; and expression evaluation + placeholder population run once, only after all overlays are applied. New helpers `_get_config_files` (6.1.4.2), `_apply_config_files` (6.1.4.3), and `_apply_config_file` (6.1.4.4) implement the work-list. Default wiring: `config/base.json` `COMMON.CONFIG_FILES = {app.json: true}` and `config/app.json` `COMMON.CONFIG_FILES = {local.json: true}`, preserving the base→app→local layering. New prep test `test/tests/base/config.py::test_config_files_ordering` (10/10 criteria) verifies declared-list replacement, declared ordering (last wins, beating alphabetical), work-list extension, disabled-entry skipping, and that a missing file is buffered and emitted as a `BASEW07` WARN log; existing `test_local_config_override` (5/5) confirms the default path. Full `app/base.py` run shows only the unrelated pre-existing `resolve_baseapp_source` (TST005) failure.

## Release Highlights (26.06.30.02)

- **First-class `PullBase` task type — managed, reviewable BaseApp-update pulls** (Feature 3.6; Requirement BASE-REQ-008.7): Consuming a BaseApp update is now a managed Task Manager task rather than a manual staging of base churn in the variant app. The Task Manager create pane (`resources/templates/task_manager.html`) moves the **task type selector above the title** and adds a **`PullBase`** option (also added to the edit pane); selecting `PullBase` makes the **title optional** (the server auto-fills `PullBase {YYYY-MM-DD}`) and shows an auto-fill hint. `scripts/task_manager.py` selects the worker instruction template **by task type** in `_build_copilot_prompt` (Architecture 3.6.16) using a new `CONFIG.APP.TASK_MANAGER.templates` map (`Feature`→`task.md`, `Bug`→`task.md`, `PullBase`→`pullbase.md`, with a `default` of `task.md`), and `_create_task` (Architecture 3.6.4) auto-fills the PullBase title — the type name and title format live as module constants (`PULLBASE_TASK_TYPE`/`PULLBASE_TITLE_FORMAT`/`PULLBASE_TITLE_DATE_FORMAT`), with no hard-coded literals in the logic. A new dedicated instruction file **`build/instructions/pullbase.md`** (Architecture 10.1.2) drives the PullBase lifecycle from the task details: confirm the task branch, run `pullbase` on it (auto-detecting the BaseApp source unless the task description overrides it), collect and review the incoming base updates, summarize them to the engineer as an HTML report to reach **Ready**, then deploy and merge under `base.md`'s supervised-merge model (deploy+merge together, or deploy-only then merge after a separate approval). `resources/manifests/pull.json` registers `pullbase.md` so variant apps receive it on pull. Tests in `test/tests/base/task_manager.py` cover type-based template routing, the `default` fallback, and the PullBase title auto-fill (explicit titles preserved; non-PullBase types never auto-titled). Verified live: the running Task Manager created a real `PullBase 2026-06-30` task through this feature.

## Release Highlights (26.06.30.01)

- **Robust flat & nested worktree migration, persisted layout flag, and one-step GitHub publishing** (Feature 3.8; Requirements BASE-REQ-014.18, BASE-REQ-014.20): The existing-deployment migration in `scripts/init_worktree.ps1` is hardened and gains optional remote publishing. `scripts/instantiate.py` now persists the deployment layout as `COMMON.WORKTREE` (a boolean) in the gitignored `config/local.json` (`true` for `--worktree on`, `false` for `off`), so migration can deterministically tell a flat deployment (`{APP}`) from a nested one (`{APP}/{branch}`). `init_worktree.ps1` reads that flag (absent ⇒ legacy flat; `.bare` presence as a secondary nested signal, with a structural fallback) and `Convert-ExistingDeployment` now separates the per-branch content directory from the `{APP}` container, so the **default** nested instantiate output (`{APP}/{branch}` with no `.bare`) migrates into the canonical `{APP}/.bare` + `{APP}/{branch}` layout instead of being double-nested; `WORKTREE=true` is recorded after a successful init. A rename-in-use fix moves both the PowerShell location and the Win32 current directory out of the content tree before relocating it (restoring them in a `finally`), eliminating the `Cannot rename … in use` failure when the script is run from inside the deployment's own `scripts` folder. A new optional `-remote` parameter publishes the migrated repo to GitHub via the `gh` CLI — verifying auth, creating the **private** repo when absent, wiring `origin` with the standard fetch refspec, and pushing all branches with upstream tracking — and accepts an `owner/name`, a bare name, or a full `https`/`ssh` clone URL (normalized to `owner/name` for `gh`). Note: an editor or Explorer window rooted at the `{APP}` container holds a recursive file-watch handle on the container directory and will make the rename step fail with access denied, so close the app folder in your editor before migrating. Verified end-to-end on flat and nested deployments, a no-remote regression run, and a real flat app migrated locally and synced to a throwaway private repo with the production remote untouched; init + instantiate pytests pass and full `app/base.py` EXIT=0.

## Release Highlights (26.06.29.03)

- **Task Manager UI saved views — flat, multi-tab sort/filter configurations** (Feature 3.6; Requirements BASE-REQ-014.16, BASE-REQ-014.17): The Task Manager UI (`resources/templates/task_manager.html`) gains a **views bar** directly below the status tabs that lets you save and re-apply named sort/filter configurations. Views are modelled as a single **flat array** of view objects `{ id, name, tabs:[...], sort:{col:dir,...}, filter:{col:[vals],...} }`: a view's `tabs` list associates it with one or more tabs (the `ALL` tab is just another tab name), `sort` is a compact ordered map enabling **multi-column** sort (key order = precedence), and `filter` is a compact map of column → array of values enabling **multi-value** filtering. On each tab every view renders as a chip but is **enabled** only when the current tab is in its `tabs` list (non-matching views are grayed out); a grayed user view offers a "+" to include the current tab, an enabled user view a toggle to remove it, and a grayed **built-in** view's "+" **clones** it into a new user view (built-ins are never modified). A **Save view** button stores the live sort/filters as a new user view auto-named from its filters and sort, and clicking an enabled chip applies its multi-column sort and multi-value filters and marks it active for that tab (double-click renames a user view, X deletes it). The Type/Priority filters became **checkbox multi-selects** that always offer the canonical values regardless of the data (`Feature/Bug/Chore/Doc`, `Critical/High/Medium/Low`), and the table now supports true multi-column sort with precedence indicators. Views persist **server-side**: built-in defaults ship in committed `config/base.json` under `APP.TASK_MANAGER.views` (flat array, tagged `builtin=true`, fully read-only), and the server layers the user's own views from `config/local.json` (app-local, git-tracked placeholder) then `config/machine.json` (machine-specific, git-ignored). `GET /api/views` returns `{ views:[builtins, then user views], active:{tab:viewId} }`; `PUT /api/views` persists only user views plus the per-tab active-view map to `config/machine.json` (atomic write, preserving all other keys). Switching tabs restores that tab's last active view. New Python helpers in `scripts/task_manager.py` (`_load_views`/`_save_views` and friends) and a suite of JS helpers in the template. Tests in `test/tests/base/task_manager.py` cover the flat model, multi-column/multi-value behaviour, canonical filter values, built-in read-only protection, and the GET/PUT endpoints.

## Release Highlights (26.06.29.02)

- **Task Manager UI "ALL" tab — a single view of every task** (Feature 3.6; Requirement BASE-REQ-014.15): The Task Manager UI (`resources/templates/task_manager.html`) gains an **`ALL`** tab placed **before** the status tabs and active by default, listing every task with no status division and carrying a total-count badge (`cnt-ALL`). The existing per-column sorting and filtering (ID, Title, Type, Priority) apply unchanged within the ALL tab. Because the ALL tab mixes statuses, the task list also exposes a **Status** column that is shown **only** in the ALL tab (hidden in the per-status tabs via a `show-status` class toggled on the table): its header is **sortable** (none → ascending → descending, sorting by lifecycle/status-tab order rather than alphabetically) and it offers an exact-match **filter** dropdown populated with the distinct normalized statuses present plus an "All" option. `ALL` is handled as a virtual tab that bypasses `normalizeStatus` (which would otherwise collapse it to `Other`); the canonical `STATUS_TABS` array is left intact. Implemented entirely in the single-page template (`getVisibleTasks` status-filter bypass, `compareCol` lifecycle-order status sort, `populateColumnFilters`/`initColumnControls` status filter, `updateBadges` `cnt-ALL`, `renderTable` `show-status` toggle + Status cell, and the `initTabs` handler); no server-side change. New test `test_task_manager_template_has_all_tab` in `test/tests/base/task_manager.py`. Build tests verified: build phase 2/2, 0 failures.

## Release Highlights (26.06.29.01)

- **Task Manager UI task search** (Feature 3.6; Requirement BASE-REQ-014.14): The Task Manager UI (`resources/templates/task_manager.html`) gains a task-search bar positioned **above the status tabs** that locates a task by its id. Submitting a query (via the Search button or Enter) matches task ids case-insensitively — an exact id match takes precedence, and otherwise a unique substring match is accepted, while an ambiguous substring match prompts the user to enter a more specific id. When a task is found, the UI switches the active status tab to the matched task's status, renders that list, selects and scrolls the matched row into view, and opens the task's details in the details pane; a no-match query reports an inline message and leaves the view unchanged. The search reuses the existing `normalizeStatus`/`renderTable`/`showDetail`/`refreshRows` logic via new `searchTask`/`findTaskById`/`activateStatusTab`/`initSearch` helpers. Architecture feature `11.2.2 resources.templates.task_manager` records the (previously un-architected) single-page UI template. New test `test_task_manager_template_has_task_search` in `test/tests/base/task_manager.py`. Build tests verified: 1 run, 0 failures.

## Release Highlights (26.06.26.07)

- **Task branches are named after the task id (no `task/` prefix)** (Feature 3.6.9; Requirement BASE-REQ-014.9): A task's short-lived branch is now named after the task id exactly (e.g. `BASE-TASK-260624-0001`) instead of `task/<id>`. Because a worktree's folder is derived from the branch's last path segment, the slash in `task/<id>` could instantiate the branch as nested sub-folders (a `task` folder containing `<id>`) and no longer matched the task's worktree folder (`{APP}/<task-id>`, which already had no prefix). `scripts/task_manager.py::_branch_name_for_task` now returns the sanitized task id directly — the same value used for the worktree folder — so the branch name, the worktree folder, and the task id are all identical, which also keeps branch folder names consistent in instantiated variant apps. `_task_branch_exists` derives its local/remote ref from the same helper, and the UI confirm dialog, `init_worktree.ps1` docs/examples, `sync_task_repo.ps1` comments, agent instructions (`base.md`, `app.md`), and the relevant tests were updated to match. Build tests verified: `task_manager` pytest suite passes; full `app/base.py` suite EXIT=0.

## Release Highlights (26.06.26.04)

- **README.md is now an app overview, with guardrails against changelog drift** (Feature 5.3.1.1.4; Requirement BASE-REQ-013.8): The top-level `README.md` was rewritten from a long per-release "Highlights" changelog into a current-state overview of the app (what it does, project structure, and how to set up / run / instantiate / pull base updates / build via the Task Manager). To stop it from drifting back into a changelog, the finalization instructions were hardened — `build/instructions/base.md` (deployment step 4e) and `build/instructions/app.md` now explicitly forbid `Highlights`/`Changelog`/`Release Notes`/`What's New` sections, release enumeration, and date/version-stamped headings in `README.md`, and require editing the overview in place (per-release history stays only in `docs/readme/{base,app}.md` and `build/updates/{base,app}.json`). A new advisory test `test.tests.base.base.test_readme_overview_only` (registered as the build-phase test `build_readme_overview_only` in `test/config/base.json`) scans `README.md` for those markers and reports a non-blocking **WARN** (never FAIL, since `fail_on=["FAIL"]`) so the convention is surfaced without breaking the build. Added architecture feature 5.3.1.1.4 and requirement BASE-REQ-013.8. Build tests verified: build phase 2/2, 0 failures.

## Release Highlights (26.06.26.03)

- **Sort and filter the Task Manager task list by each column** (Feature 3.6; Requirement BASE-REQ-014.13): The Task Manager UI (`resources/templates/task_manager.html`) task list is no longer static — it can now be sorted and filtered by each of its columns (ID, Title, Type, Priority). Clicking a column header cycles that column's sort through **none → ascending → descending**, with a ▲/▼ indicator marking the active sort column and direction (only one column sorts at a time); the Priority column sorts by **severity rank** (Critical &gt; High &gt; Medium &gt; Low) rather than alphabetically. A filter row under the headers restricts the visible rows: **ID and Title** filter by case-insensitive substring text, while **Type and Priority** filter by an exact value chosen from a dropdown populated with the distinct values present in the list (e.g. filter Type to Bug or Feature), each with an "All" option that clears the filter. Sorting and filtering act within the selected status tab and combine (filters first, then sort); when filters exclude every task the list shows "No tasks match the current filters." Implemented entirely in the single-page template (new JS `getVisibleTasks`/`compareCol`/`cycleSort`/`populateColumnFilters`/`initColumnControls`); no server-side change. New test `test_task_manager_template_has_column_sorting_and_filtering` in `test/tests/base/task_manager.py`.

## Release Highlights (26.06.26.02)

- **Each task runs in its own dedicated git worktree (bare-repo `{APP}/<branch>` layout)** (Feature 3.6; Requirement BASE-REQ-014.9): The repository now uses a **bare shared object store with every branch — including `main` — checked out as its own peer git worktree** under a single `{APP}` container (`{APP}/.bare` shared store, `{APP}/main` for `main`, `{APP}/<task-id>` for each task). This replaces the previous in-place `git checkout` model, where a single working tree could hold only one branch at a time and concurrent task agents would stomp on each other. The launcher `scripts/launch_task_agent.ps1` gains a `-Worktree` parameter that materialises (or reuses) the task's dedicated worktree and runs the worker session inside it; `scripts/task_manager.py` adds `_worktree_root`/`_worktree_path_for_task` (the worktree location is **fixed** at `{APP}/<task-id>`, a sibling of `main` — the parent of the main working tree — and is not configurable), and `_build_copilot_prompt`/`_build_review_prompt` take a `workspace_override` so the worker's `{workspace_root}` points at its worktree (also recorded on `worker_session`). The task's worktree is dissolved together with its branch when the task reaches `Done`. Agent instructions (`build/instructions/base.md`, `task.md`) document the bare `{APP}/<branch>` model. New worktree tests in `test/tests/base/task_manager.py`. Build tests verified: 1 run, 0 failures.

## Release Highlights (26.06.26.01)

- **Status-sync commits only the task ledger, isolated from the worker's working tree** (Feature 3.7; Requirement BASE-REQ-014.12): When the Task Manager applies a queued status-update request, the git sync (`scripts/sync_task_repo.ps1`, invoked by `scripts/task_manager.py::_sync_task_store_to_git`) now commits and pushes **only** the task ledger (`build/tasks/<app>.json`) in complete isolation from the worker's primary working tree. Previously the headless status-sync ran in the shared working tree — which, while a worker is active, is checked out on the worker's `task/<id>` branch with uncommitted changes — committing the ledger there and running `git pull --rebase --autostash`, which could sweep/push worker code ahead of the approval gate, drop uncommitted worker changes via autostash, and mix ledger-only commits with worker changes. The script now reads the server-updated ledger and applies the commit inside a dedicated, reusable **detached git worktree** built from the latest `origin/<branch>` tip, pushing to the configured ledger branch (`APP.TASK_MANAGER.ledger_branch`, default `main`); any rebase needed to integrate a moved remote runs inside that isolated worktree (which only ever contains the ledger change). It never runs `git add -A`/`commit -a` or a working-tree autostash/rebase, so the worker's working tree is never staged, stashed, rebased, or pushed. A new `-Branch` parameter selects the ledger branch, and `scripts/task_manager.py` adds `_resolve_ledger_branch` and a `branch` argument on `_sync_task_store_to_git` (the headless status-inbox sync passes the ledger branch). New regression test `test_sync_task_repo_isolates_ledger_commit_from_worker_tree` in `test/tests/base/task_manager.py`. Build tests verified: 1 run, 0 failures.

## Release Highlights (26.06.25.01)

- **Independent scrolling for the Task Manager's task list and task details** (Feature 3.6; Requirement BASE-REQ-014.11): The Task Manager UI (`resources/templates/task_manager.html`) now presents the task list and the selected task's details as two fixed, vertically-stacked blocks that each scroll on their own, instead of rendering the details below the list inside a single page-level scroll. The page body is a full-height flex column with hidden overflow; the content area holds a `.split` column containing a `.list-pane` (the task table, `overflow:auto`), a draggable `.split-divider` gutter, and a `.detail-pane` (the detail panel plus a placeholder, `overflow:auto`), with the panes sized by `flex-grow` 60/40. A new `initSplit()` handler drags the divider to reallocate space (ratio clamped 12%-88%), persists the chosen ratio in `localStorage['tm_split_ratio']`, and resets to the default 60/40 on double-click. `showDetail()`/`hideDetail()` toggle a placeholder ("Select a task above to view its details.") and reset the details pane to the top when a task is opened. New test in `test/tests/base/task_manager.py`. Build tests verified: 1 run, 0 failures.

## Release Highlights (26.06.24.06)

- **Durable file-store task status channel with a per-agent MCP enqueue server** (Feature 3.6.10/3.6.13; Requirement BASE-REQ-014.10): Because each task may be worked on its own branch while the Task Manager tracks only `main`'s ledger, worker agents no longer edit/commit the task ledger directly. Instead an agent requests every ledger change (status transitions and progress comments) by writing an atomically-written (write-then-rename) JSON request — following `build/tasks/update.template.json` (`{"TASKS":[{id,status,comments}]}`) — into a durable, configurable "task status store" (`APP.TASK_MANAGER.status_queue`/`request_polling_frequency`/`enable`). The Task Manager (`scripts/task_manager.py`) is the sole writer: `_status_inbox_watcher` (a daemon started by `run()`) continuously samples the store at the configured interval, and `_process_status_inbox` applies each request to a freshly loaded ledger by id via `_apply_task_update` (lists combine, dicts update recursively, scalars overwrite — no generic deep-merge), persists it, then stages/commits/pushes to `main` headlessly, moving each request to `processed/` or `failed/` exactly once (restart-safe; a server restart never blocks an agent). To remove hand-writing request files, each worker is given a per-agent stdio MCP server (`scripts/status_queue_mcp.py`, a thin enqueue writer over the same store) wired in by `scripts/launch_task_agent.ps1` via the Copilot CLI `--additional-mcp-config` hook (`_write_agent_mcp_config`), exposing an `enqueue_status_update(status?, comment?)` tool; the direct file write remains the documented fallback, and the dequeue side stays a direct filesystem read by the Task Manager (the enqueue server is never on the critical path). Task-manager directories are config-driven: `prompt_store` (worker `{task.id}.md` prompts and per-agent `.mcp.json`), `result_store` (agent HTML work summaries), and `status_queue`. `build/instructions/base.md`/`task.md` document the request-via-store workflow.
- **Live aggregate MCP server health check** (Feature 6.3.6): A live test (`utils/testutils.py::mcp_server_status`, registered as `live_mcp_server_status`, sampled every 60 seconds) discovers every configured MCP server from the per-agent `.mcp.json` files in the prompt store (falling back to the canonical server when idle), spawns each and verifies an MCP `initialize` handshake, and reports — to the console — how many servers are up/down and the active rate (up/total) plus the pending queue depth. The single aggregate result is logged as **GOOD** when no server is down, **WARN** when exactly one is down/unresponsive, and **FAIL** when more than one is down. A distinct `GOOD` log type renders in light green for test output. New tests in `test/tests/base/task_manager.py`. Build tests verified: 1 run, 0 failures.

## Release Highlights (26.06.24.05)

- **Branch-per-task workflow, `Deployed` status, and richer status tabs** (Feature 3.6.9; Requirement BASE-REQ-014.9): Each task is now worked on its own short-lived, ad-hoc `task/<id>` branch off `main` (never directly on `main`). The branch is created by the launcher (`scripts/launch_task_agent.ps1`, new `-TaskBranch` parameter) — not by the agent — after the system verifies whether the branch already exists and whether starting a worker is necessary. `scripts/task_manager.py` adds `_branch_name_for_task` and `_task_branch_exists`; `_start_copilot_for_task` passes `-TaskBranch` for work-mode workers and records the branch on the worker session; the `POST /api/tasks/<id>/start-agent` endpoint returns HTTP 409 (`requires_confirmation`) when a branch already exists unless the request opts in via `confirm_existing_branch`, and the UI prompts the engineer and retries on confirmation. The lifecycle adds a `Deployed` status between `Ready` and the terminal `Done` (`ToDo -> InProgress -> Ready -> Deployed -> Done`): `Deployed` means finalized and pushed to the task's own branch (not yet merged), while `Done` means fully integrated into `main` (branch merged and dissolved under the integration engineer's supervision), keeping `Done` terminal for backward compatibility. The Task Manager UI (`resources/templates/task_manager.html`) presents status tabs in the order `ToDo, InProgress, Specified, Ready, Deployed, Approved, Done`, the existing `Deleted` tab, and a new pathological `Other` catch-all tab that holds any task whose status is not one of the recognized statuses (unrecognized statuses normalize to `Other` instead of collapsing into `ToDo`). Agent instructions (`build/instructions/base.md`, `app.md`, `task.md`, `task-review.md`) updated to match. New tests in `test/tests/base/task_manager.py`. Build tests verified: 1 run, 0 failures.

## Release Highlights (26.06.24.04)

- **Task deletion with a `Deleted` status** (Feature 3.6.7; Requirement BASE-REQ-014.7): The Task Manager UI (`resources/templates/task_manager.html`) gains a `Delete Task` button and a dedicated `Deleted` tab. Backend support in `scripts/task_manager.py` adds `_delete_task` and a `DELETE /api/tasks/<id>` endpoint that soft-deletes a task to the `Deleted` status, or permanently removes it when it is already deleted. `_update_task` now restricts deleted tasks to status-only changes, and the start-agent and comments endpoints reject deleted tasks with HTTP 409. New tests in `test/tests/base/task_manager.py`. Build tests verified: 1 run, 0 failures.
- **Automatic git sync on Task Manager startup** (Feature 3.6.8; Requirement BASE-REQ-014.8): On startup, `scripts/task_manager.py::_sync_selected_app_on_startup` synchronously syncs only the currently selected app's task file with its own linked git repo (via `_sync_task_store_to_git` with the new `wait=True` flag, running `scripts/sync_task_repo.ps1` inline) before serving the UI, so the displayed task list reflects the synced state. The sync is skipped when no repo is linked, failures never block startup, and the `--no-startup-sync` flag disables it. `scripts/sync_task_repo.ps1` now rebases with `git pull --rebase --autostash` and aborts the push if the rebase fails. Copilot worker/review console window titles are kept task-specific as `<title> (<id>)` via a `TaskWindowTitleKeeper` thread in `scripts/launch_task_agent.ps1` that continuously re-asserts the title so the CLI's auto-generated name cannot stick. Build tests verified: 1 run, 0 failures.

## Release Highlights (26.06.24.01)

- **`Ready` task state and review activation** (Features 3.6.5, 3.6.6; Requirement BASE-REQ-014.6): Tasks now follow a `ToDo -> InProgress -> Ready -> Done` lifecycle. The agent instructions (`build/instructions/base.md`, `app.md`, `task.md`) require the Copilot worker to set a task to `Ready` (not `Done`) when its work is finished and to defer finalizing, committing, pushing, and marking `Done` until the engineer reviews and approves. The Task Manager UI (`resources/templates/task_manager.html`) gains a dedicated `Ready` tab. Activating Copilot on a `Ready` task re-focuses the original worker console window when it is still traceable (each worker records its pid + window title via `scripts/launch_task_agent.ps1`); if the window was closed or the work was done elsewhere, it launches a dedicated review session using the new `build/instructions/task-review.md` prompt (`_build_review_prompt`) that helps the engineer understand the changes and verify requirement fulfilment. New helpers `_process_alive` and `_activate_copilot_window` and a `POST /api/tasks/{id}/activate` endpoint back this flow in `scripts/task_manager.py`. Build tests verified: 1 run, 0 failures.

## Release Highlights (26.06.18.01)

- **Config file filtering by name and flag** (Feature 6.1.4.1): `Config.__init__` in `utils/baseutils.py` now skips config JSON files based on (1) naming convention (files starting with underscore `_` or dot `.` are skipped before opening) and (2) explicit flag (`LOADME: false` key skips after parsing). This allows users to disable config overrides without deleting files and follow naming conventions for temporary or development configs. Alphabetical load order is preserved for all active files. Build tests verified: 1 run, 0 failures.

## Release Highlights (26.06.17.01)

- **Error summary printout** (Feature 6.1.9.24): Printing error summary to console at close.

## Release Highlights (26.06.10.03)

- **Bug fix: `os.remove` on old log files wrapped with `try/except`** — `Logger._remove_old_files()` now catches `OSError` when removing old log files, so permission errors or file-in-use conditions are silently skipped rather than propagating and tripping the app. Deletion count only increments on success.

## Release Highlights (26.06.10.02)

- **Improved plain text rendering in HTML outputs** (Feature 6.1.6) — `HtmlDoc._format_text()` now converts actual `\n` characters to `<br>` tags and inserts `<br>` breaks before mid-text `#` section markers. Long prompt strings, instruction blocks, and other structured plain text are now visually separated and readable in HTML reports. `_render_cell()` delegates to `_format_text()` for all plain string values.

## Release Highlights (26.06.10.01)

- **Config overrides during instantiation** (Features 3.1.12, 3.1.6, 3.1.13) — `instantiate.py` now accepts an optional `--overrides` argument that takes a JSON file path or an inline JSON object string. The override dict is deep-merged into `config/app.json` after `APP_NAME` is set, so the new app is pre-configured without manual edits. `_deep_merge()` handles nested key merging without clobbering sibling keys. New message codes `INST010` (overrides applied) and `INSTW10` (failed to load overrides). New `BASE-REQ-008.6` with 4 breakdown items. New prep test `test_instantiate_config_overrides` (Feature 5.3.1.5.1) — 8 criteria covering file and string parsing, error handling, `APP_NAME` setting, override application, non-overridden key preservation, and deep merge correctness.

## Release Highlights (26.06.09.03)

- **Tasks grouped by type and status** (Feature 5.3.1.1.3) — Added `tasks_by_type_status` PROCESS step that produces a nested `{type: {status: [tasks]}}` dict from `tasks_df`. Tasks without an explicit `type` field are grouped under `Feature`. Output is split into per-type HTML reports via a new `tasks_by_type_status_split` OUTPUT entry. New `test_tasks_by_type_status` post test (Feature 5.3.1.1.3) validates the nested structure, bucket correctness, and default type handling. Architecture entries 5.3.1.1.2 and 5.3.1.1.3 added.

## Release Highlights (26.06.09.02)

- **`DataConverter` per-step error handling** (Feature 6.2.1.1) — `DataConverter.apply()` now wraps each conversion step in a `try/except`. Any exception (including missing source column `KeyError`) is caught, logged via `DATAE01`, and appended to `self.errors`. Execution continues with the next step. `self.errors` is reset at the start of each `apply()` call so successive calls do not accumulate. New `test_dataconverter_error_handling` prep test (Feature 5.3.1.3.6) covers 9 criteria. New `test_dataconverter_json_column` prep test (Feature 5.3.1.3.7) validates `json.loads` column parsing on an `ExtendedProps`/`Schema` demo dataset mirroring the `rdd_summary` CSV structure.
- **`AppManager.process_data()` dict source fix** — When a process step's source value is a `dict`, it is now passed directly to `DataConverter.apply()` instead of being silently replaced with an empty `DataFrame`. This allows `custom`-scope conversions that call `df.items()` to work correctly.
- New message code `DATAE01` added to `resources/message_codes/base.csv`.
- `BASE-REQ-004.4` added: robustness requirement for per-step exception handling in `DataConverter.apply`.

## Release Highlights (26.06.09.01)

- **Caller lineage depth from CSV** — Added `caller_depth` column to all four message code CSVs. Blank = default 2 levels; WARN/ERROR/FAIL codes = 4 levels. `Logger._lookup_entry` returns a `(text, type, caller_depth)` triple; `log()` uses the per-code depth automatically.
- **`instantiate.py` `--baseSource` flag** — Added optional `--baseSource <path>` argument so a copied `instantiate.py` can explicitly point at the BaseApp root. Improved the same-path error message to include both resolved paths and a clear usage hint.
- **`test_tasks_by_status` post test** (Feature 5.3.1.1.2) — Reads `results/results.json` and verifies every task appears in the correct status bucket of `tasks_by_status`. `AppManager.close()` now publishes `output_path` to the monitor so the test can resolve the results file location at runtime.
- **HTML hyperlinks** (Feature 5.3.1.3.5) — `HtmlDoc._as_hyperlink()` detects Windows absolute paths (`C:\…`), POSIX absolute paths (`/…`), and explicit URL schemes (`http://`, `https://`, `file:///`). `_render_cell()` wraps detected values in `<a href=…>` anchor tags with properly escaped display text. New `test_html_hyperlinks` prep test covers 9 criteria.
- Removed stray `from pandas import col` import from `utils/baseutils.py`.
- All prep and post tests PASS, exit 0.

## Release Highlights (26.06.08.02)

- Renamed `RESULT_MAP` → `OUTPUT_MAP` throughout. `OUTPUT_MAP` is now initialized as `{}` in `AppManager.__init__`, so it persists and is always present on every instance — including those created via `object.__new__()`.
- `store_outputs` deep-updates `OUTPUT_MAP` without resetting it between calls, enabling accumulation across multiple invocations.
- `base.py` uses a two-call pattern: first call stores all non-`OUTPUT_MAP` entries (populating `OUTPUT_MAP`), second call stores `output_map`/`output_map_html` entries using the fully populated map.
- `test_manager.store_outputs` is now scoped to only the keys declared in `test/config/base.json` OUTPUT, preventing it from overwriting any app-managed artifacts regardless of what the merged config contains.
- Fixed `test_output_delta` (Feature 5.3.1.3.1) to use `path_join` instead of `os.path.join` for paths used as manifest keys, ensuring forward-slash parity with the manifest on Windows.
- Renamed `config/base.json` output entries `result_map`/`result_map_html` → `output_map`/`output_map_html`.
- Updated `build/requirements/base.json` (`BASE-REQ-005.8`) and `build/architecture/base.json` (features `6.1.11.10`, `5.3.1.3.4`) to reflect the rename.
- All prep tests PASS.

## Release Highlights (26.06.08.01)

- `store_outputs` (Feature 6.1.11.3) now populates `self.RESULT_MAP` — a `dict` mapping each `output_key` to the list of file paths written or confirmed present in delta-skip mode — and returns it so callers can use the mapping directly.
- `_save_output_artifact` returns the full output path whether the file was written or skipped; `_store_one_output` returns a `list` of those paths.
- Added `result_map` output entry to `config/base.json` (`source: RESULT_MAP`) so the mapping is also persisted as a JSON artifact.
- Added prep test `test_output_file_mapping` (Feature 5.3.1.3.4): 7 criteria covering dict type, key completeness, non-empty string lists, on-disk path existence, delta-skip retention, concurrent mode parity, and return-value identity.
- Added requirement `BASE-REQ-005.8` and architecture features `6.1.11.10` and `5.3.1.3.4`.
- All 8 prep tests PASS.

## Release Highlights (26.06.06.05)

- Added `resources/manifests/drop.json` (Feature 11.3.3): lists 9 deprecated folder paths that existed in older variant apps before the v26.06.06.01 file tree refactor (`docs/instructions`, `docs/tasks`, `docs/requirements`, `docs/architecture`, `docs/message_codes`, `docs/templates`, `docs/manifests`, `docs/version`, `updates`).
- Added `load_drop_entries()` (Feature 3.2.15) and `drop_deprecated_paths()` (Feature 3.2.16) to `scripts/pullbase.py`. On every base pull, after syncing pull and once files, pullbase now reads the `drop` key from all manifest files and removes any matching paths in the local app root. Logged with new message codes `PULL009` and `PULL010`.
- Build test 25/25 PASS.

## Release Highlights (26.06.06.04)

- Fixed `scripts/setup_env.ps1` (Feature 3.4): replaced `pip.exe` with `python -m pip` in both `Get-InstalledPackageNames` and `Install-MissingPackages`. The `pip.exe` launcher embeds the absolute path to the Python interpreter used at venv creation time; if that path changes (e.g. an OneDrive folder sync moves the source), the launcher fails with *Unable to create process*. Using `python -m pip` bypasses the launcher entirely. Renamed the `PipExe` parameter to `PythonExe`; removed the `$venvPip` variable.

## Release Highlights (26.06.06.03)

- `build.updates` renumbered from Feature 7 to Feature 10.7 in the architecture inventory, placing it correctly under the `build` Feature 10. Sub-features are now 10.7.1 (`build/updates/base.json`) and 10.7.2 (`build/updates/app.json`). The duplicate top-level Feature 7 is removed.
- Added Unicode stdout/stderr reconfiguration to `utils/baseutils.py` so log output is safe on Windows cp1252 consoles.
- Build test 25/25 PASS.

## Release Highlights (26.06.06.02)

- Moved `docs/version/` into `resources/version/`, completing the consolidation of all shared runtime data under `resources/`. `docs/` now holds only `readme/`.
- Updated `resources/manifests/pull.json` and `resources/manifests/once.json` to reference `resources/version/base.txt` and `resources/version/app.txt` respectively.
- Updated architecture inventory: `resources.version` (Feature 11.4) added with `11.4.1` (`base.txt`) in `build/architecture/base.json`; `11.4.2` (`app.txt`) in `build/architecture/app.json`. Feature 4 (docs) now covers only `readme/`.
- Build test 25/25 PASS.

## Release Highlights (26.06.06.01)

- Refactored the workspace file tree to consolidate build-related and runtime data resources into dedicated top-level directories (`BASE-REQ-013`).
- New `build/` directory consolidates: `instructions/` (agent/developer guidance), `tasks/` (task tracking), `requirements/` (requirements definitions), `architecture/` (feature inventory), and `updates/` (update payloads). These were previously scattered under `docs/` and the root `updates/` folder.
- New `resources/` directory consolidates: `message_codes/` (CSV catalogs), `templates/` (HTML rendering templates), and `manifests/` (pull and once manifests). These were previously under `docs/`.
- `docs/` now holds only `readme/` and `version/`, keeping it focused on human-readable documentation and version markers.
- `pull.json` entries use a `target` field for destination overrides; `once.json` entries use a `destination` field.
- `scripts/pullbase.py` and `scripts/instantiate.py` now read `destination` (with fallback to `target`) for once-manifest entries, so existing variant app files are never overwritten on pull.
- All configuration paths, manifest entries, test fixtures, and architecture inventory updated to the new locations. Build test 25/25 PASS.

## Release Highlights (26.06.05.01)

- `to_json_compatible` (Feature 6.1.17) is now a module-level function in `utils/baseutils.py`, promoted from a nested helper inside `save()`. It recursively converts `Params`, `dict`, `list`/`tuple`, `pd.DataFrame`, `np.ndarray`, NumPy scalars (`np.floating`, `np.integer`, `np.bool_`), `pd.NA`, and `float` NaN/Inf to JSON-serializable primitives. `save()` calls it unchanged. All code that needs this conversion can now import it directly.
- Added prep test `test_to_json_compatible` (Feature 5.3.1.3.3) with 9 criteria covering: module-level import, `Params` expansion, `np.floating` NaN/value, `np.integer`, `np.bool_`, `pd.NA`, `DataFrame` conversion, and final `json.dumps` serializability — all PASS.
- Fixed `test_deployment` (Feature 6.3.5) in `utils/testutils.py` to pass `sys.executable` as the `-Python` argument when spawning `test_deployment.ps1`, so the correct venv Python is used. Build test now 25/25 PASS.
- Added `BASE-REQ-005.7` requirement capturing the serialization helper promotion.

## Release Highlights (26.06.04.07)

- Added `scripts/setup_env.ps1` (Feature 3.4): creates or reuses the Python virtual environment (`.venv`) and installs only missing packages from `dependencies/base.txt` and `dependencies/app.txt` using pip. Helper functions `Get-InstalledPackageNames` (pip list → hashtable) and `Install-MissingPackages` (per-file delta install) keep install time minimal. Accepts `-AppRoot` and `-Python` parameters; exits 0 on success, 1 on failure.
- Added `scripts/deploy.ps1` (Feature 3.5): deployment ceremony script with three sequential phases — ENVIRONMENT (delegates to `setup_env.ps1`), DEPLOYMENT TEST (runs `scripts/test_deployment.ps1`), and BUILD TESTS (runs `test/tests/build.py` via the venv Python). Reports per-criterion `[PASS]`/`[FAIL]` lines and an overall PASS/FAIL summary; aborts on environment failure. Accepts `-AppRoot`, `-Python`, and `-KeepTemp` parameters.
- `scripts/instantiate.py` now calls `run_setup_env(target_root)` after completing file operations (Feature 3.1.11): resolves `setup_env.ps1` from the scripts folder and invokes it non-interactively; missing script logs a WARN and continues.
- `scripts/pullbase.py` now calls `run_setup_env(local_root)` after completing pull operations (Feature 3.2.14): same pattern as instantiate, ensuring the virtual environment is refreshed after every base pull.
- Merged `BASE-REQ-011` (deployment validation script) into `BASE-REQ-012` as sub-requirement `012.8` with breakdown items `012.8.1`–`012.8.6`; added Feature `3.3.1` to BASE-REQ-012 solution.

## Release Highlights (26.06.04.06)

- Refactored `config/requirements.txt` into `dependencies/base.txt` and `dependencies/app.txt`. Base dependencies (numpy, scipy, pandas, tqdm) are now declared in `dependencies/base.txt` and pulled to every variant app on each base update. App-specific additions live in `dependencies/app.txt`, which is copied once on instantiation.
- Updated `docs/manifests/pull.json` to pull `dependencies/base.txt` and `docs/manifests/once.json` to copy `dependencies/app.txt` on first instantiation.
- Added `dependencies` folder as top-level Feature `9` (with `9.1` = `base.txt`, `9.2` = `app.txt`) in `docs/architecture/base.json`; removed stale Feature `2.3` (`config/requirements.txt`) and updated the `config` folder description.

## Release Highlights (26.06.04.05)

- Added `test/tests/build.py` (Feature 5.3.2): a standalone build-phase test runner. It loads runtime config (`config/base.json`) and test config (`test/config/base.json`), merges them via `build_test_config`, creates a `TestManager`, runs `run_phase("build")`, stores test outputs, and returns exit code 0 on pass or 1 on failure. Designed to be invoked during deployment ceremonies.
- Added `test_deployment` (Feature 6.3.5) to `utils/testutils.py`: resolves the configured `script_path` relative to `base_dir`, spawns `pwsh -NonInteractive -File <script>` via `subprocess`, parses each `  [PASS] name` / `  [FAIL] name -- detail` line from stdout as a structured criterion, and returns a `TestManager`-compatible result dict including `status`, `criteria`, `features`, and `data`.
- Fixed `TestManager.run_a_test` to initialize the per-phase results slot on demand when the phase is not one of the pre-seeded names (`prep`, `live`, `post`, `monitor`). This allows any new phase (`build`, etc.) to store results without modification to `TestManager.__init__`.
- Added `BASE-REQ-007.5` requirement capturing the build phase test runner capability.
- Added `TST008` message code for build phase test runner messages.
- Added architecture features `5.2.1.4` (build dataset in test config), `5.3.2` / `5.3.2.1` (build runner module and run function), and `6.3.5` (test_deployment function).

## Release Highlights (26.06.04.04)

- Added `scripts/test_deployment.ps1` (Feature 3.3.1): a standalone PowerShell integration test that exercises the full BaseApp deployment pipeline. The script runs four sequential phases — pre (source validity), instantiate (`scripts/instantiate.py`), pullbase (`scripts/pullbase.py` from the TestApp), and post (final state verification) — reporting per-criterion PASS/FAIL across 25 checks. Accepts optional `-BaseAppRoot`, `-Python`, and `-KeepTemp` parameters. Temp folders are cleaned up automatically.
- Added requirement `BASE-REQ-013` with full breakdown and solution traceability.

## Release Highlights (26.06.04.03)

- `store_outputs` now writes artifacts concurrently via `ThreadPoolExecutor`. Set `CONFIG.COMMON.OUTPUT_WORKERS` (default 8) to control the worker count; values of 0 or 1 retain the original sequential behaviour.
- New private method `_store_one_output` (Feature 6.1.11.9) encapsulates the resolve/convert/save logic for a single artifact, making it callable from both the sequential loop and the thread pool.
- `_output_manifest_lock` (`threading.Lock`) added to `AppManager.__init__` to serialise all reads and writes to `output_manifest` across concurrent worker threads.
- `_save_output_artifact` acquires the lock when checking and updating manifest entries, ensuring delta-mode integrity under concurrency.
- Per-worker exceptions are caught and logged as `BASEW06` (WARN) without aborting remaining writes.
- Added prep test `test_concurrent_store_outputs` (Feature 5.3.1.3.2) with 6 criteria covering sequential output, concurrent output, content parity, error isolation, and lock initialisation.
- Added requirement `BASE-REQ-005.6` with full breakdown and solution traceability.

## Release Highlights (26.06.04.02)

- `pullbase.py` now applies `once` manifest entries when run against an existing variant app. Two new functions handle this: `load_once_entries` (Feature 3.2.12) reads `once` entries from all synced manifest files, and `pull_once_files` (Feature 3.2.13) copies each source file or directory to the local app only when the destination does not already exist — preserving any app-specific customisations.
- `main()` merges missing-source paths from the once pull into the same `PULLW07`/`PULLW08` warning flow used by the regular pull, and prints a summary line reporting how many once files were synced vs skipped.
- Fixed a message-code typo: `PULLE07` → `PULLW07` (missing sources detected is a warning, not an error).
- Added message codes `PULL007` (Loaded once entries) and `PULL008` (Once pull synced files).
- Added prep test `test_pullbase_once_sync` covering all nine criteria across pre/live/post phases for features 3.2.12 and 3.2.13.
- Added requirement `BASE-REQ-008.5` capturing the once-pull propagation requirement with full breakdown and solution traceability.

## Release Highlights (26.06.04.01)

- `Logger._write_csv` now operates in append-only mode: a `_csv_written_count` counter tracks how many entries have been flushed so only new log entries are appended on each call, eliminating the full file rewrite that occurred on every `log()` call. Dict/list column values are serialised to JSON strings before DataFrame construction.
- `AppManager._to_raw_data` no longer attempts to auto-parse string values that happen to start with `{` or `[` via `json.loads`. Strings are returned as-is; callers own string semantics. This removes the false-positive BASEW11 warnings that fired on Python expression strings and config template values.
- `DataConverter.apply` now supports a `custom` scope that evaluates the `op` expression against the converter's context dict and returns the raw result directly, bypassing the DataFrame column-assignment path.
- Removed `BASE011` (JSON text parse summary) and `BASEW11` (Failed to parse JSON text field) message codes — no longer generated.

## Release Highlights (26.06.03.03)

- Added config-driven task report generation with status-based grouping using the built-in `PROCESS` mechanism.
- Tasks are grouped by status field through custom data conversion expressions that transform raw loaded task dictionaries into status-keyed groups.
- New process step: `tasks_by_status` uses custom conversion scope to group tasks from multiple source files.
- New output configurations: `tasks_by_status_split` (JSON) and `tasks_by_status_html` (HTML) render each status group to separate files under `{$OUTPUT_PATH$}/tasks/reports/`.

## Release Highlights (26.06.03.02)

- Added `requirements` and `tasks` to runtime INPUT so BaseApp loads both catalogs during normal execution.
- Added `requirements_split_html` and `tasks_split_html` outputs so both catalogs are rendered to HTML under `{$OUTPUT_PATH$}`.
- Extended test post-checks to validate `requirements_loaded` and `tasks_loaded` monitor flags.

- Added requirement and task artifacts to the framework.
- `docs/manifests/pull.json` now pulls `docs/requirements/base.json`, `docs/requirements/req-eng-instructions.md`, `docs/tasks/base.json`, and `docs/tasks/template.json`.
- `docs/manifests/once.json` now provisions `docs/requirements/app.json` and `docs/tasks/app.json`.
- Created  requirements with reverse-engineered base and app requirement hierarchies.
- Added `requirements` and `tasks` to runtime INPUT so BaseApp loads both catalogs during normal execution.
- Added `requirements_split_html` and `tasks_split_html` outputs so both catalogs are rendered to HTML under `{$OUTPUT_PATH$}`.

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
- `conversions` is applied sequentially through `DataConverter`.
- `DataConverter` supports an injectable logging function and defaults to
  `print` when verbose logging is enabled.
- `df`-scope conversions do not need their own `target`; the step-level
  `target` controls where the final processed DataFrame is stored.
- Conversion scope can be `df` for DataFrame operations or `custom` for arbitrary
  Python expressions that transform any data shape (dict, list, etc.).
- Process outputs are stored back on `results` using the step id unless a
  `target` override is provided.

BaseApp includes example steps:

1. `message_codes_df` — uses `df` scope to concatenate all loaded message code CSVs
   from the `message_codes` context mapping into a single DataFrame and adds a
   `source_file` column.

2. `tasks_grouped_by_status` — uses `custom` scope with a dictionary comprehension
   to group all tasks loaded from multiple source files by their `status` field.
   The expression iterates through each source file's TASKS array and collects
   tasks into a status-keyed dictionary. Output is stored as `tasks_by_status`
   and rendered via split outputs to separate JSON/HTML files per status.

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
python scripts/instantiate.py --root .. --appName MyNewApp
```

Behavior:

- Reads all `.json` files under `resources/manifests`.
- Applies entries under `pull` by copying on every instantiate run.
- Overwrites core/base files on repeat runs.
- Applies entries under `once` only when destination files do not already
  exist, including `resources/version/app.txt`.
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
python scripts/pullbase.py --baseSource ../../BaseApp  # explicit source path
```

### Normal mode

Syncs BaseApp `resources/manifests` to local `resources/manifests`, then applies all
entries under the `pull` key from all manifest files in that folder.
To add or adjust pull behavior, update manifest files in `resources/manifests`.
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

Current manifest (`resources/manifests/pull.json`):

The following are current `pull` sources:

- `app/base.py`
- `config/base.json`
- `build/updates/base.json`
- `build/architecture/base.json`
- `resources/manifests/pull.json`
- `scripts/pullbase.py`
- `utils/baseutils.py`
- `utils/datautils.py`
- `utils/testutils.py`
- `build/instructions/base.md`
- `build/instructions/app.md`
- `resources/version/base.txt`
- `docs/readme/base.md`
- `resources/templates/dataset_table.html`
- `resources/message_codes/base.csv`
- `resources/message_codes/logger.csv`
- `resources/message_codes/test.csv`
- `dependencies/base.txt`
- `scripts`
- `test/config/base.json`
- `test/tests/base`

### Hard mode (`--hard`)

Copies every file from BaseApp (excluding `__pycache__`, `.git`, `.venv`,
`.pyc`, `.pyo`), overwriting all local files including app-specific placeholders.
Use this to fully reset an app to the base.

## Builder Workflow (Recommended)

1. Instantiate once: `python scripts/instantiate.py --root .. --appName MyNewApp`
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
mirrored in `resources/version/base.txt`.

App-specific version is tracked in `resources/version/app.txt` and is created once
during instantiation so it remains app-owned during future base pulls.

- Version format: `YY.MM.DD.NN` (two-digit year, month, day, sequence number)
- Rule: every BaseApp modification increments the version.
- Changelog: `updates` array in `updates/base.json`, newest entry first.
- Latest: `26.06.02.03` brings the architecture compliance test to full alignment
  (0/0/0), expands the base architecture inventory to cover all public
  scripts/utils helpers, leaf docs files, and the `updates/` folder; teaches
  the comparator to skip dotfiles/dot-folders and `__init__.py` and to resolve
  `file.json::dotted.key` JSON-pointer paths; adds caller lineage (file,
  module, class, function, line) to every `Logger.log` entry; and routes test
  outputs to a dedicated `TESTS_RESULTS` subfolder so test runs no longer
  overwrite the main app's `config.json` / `config.html`.

## Agent Guidance

Agent-specific development instructions are stored in:

- `docs/instructions/base.md` for base app rules
- `docs/instructions/app.md` for generic variant-app rules
