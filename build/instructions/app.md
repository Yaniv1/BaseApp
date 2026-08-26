# App Instructions
## These instructions apply when working on the variant app

These instructions define how you must work on a variant app of BaseApp.


## Task Selection

If you're asked to work on a specific task, create a task for anything you're asked to do, using the task template in `build/tasks/template.json`. Create the task in `build/tasks/app.json`.

If you're asked to work on any open task, follow the task list in `build/tasks/app.json`. Prioritize tasks by priority and progress.
Keep working until there are no open tasks. 

## For every task you're working on:

Prioritize tasks by priority and progress.
Update the task list when significant progress is made on a task.
Use the task comments to update progress details.

For every task that includes a system enhancement or modification:

Clearly state the system requirement in the appropriate location in the hierarchy of requirements in `build/requirements/app.json`

Follow the requirements engineering instructions in `build/architecture/req-eng-instructions.md`

For maintenance requirements, there is typically no need to change the requirements or the architecture, unless it's clearly stated that the fix requires architecture changes.

Follow all the instructions in `build/instructions/base.md` but notice that wherever the instructions refer to a base resource (e.g. `app/base.py`, `config/base.json`, etc.) - switch here to the corresponding app resource ( `app/app.py`, `config/app.json`, etc. )

Follow the same task lifecycle as the base app: `ToDo` -> `InProgress` -> `Ready` -> `Deployed` -> `Done`. When you finish the work, set the task to `Ready`, commit the changes to the architecture, requirements, tests, implementation but do not update the versioning and documentation yet.
After engineer approval move it to `Deployed`: finalize, commit, and push to the task's own short-lived branch (not merged into `main` yet).
Work each task on its own short-lived, ad-hoc branch named after the task id (e.g. `{task_id}`, with no `task/` prefix so the branch name matches its worktree folder) created off the latest `main`, never directly on `main`. A task reaches `Done` only after its branch is merged into `main` and dissolved, with the integration engineer supervising the merge to make sure there are no unresolved conflicts; `Done` therefore means fully integrated into `main`.

Avoid modifications to base app artifacts, these should not be modified by the app. If behavior changes are needed, inherit and override in app artifacts (`app.py`,`apputils.py`, `app.json`, etc.)

When logging, utilize logger functionality - use message codes instead of text, if necessary add new message codes to `messages/app.csv`. report variable values using data and not in message.

If a generic functionality is needed, submit a task into the `build/tasks/base.json` file so that the functionality will be implemented as a base functionality and be available to all apps utilizing this framework.


## Working with AI/model-driven features

When a feature relies on a language model or other generative/AI component:

- **The model generates content; code owns structure.** Never let the model decide counts, positions, labels, identifiers, or any invariant, and never let it classify or label its own output — that makes it the judge of its own work. Assign labels and enforce constraints deterministically in code.
- **Generate distinct classes/partitions in isolation.** Producing multiple classes in one context lets the model self-decide boundaries, contaminating and correlating them. Generate each class separately with its own rubric and attach labels in code.
- **Derive vocabularies from authoritative sources, stay data-agnostic.** Labels, categories, and allowed values should come from the governing data description (schema, taxonomy, RDD), not hard-coded lists or model inference. Config carries only agnostic knobs (rates, limits, namespace prefixes).
- **Verify semantics on real artifacts.** Models produce plausible-but-wrong output that passes schema and unit checks. Always open a real generated result and confirm the content actually carries what it claims.
- **Ground with verified examples where fidelity matters.** Prefer retrieval of vetted examples (RAG/similarity) over free generation when correctness is important.
- **Guard diversity in code.** One-item-at-a-time generation can silently duplicate; batch generation or deterministically de-duplicate where variety is required.


Update the app's README.md file and use it to provide an overview of the app and its capabilities, functionalities, and features. **`README.md` is a current-state overview, never a changelog:** edit the overview sections **in place** and do not add per-release/per-version/per-task entries. It must not contain `Highlights`, `Changelog`, `Release Notes`, or `What's New` sections, must not enumerate releases, and must not carry date- or version-stamped headings — all release history belongs only in `docs/readme/app.md` and `build/updates/app.json`. The advisory `WARN`-only test `test_readme_overview_only` flags drift back into a changelog.