# BaseApp Agent Instructions
## These instructions apply when working on the base app

These instructions define how you must work on BaseApp.

## Task Selection

If you're asked to work on a specific task, create a task for anything you're asked to do, using the task template in `build/tasks/template.json`. Create the task in `build/tasks/base.json`.

If you're asked to work on any open task, follow the task list in `build/tasks/base.json`. Prioritize tasks by priority and progress.
Keep working until there are no open tasks. 

## For every task you're working on:

Change the task's status from `ToDo` to `InProgress` when you start working on it. Do this by submitting a status-update **request** to the task status store (see "Task Status Updates" below) — do **not** edit or commit the task ledger yourself; the Task Manager server applies your request to the authoritative ledger on `main`.

Update the task list when significant progress is made on a task.
Use the task comments to update progress details.

## Task Status Updates: request via the `enqueue_status_update` tool

The task ledger (`build/tasks/<app>.json`) is owned and written **only** by the Task Manager server, on the `main` branch. Because a task may be worked on in its own branch, any status change you commit on a branch would be invisible to the Task Manager (which tracks `main`). Therefore:

- **Do not** edit or commit the task ledger to change a task's status or to add task comments. Leave the ledger to the server.
- Instead, **request** every task-ledger change (status transitions and progress comments) with the **`enqueue_status_update`** tool from the `task-status-queue` MCP server. The task id is taken from your environment, so you only pass the fields you are changing — `status` (the new status, which replaces the current value) and/or `comment` (a single progress comment, appended). Provide at least one. Example: `enqueue_status_update(status="InProgress", comment="Started implementation.")`.
- The tool is a thin writer over a **durable** task status store: each call enqueues a request file, and the Task Manager server **continuously samples** the store and applies pending requests to the ledger on `main` (matching the task by `id`, then committing and pushing). If the server is momentarily down or restarts, the request simply waits in the store and is applied once the server is back — so a server restart never blocks your progress. Each request is processed exactly once.
- **Fallback (only if the `enqueue_status_update` tool is not available):** write the request yourself as a single JSON file, written atomically (temporary name, then rename), into the store's pending area (the `{status_store}` value in your `task.md` prompt). Its content follows the task update template (`build/tasks/update.template.json`): a `{"TASKS":[ ... ]}` document where each item is identified by its `id` and carries only the fields you are changing — `status` and/or `comments` (a single comment item). Give each file a unique name, e.g. `<task-id>-<utc-timestamp>.json`.

You still commit your **code, requirement, architecture, design, and test changes** on the task branch as usual — only the **task ledger** (status + task comments) is updated through the status store rather than by you.

## Branch Strategy: one short-lived branch (and worktree) per task

Do not do task work directly on the long-lived `main` branch. The repository uses a **bare shared object store with every branch checked out as its own git worktree** under a single `{APP}` container directory (the bare store lives in `{APP}/.bare`; `main` is the worktree `{APP}/main`; each task gets a sibling worktree `{APP}/<task-id>`). Each task is therefore worked on in its own short-lived, ad-hoc branch checked out into its own dedicated worktree alongside `main`, so that concurrent tasks never mix their changes together and multiple task agents can run in parallel.

1. When a task is started (moving it from `ToDo` to `InProgress`), it is worked on in its own dedicated short-lived branch named after the task id, e.g. `BASE-TASK-260624-0001` (no `task/` prefix, so the branch name matches its worktree folder name exactly and never nests into sub-folders), branched off the latest `main`. You (the agent) do **not** create this branch or worktree yourself: the launcher `scripts/launch_task_agent.ps1` materialises a dedicated worktree at `{APP}/<task-id>` (always a sibling of `main` under the `{APP}` container, i.e. the parent of the main working tree — a fixed location, not configurable) and checks the branch out there (via its `-TaskBranch` and `-Worktree` parameters) before your session starts, after first verifying whether such a branch already exists and whether starting a worker is necessary. A git worktree is required because a single working tree can only have one branch checked out at a time, which would otherwise prevent parallel task agents; placing every branch (including `main`) as a peer worktree under `{APP}` keeps each one visible in its own editor/source-control window. If a branch already exists, the task may already be (or have been) worked on, so the Task Manager does not silently start another worker: it asks the engineer to confirm, and the launcher proceeds only when the engineer opts in. By the time you begin you are already inside the task's worktree on the task branch — just confirm you are not on `main`.
2. Do the task's work — requirements, architecture, implementation, tests — on that branch (inside your worktree), and push the branch (not `main`) when you commit and push. Task-ledger status changes and task comments are **not** committed on the branch: request them through the task status store and the Task Manager server applies them to the ledger on `main` (see "Task Status Updates").
3. The branch and its worktree are short-lived: they exist only for the duration of the task and are dissolved (the branch deleted and the worktree removed) once the task is merged into `main` and reaches the `Done` status.
4. The merge of the task branch into `main` is supervised by the integration engineer, who makes sure there are no conflicts that must be resolved before the merge can complete successfully. Do not merge into `main`, dissolve the branch, or remove the worktree yourself; that happens at the `Done` step, under the integration engineer's supervision.

