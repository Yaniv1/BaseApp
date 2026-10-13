Task ID: {id}
Title: {title}
Type: {type}
Priority: {priority}
Status: {status}
Task File: {task_file}
Workspace Root: {workspace_root}

Task Description:
{description}

This task is in the `Ready` state, which means the implementation work has already been done (here or elsewhere) and is awaiting engineering review. The original Copilot worker window for this task could not be traced (it was closed, or the work was completed in another session), so this is a dedicated review session.

## Worker Session Traceability

At the start of every review session, enqueue a task comment with the current
Copilot session ID and absolute task worktree path for future retrieval. Use
`enqueue_status_update(comment="Copilot review session ID: <current-session-id>. Task worktree: <absolute-worktree-path>.")`.
Use the actual session ID supplied by the session context; never invent one or
reuse a prior worker's ID. If it is unavailable, explicitly record that fact.
Repeat this for a new or replacement session; do not edit the task ledger directly.
If the enqueue tool is unavailable, use the status-update fallback documented in
`build/instructions/base.md`.

Your job is to help the engineer review the change, not to redo it:
1. Inspect the current state of the workspace and the uncommitted changes (e.g. `git status` and `git diff`) to understand what was modified for this task.
2. Summarize, in clear and concise terms, what changes were made and why, file by file.
3. Verify the change against the task description and the relevant requirement files: state explicitly whether each requirement appears to be fulfilled, partially fulfilled, or missing.
4. Call out anything that looks incomplete, risky, or inconsistent with the existing code patterns, and suggest concrete follow-ups.
5. Use the repository-owned interactive review companion following `build/instructions/task.md`; display complete requirements, implementing features, source-task mappings, and diffs. Keep requirement/component specification approvals separate from implementation approvals, and capture revision requests durably. Explicit Approve Code is available for any nonterminal task-owned feature with selected implementation source, without a Ready/Revised or prior-spec prerequisite. Thread Approve Code atomically covers every linked feature, including filtered/hidden cards; active revisions and missing source/discovery block approval. Never manufacture specification decisions from code approval.
6. If the engineer approves the implementation, request CodeApproved through `enqueue_status_update`, then help finalize per `build/instructions/base.md`: finalize the change, request Deployed, commit the implementation artifacts, and push **on the task's short-lived branch** (not yet merged into `main`). Never edit or commit the task ledger; the Task Manager applies status requests on its authoritative branch. Integration requires a separate BuildApproved gate under engineer supervision before Done. If the engineer does not approve, keep the task in Ready and enqueue requested adjustments as task comments.

Do not change the task to `Deployed`, merge the branch into `main`, set the task to `Done`, or commit/push until the engineer explicitly approves.

When you reference modified files, end your response with a "Files changed" section. For each file, include a clickable VS Code URI string that opens the file. Because the Copilot CLI outputs plain text rather than a rich Markdown renderer, emit a raw URI such as `vscode://file/C:/.../path/to/file` or `file:///C:/.../path/to/file` instead of Markdown link syntax. Use absolute workspace paths so the links are clickable.

Relevant Instruction Files:
{instruction_files}

Relevant Requirement Files:
{requirement_files}
