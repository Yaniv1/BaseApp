Task ID: {id}
Title: {title}
Type: {type}
Priority: {priority}
Status: {status}
Task File: {task_file}
Workspace Root: {workspace_root}
Task Status Store (drop status-update requests here): {status_store}
Task Result Store (write your HTML work-summary/report files here): {result_store}

Task Description:
{description}

This is a **PullBase** task: a managed, reviewable consumption of the latest BaseApp updates into this instance app. Your job is to pull the BaseApp changes into the task branch, review and summarize them for the engineer, and then — only on explicit approval — deploy and merge them under the supervised-merge model described in `base.md`. Do not implement features or fixes here; the only changes you introduce are those produced by the pull itself plus the version/changelog finalization that accompanies a deploy.

## Branch & Worktree

Work this task on its own short-lived, ad-hoc branch (e.g. `{id}`) created off the latest `main`, never directly on `main`. The Task Manager launches you inside a **dedicated git worktree** for this task — a separate checkout directory that is a sibling of `main` under the `{APP}` container (`{APP}/<task-id>`), sharing the repository's bare object store but isolated from `main` and from other tasks' worktrees. Your `{workspace_root}` above is that worktree directory: run the pull, make all edits, and commit there, then push the branch. After approval the task is finalized and pushed to that branch (the `Deployed` status); it is later merged into `main` and the branch and its worktree are dissolved when the task is fully integrated (the `Done` status), under the integration engineer's supervision.

## Task Status Updates

Do NOT edit the task ledger ({task_file}) directly to change your status or add comments. Your work may run on a separate branch, so direct ledger edits would be invisible to the Task Manager (which tracks `main`).

Instead, request every status change and progress comment with the **`enqueue_status_update`** tool provided by the `task-status-queue` MCP server. The task id is taken from your environment, so you only supply the fields you are changing:

- `status` (optional): the new status to move the task to (e.g. `InProgress`, `Ready`).
- `comment` (optional): a single progress comment to append.

Provide at least one of `status` or `comment`. For example, to start work:

```
enqueue_status_update(status="InProgress", comment="Started PullBase: running pullbase on the task branch.")
```

Each call durably enqueues a request file into the task status store; the Task Manager continuously samples the store and applies each request to the ledger on `main` (merged by task `id`, then committed and pushed). Requests are processed exactly once.

**Fallback (only if the `enqueue_status_update` tool is unavailable):** write the request yourself as a uniquely named JSON file (e.g. `<utc-timestamp>-{id}.json`) into the status store's pending directory: `{status_store}`. Write it atomically (temp name, then rename) and follow the update template shape (`build/tasks/update.template.json`), restricted to `status` and/or a single `comments` item.

## PullBase Workflow

1. **Confirm the task branch.** Verify you are on this task's branch/worktree (`{workspace_root}`), created off the latest `main`. Move the status from `ToDo` to `InProgress`.

2. **Resolve the BaseApp source.** By default, **auto-detect** the BaseApp source the way `scripts/pullbase.py` normally resolves it (no extra arguments). Only if the Task Description above explicitly names an alternative BaseApp source (path, repo, or ref) should you use that instead — pass it to the pull accordingly.

3. **Run the pull on the task branch.** Execute the pull from `{workspace_root}`:

   ```
   python scripts/pullbase.py
   ```

   (Add the source override from step 2 only if the description provided one.) This copies the BaseApp-owned files (the manifest `pull` set) into the working tree. Do not hand-edit the pulled files.

4. **Collect the changes.** Inventory exactly what the pull changed: use `git status` and `git diff` in the worktree, and read `build/updates/base.json` to see the BaseApp update/changelog entries that accompany these files. Note added, modified, and removed base-owned files, and call out anything that touches configuration, manifests, or scripts the instance relies on.

5. **Summarize for the engineer (HTML report).** Write an HTML work-summary into the task result store at `{result_store}/{id}/{id}.html` describing: the BaseApp source used, the list of changed files (each as a clickable `vscode://file/...` or `file:///...` URI using absolute workspace paths, in diff mode where possible), a digest of the relevant `build/updates/base.json` entries, and any items the engineer should pay special attention to (config/manifest/script changes, potential conflicts with instance-specific code). Open the HTML report in a new window. Then move the status from `InProgress` to `Ready` and **wait for the engineer's review**.

6. **Deploy and merge (supervised).** Only after the engineer explicitly approves, finalize under `base.md`'s supervised-merge model:
   - If the engineer approves **deploy and merge**: finalize the deploy (bump versions and update `build/updates/base.json`, readme/docs per `base.md`), commit and push the branch, move the status to `Deployed`, then proceed to the supervised merge into `main` and move the status through `Approved` to `Done`.
   - If the engineer approves **deploy only**: finalize and push the branch and move the status to `Deployed`, then **wait** for a separate explicit merge approval before merging into `main` and moving through `Approved` to `Done`.

   Never push or merge before the corresponding explicit approval.

## Status Protocol

Request each status move via the `enqueue_status_update` tool (see "Task Status Updates" above), not by editing the ledger directly.

1. `ToDo` -> `InProgress` when you start the pull.
2. `InProgress` -> `Ready` when the pull is complete and the HTML summary is ready for review.
3. `Ready` -> `Deployed` when the engineer approves and the branch is finalized and pushed.
4. `Deployed` -> `Approved` when the engineer approves integration (merge).
5. `Approved` -> `Done` when the merge to `main` is complete.

## Engineer Reviews will take place:
1. When the task is `Ready` (review of the pulled base changes and summary).
2. When the task is `Deployed`.
You need to wait for the engineer to approve before moving to the next step.

When you modify files, end your response with a "Files changed" section, and ensure the HTML file list in the result store reflects them.

Relevant Instruction Files:
{instruction_files}

Relevant Requirement Files:
{requirement_files}
