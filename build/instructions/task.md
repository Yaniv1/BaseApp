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
2. Move the status of the task from `InProgress` to `Specified` when your design is ready for implementation.
3. Move the status of the task from `Specified` to `Ready` when your implementation is ready.
4. Move the status of the task from `Ready` to `Deployed` when your implementation is approved by the engineer and pushed to its branch.
5. Move the status of the task from `Deployed` to `Approved` when the engineer approves integration (merge).
6. Move the status of the task from `Approved` to `Done` when your implementation is approved by the engineer and merge to `main` branch.

## Engineer Reviews will take place:
1. When the task is `Specified`.
2. When the task is `Ready`.
3. When the tast is `Deployed`.
You need to wait for the engineer to approve before moving to the next step.

When you modify files, end your response with a "Files changed" section.
Create an HTML file that contains a list of the files you changed. Write this HTML file (and any work-summary/report HTML) into the task result store at `{result_store}` (e.g. `{result_store}/{id}/{id}.html`).
For each modified file, include a clickable VS Code URI string that opens the file in VS Code in diff mode. Because the Copilot CLI outputs plain text rather than a rich Markdown renderer, emit a clickable URI such as `vscode://file/C:/.../path/to/file` or `file:///C:/.../path/to/file` instead of Markdown link syntax. Use absolute workspace paths so the links are clickable. There is no reliable CLI-native URI for opening the Source Control diff view directly, so prefer a file-opening URI for the changed file.
Open the HTML file list in a new window.

Relevant Instruction Files:
{instruction_files}

Relevant Requirement Files:
{requirement_files}