## Task Lifecycle: ToDo -> InProgress -> Specified -> SpecApproved -> Ready -> CodeApproved -> Deployed -> BuildApproved -> Done

Tasks move through the following steps.

1. *Task Creation* Creates the task and specifies the title, description, type, priority, and additional attributes. This is done by the engineer who can use the UI, a powershell call, or manually edit the tasks file. At the end of this process, the status of the task is `ToDo`.

2. *Task Specifying*: This step is triggered by the engineer, and accomplished by you. If you are asked to work on a task:
        a. Request a status change to `InProgress` via the task status store (do not edit/commit the ledger yourself).
        b. Make sure that you are working in the dedicated worktree for the task, on the task branch, and not on the `main` branch. You do **not** create this branch or worktree — the launcher `scripts/launch_task_agent.ps1` already created the worktree (a sibling of `main` under the `{APP}` container, `{APP}/<task-id>`) and checked out `<task-id>` there (via its `-TaskBranch` and `-Worktree` parameters) before your session began, after verifying whether the branch already existed and whether starting a worker was necessary (asking the engineer to confirm when a branch already existed). Just verify you are in the task worktree on the task branch.
        c. Request a comment to be appended to the task (via the task status store) noting that you started working on the task.
        d. Do not commit or push the task ledger; the server applies your status/comment requests to the ledger on `main`. (Commit your code/spec/test changes on the task branch as you progress.)
        e. Task Breakdown: break down each task into simple sub-tasks, including requirements specification tasks, architcture adjustment tasks, solution design tasks, implementation tasks, testing tasks, and deployment verification tasks. Execute the sub-tasks one by one. There is no need to document the simpler tasks in the tasks file. This is mainly for better agentic flow. But you can update in the comments about each task what you achieved and that would reflect the task breakdown.
        **Begin working on systems engineering:**
        f. Complete requirements specification. Requirements Engineering Guidance: For every task that includes a system enhancement or modification:
                f1 Clearly state the system requirement in the appropriate location in the hierarchy of requirements in `build/requirements/base.json`.
                Each task must have a running enumerator, `id`, a concise `title`, and an imperative `description`.
                f2 Follow the requirements engineering instructions in `build/architecture/req-eng-instructions.md`.
                f3 For maintenance requirements, there is typically no need to change the requirements or the architecture, unless it's clearly stated that the fix requires architecture changes.
        g. Complete architecture specification / modifications:
                g1 Use `build/architecture/base.json` for BaseApp items including folders. Use `build/architecture/app.json` for Variant App items and placeholders for future extension by the variant app designer. Identifiers must be unique across both files. For example, the `app` folder is listed as Feature #1 in `build/architecture/base.json` and as feature #1 in `build/architecture/app.json` without additional details (only for scaffolding) except for its sub-features. Then `app.py` is listed as Feature #1.2 in `build/architecture/app.json` under Feature #1. There is a separate mechanism for reconciliation between the two files.
                g2 The architecture file is a structured hierarchical list of app features. A feature is defined as a functional or structural characteristic.        
                g3 Each node in the architecture under the `features` node is a `feature_id`: `feature_dict` pair for complex features or a `feature_id`: `feature_name` for simple features. 
                g4 A complex feature's feature_dict includes `name`, `path`, `description`, `type`, and nested `features`.
                g5 The `feature_id` is a hierarchical dot-chained enumeration of items. For example, the `feature_id` of the `Logger` class in the `baseutils` package is `6.1.9` because it is item #9 in item #1 in item #6 in the app's directory. 
                g6 The `name` is a hierachical dot-chained name string that represents the lineage of the item. For example, the Logger's name is "utils.baseutils.Logger" because it sits in the baseutils package which sits in the utils sub-directory.
                g7 The `path` is an accurate relative path under the app folder. beyond the file name, items in the file are added to the path after a `::` separator.
                g8 If multiple files in a folder have the same basename add the extension with an underscore to avoid confusing dots for separators. for example if the same folder has `base.json` and `base.csv` used `base_json` and `base_csv`.
                g9 The `description` is a concise summay of the feature, which can also be adapted from the docstring. There is no need to include every nuance and every secondary or tertiary specific refinment or enhancement - just the main and core functionality/behavior. Details can be provided in the descriptions of sub-features.
                g10 The `type` is a dot-chained type string that represents the type lineage (combination of Folder, PyFile, JsonFile, MdFile, CsvFile, Class, Method, Function, DataSet, etc.) For example, the logger's type is `Folder.PyFile.Class` becasue it is a class inside a python file inside a folder.
                g11 Features can be hierarchical: Each feature can contain other features.
                g12 The number of items in the type chain must match the number of items in the name chain. do not add sub-types unless they map to named features.
                g13 As you update the feature list, you should also add the identifier of the feature as a comment in the file but only if the format permits it in a way that does not change the semantics of the file. For example, python code supports comments, but json and csv files do not. MD files support comments but these comments are considered part of the text that the reader sees.
                g14 Use the architecture/temp items to update the archiecture based on suggested corrections made by the architecture compliance test.
                g15 Architecting and Design Guidance:
                        1. Maximize reuse and avoid changing the architecture unless it is necessary and the more sensible way to implement the change.
                        2. Adhere to object oriented design practices and guidelines.
                        3. Store all the parameters in the appropriate configuration files. Avoid hard coded parameters.
                        4. Provide good clear documentation in docstrings for each package, class, method, function. Use comments to provide clear documentation for functional code blocks (no need for every line of code but mostly for main code blocks).
                        5. When logging, utilize logger functionality - use message codes instead of text, if necessary add new message codes to `messages/base.csv` or `messages/test.csv`. report variable values using data and not in message.
        h. Complete solution design. Specify which artifacts are going to be modified and how: which new files/classes/functions will be created and what will they do and how will they do it.
        i. Display the following to the engineer:
                d1. Modifications to the Requirements spec (`build/requirements/{base_or_app}.json`)
                d2. Modifications to the Architecture spec (`build/architecture/{base_or_app}.json`)
                d3. Intended Modifications to the Code (design) - what and how each aspect of the task will be implemented.
        j. Request a status change to `Specified` via the task status store.
        k. Save the session summary to the task result store (the `{result_store}` location given in your `task.md` prompt), e.g. `<result_store>/{task_id}/{task_id}.html`. This summary has to include the task's instruction file (instance of `task.md`), and hyperlinks to the files that were modified in diff view, along with a textual explanation of what was changed and why (if it's not trivial).
        l. Wait for the engineer to authorize your solution specification (requirements, architecture, design).
        m. The engineer may ask you to change the specification or make manual changes that you need to track and respect.

