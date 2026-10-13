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

Please implement this task end-to-end in the workspace. Review the relevant instruction and requirement files, follow the existing code patterns, update the task ledger when appropriate, and run relevant tests or validation before finishing.


**Before diving in, quickly establish:**
1. **Outcome & check** — one line: what the finished task lets the user do, and how you'll prove it.
2. **Contracts** — inspect real samples of any data/interface you'll build on; note their actual shape before coding.
3. **Decomposition** — list enablers vs core vs polish; surface prerequisites as sub-tasks.
4. **Acceptance artifact** — decide which real output (not just tests) you'll inspect to confirm correctness.

(See "Working principles (apply to every task)" in `build/instructions/base.md` for the reasoning behind each.)


## Worker Session Traceability

At the start of every worker session, enqueue a task comment with the current
Copilot session ID and absolute task worktree path for future retrieval. Use
`enqueue_status_update(comment="Copilot worker session ID: <current-session-id>. Task worktree: <absolute-worktree-path>.")`.
Use the actual session ID supplied by the session context; never invent one or
reuse a prior worker's ID. If it is unavailable, explicitly record that fact.
Repeat this for a new or replacement session; do not edit the task ledger directly.
If the enqueue tool is unavailable, use the documented status-update fallback.

## Interactive Review Companion

When `scripts/review_server.py` is present and review is enabled, keep the
repository-owned companion running for the lifetime of this task worker:
`python code_review.py` from the task root or from `scripts` (the root entrypoint
delegates to the same launcher). The Task Manager worker launcher starts it automatically;
do not start a duplicate when this worker already owns a healthy companion.
Use explicit `--root`, `--task-id`, `--layer`, `--architecture`, `--requirements`,
`--results-root`, `--status-queue`, or `--baseline-ref` overrides when needed.
Verify `/health` matches this task and worktree. Never reuse, stop, or replace
another worker's listener; keep the launcher attached for worker cleanup.

The review displays complete requirement text, implementing architecture
features, and source-task provenance. Approve Spec independently for every
requirement thread and component before requesting task-level SpecApproved.
Approve Code applies independently to any nonterminal task-owned feature with
available selected implementation source/artifact, including Specified and InProgress;
neither Ready/Revised status nor specification approval is required. Thread-level
Approve Code covers all features linked to that requirement, including filtered/hidden
cards, skips CodeApproved/Removed features, and saves the batch atomically after
prevalidation and a revision check. Active revisions, missing discovery, or missing
source block approval; code approval never changes specification decisions.
Complete CodeApproved/Removed coverage enables task-level
CodeApproved. Reviewer identity comes from the OS, never a manual name field.
Read revision requests from `build/review/<task-id>/review.json`, complete them
with summaries, and regenerate with `scripts\review_report_generator.py` after
review-affecting changes. Run `scripts\validate_code_review.py` before review.

Review reports own `Spec Review.html` and `Code Review.html` filenames only;
the canonical task report and lifecycle reports remain owned by TaskReport.
If runtime support is absent, state that the companion is unavailable rather
than borrowing another task's server or a session-state generator.

## Branch & Worktree

Work this task on its own short-lived, ad-hoc branch (e.g. `{task_id}`) created off the latest `main`, never directly on `main`. The Task Manager launches you inside a **dedicated git worktree** for this task — a separate checkout directory that is a sibling of `main` under the `{APP}` container (`{APP}/<task-id>`), sharing the repository's bare object store but isolated from `main` and from other tasks' worktrees so that several task agents can run in parallel without colliding. Your `{workspace_root}` above is that worktree directory: do all of your file edits and commits there, and push the branch. After approval the task is finalized and pushed to that branch (the `Deployed` status); it is later merged into `main`, and the branch and its worktree are dissolved when the task is fully integrated (the `Done` status), under the integration engineer's supervision.

## Task Status Updates

Do NOT edit the task ledger ({task_file}) directly to change your status or add comments. Your work may run
on a separate branch, so direct ledger edits would be invisible to the Task Manager (which tracks `main`).

Instead, request every status change and progress comment with the **`enqueue_status_update`** tool provided
by the `task-status-queue` MCP server. The task id is taken from your environment, so you only supply the
fields you are changing:

- `status` (optional): the new status to move the task to (e.g. `InProgress`, `Specified`, `Ready`).
- `comment` (optional): a single progress comment to append.

Provide at least one of `status` or `comment`. For example, to start work:

```
enqueue_status_update(status="InProgress", comment="Started implementation.")
```

Each call durably enqueues a request file into the task status store; the Task Manager continuously samples
the store and applies each request to the ledger on `main` (merged by task `id`, then committed and pushed).
Requests are processed exactly once.

**Fallback (only if the `enqueue_status_update` tool is unavailable):** write the request yourself as a
uniquely named JSON file (e.g. `<utc-timestamp>-{id}.json`) into the status store's pending directory:
`{status_store}`. Write it atomically (temp name, then rename) and follow the update template shape
(`build/tasks/update.template.json`), restricted to `status` and/or a single `comments` item:

```json
{
    "TASKS": [
        {
            "id": "{id}",
            "status": "InProgress",
            "comments": [
                { "author": "{id} Agentic Builder", "content": "Started implementation.", "timestamp": "2026-06-25T12:00:00Z" }
            ]
        }
    ]
}
```

## Status Protocol

Request each status move via the `enqueue_status_update` tool (see "Task Status Updates" above), not by editing the ledger directly.

1. Move the status of the task from `ToDo` to `InProgress` when you start working.
2. Move the status of the task from `InProgress` to `Specified` when your design/specification is ready for review.
3. Move the status of the task from `Specified` to `SpecApproved` when the engineer approves the specification (spec-review gate); implementation may then proceed.
4. Move the status of the task from `SpecApproved` to `Ready` when your implementation is ready for review.
5. Move the status of the task from `Ready` to `CodeApproved` when the engineer approves the implementation (code-review gate); deployment may then proceed.
6. Move the status of the task from `CodeApproved` to `Deployed` when the change is finalized and pushed to its branch.
7. Move the status of the task from `Deployed` to `BuildApproved` when the engineer approves the deployed build for integration (build/integration-review gate).
8. Move the status of the task from `BuildApproved` to `Done` when the task branch is merged into `main`.

## Engineer Reviews will take place:
1. When the task is `Specified`.
2. When the task is `Ready`.
3. When the tast is `Deployed`.
You need to wait for the engineer to approve before moving to the next step.

When you modify files, end your response with a "Files changed" section.
Create an HTML file that contains a list of the files you changed. Generate it with the built-in `TaskReport` generator (Feature 6.1.19 in `utils/baseutils.py`) so the report shares the standard structure, style, and color scheme, selects its template from `APP.TASK_MANAGER.reports.templates` by status, and is preserved per-stage. Write this HTML file (and any work-summary/report HTML) into the task result store at `{result_store}` as `{result_store}/{id}/{id} - {Status}.html`, mirrored to `{result_store}/{id}/{id}.html`.
For each modified file, include a clickable VS Code URI string that opens the file in VS Code in diff mode. Because the Copilot CLI outputs plain text rather than a rich Markdown renderer, emit a clickable URI such as `vscode://file/C:/.../path/to/file` or `file:///C:/.../path/to/file` instead of Markdown link syntax. Use absolute workspace paths so the links are clickable. There is no reliable CLI-native URI for opening the Source Control diff view directly, so prefer a file-opening URI for the changed file.
Open the HTML file list in a new window.

Relevant Instruction Files:
{instruction_files}

Relevant Requirement Files:
{requirement_files}