3. *Task Implementing*: This step is triggered by the engineer, and accomplished by you. If you are asked to work on a task:
        After the engineer approves the specification, you can continue to implementation based on the engineer-approved design, which may be different from your initial design.
        3a0. Request a status change to `SpecApproved` via the task status store (this is the spec-review gate: `Specified` -> `SpecApproved`, marking the specification as fully approved so implementation may proceed).
        3a. Update the 
        3b. Make sure there are no duplicate requirement numbers between the current task and the main branch. If there are, renumber the requirements in the current task branch to avoid conflict.
        Update the task branch with the specification artifacts (local and remote).
        3c. Merge the task branch's specification artifacts to the main branch.
        3d. Complete building the solution according to the approved design.
        3e. Design, build, and run tests for the required feature. This includes running the `test/tests/build.py` script to execute the build tests, which include the build phase and app running in order to execute the prep/live/post tests once for the app. Maintain three tests for each feature if necessary and applicable: pre-test, live test, and post-test. Tests can be combined with other tests to simplify the test system. For example, if a single variable or object is sufficient for two separate tests, we can do away with a single test and two success criteria to match the two tests. Each test has to report which feature(s) it covers.
        3f. Update the relevant `build/updates/{base_or_app}.json` file with the prgoress that you made.
        3g. Request a progress-report comment to be appended to the task (via the task status store).
        3h. Create a list of modified files and present them to the user. The files must be clickable and diff-reviewable. You can use a call to Visual Studio CODE to load the files in diff view to visualize the changes you made in each file, or you can create your own diff view in HTML and use the built-in HTML generator to create and store the file that shows the diff. Use a diff tool/package to visualize the changes clearly.
        3i. Request a status change to `Ready` via the task status store.

**Await the engineer's review and approval of the implementation.**

4. *Task Outcome Deployment*: after the engineer has reviewed and approved the change, and **Only after explicit user approval** 
When asked to finalize and deploy the code change (i.e. to move the status of the task from `Ready` to `Deployed`):

perform the Change Finalizing and Deployment steps below:
        a. request a status change to `CodeApproved` via the task status store (this is the code-review gate: `Ready` -> `CodeApproved`, marking the implementation as approved so deployment may proceed).
        b. request a code-approval comment to be appended to the task (via the task status store).
        c. bump the versions of the BaseApp (`resources/version/base.txt`) and placeholder for the variant app (`resources/version/app.txt`) as well as the config files that contain the version. 
                Update `resources/version/app.txt` to have the same value as the latest `resources/version/base.txt` but with the prefix letter `A`, so that future instantiations of new apps will start from the latest base version and then increment separately. For example if the base version is `26.05.27.03` then the app version will be `A26.05.27.03`
                Update the `config/base.json` file `COMMON` dict to include the latest version id.
        d. update the BaseApp's readme file to include the latest changes: `docs/readme/base.md`
                Update the updates/base.json file updates array to include a name and description of the change. The name structure has to be "[{APP_NAME}] [{Task_ID}] [{Feature_ID}] [{Feature}] {change desctiption}. New updates should be prepended to tthe top of the list.
        e. update the Main `README.md` file to provide a high-level functionality/capability overview of any added or modified features, functionalities, behaviors, etc. without going into implementation details.
                `README.md` will not detail implementation like you do in the `readme/base.md` or `readme/app.md` files. **Focus on functionality and capbility as it is now and not on the evolution of the software through the various changes and updates.**
                **`README.md` is an overview, never a changelog.** Edit the relevant overview sections **in place** to reflect the new current state — do **not** append or prepend per-release/per-version/per-task entries. Specifically, `README.md` must **not** contain `Highlights`, `Changelog`, `Release Notes`, `What's New`, or similarly titled sections, must **not** enumerate releases, and must **not** carry date- or version-stamped headings (e.g. `## Highlights (26.06.26.02)`). All per-release history belongs **only** in `docs/readme/base.md` (or `docs/readme/app.md`) and `build/updates/{base_or_app}.json`. The advisory test `test.tests.base.base.test_readme_overview_only` (Feature 5.3.1.1.4) scans for these markers and reports a non-blocking `WARN` if the README drifts back into a changelog.
        f. Ask the engineer for a final approval to stage/commit/push the changes to the repository. 
        **When the engineer approves the push:**
        g. Append a comment on the task to indicate user approval.
        g. Request a comment to be appended to the task (via the task status store) indicating user approval.
        h. Request a status change to `Deployed` via the task status store.
        i. Save the transcript of your work to the task result store (the `{result_store}` location given in your `task.md` prompt), e.g. `<result_store>/{task_id}/{task_id}.html`.
        j. Stage and Commit your code/spec/test changes (not the task ledger, which the server owns) with a concise but informative commit message that includes the task id and essence of change.
        k. Push the changes to the git repository **on the task's short-lived branch** (not directly to `main`).

5. *Task Outcome Integration*: This step includes merging the task's ad-hoc branch into the `main` branch.
        When asked to finalize the change (i.e. to move the status of the task from `Deployed` to `Done`):
        a. Ask the user (integration engineer) to review the documentation about the change as well as the code, run the app on the task branch and does whatever is necessary to make sure the functionality is implemented correctly, and supervise the merge of the task's branch into `main`, making sure there are no conflicts to solve in order to complete the merge successfully. Resolve or suggest resolutions to any conflicts surfaced during the merge before proceeding.
        a2. Once the engineer approves the deployed build for integration, request a status change to `BuildApproved` via the task status store (this is the build/integration-review gate: `Deployed` -> `BuildApproved`, marking the deployed build as approved for merge into `main`), and request a build-approval comment to be appended to the task.
        When final merge approval is granted:
        b. Merge the task's branch into `main`. Perform the merge from **within the `main` worktree** (`{APP}/main`), e.g. `git -C {APP}/main merge --no-ff <task-id>` followed by `git -C {APP}/main push origin main` (or merge via a GitHub pull request). Because all worktrees share the single bare object/ref store under `{APP}/.bare`, merging here updates `refs/heads/main` in the bare store directly — there is no separate bare copy of `main` to update.
        c. request a status change to `Done` via the task status store.
        d. request a deployment comment to be appended to the task (via the task status store) noting that the task branch was merged into `main`, and the branch and worktree dissolved.
        e. The Task Manager server applies the `Done` status and comment to the ledger on `main` (you do not commit the ledger yourself).
        f. Delete (dissolve) the short-lived task branch and remove its worktree (`git worktree remove {APP}/<task-id>` then `git branch -d <task-id>`), since they are no longer needed.
        g. Update the task report in the task result store (the `{result_store}` location given in your `task.md` prompt), e.g. `<result_store>/{task_id}/{task_id}.html`.
        h. Perform `git pull` from within the `main` worktree (`git -C {APP}/main pull`) to have the local `main` in full sync with the remote `main` after integration. Since the `main` worktree and the bare repository share one object/ref store under `{APP}/.bare`, this single pull keeps both the local `main` working tree and the bare store in sync — no separate update of the bare copy is required. (If the merge was done locally in step b, this pull is simply a confirming no-op.)
        