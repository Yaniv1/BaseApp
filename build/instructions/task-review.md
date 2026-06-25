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

Your job is to help the engineer review the change, not to redo it:
1. Inspect the current state of the workspace and the uncommitted changes (e.g. `git status` and `git diff`) to understand what was modified for this task.
2. Summarize, in clear and concise terms, what changes were made and why, file by file.
3. Verify the change against the task description and the relevant requirement files: state explicitly whether each requirement appears to be fulfilled, partially fulfilled, or missing.
4. Call out anything that looks incomplete, risky, or inconsistent with the existing code patterns, and suggest concrete follow-ups.
5. If the engineer approves, help finalize per `build/instructions/base.md`: finalize the change, set the task to `Deployed`, commit the task file together with all the changes, and push **on the task's short-lived branch** (not yet merged into `main`). Full integration is a separate, final step: once the task branch is merged into `main` and dissolved — with the integration engineer supervising the merge to ensure there are no unresolved conflicts — set the task to `Done`. If the engineer does not approve, keep the task in `Ready` and capture the requested adjustments as task comments.

Do not change the task to `Deployed`, merge the branch into `main`, set the task to `Done`, or commit/push until the engineer explicitly approves.

When you reference modified files, end your response with a "Files changed" section. For each file, include a clickable VS Code URI string that opens the file. Because the Copilot CLI outputs plain text rather than a rich Markdown renderer, emit a raw URI such as `vscode://file/C:/.../path/to/file` or `file:///C:/.../path/to/file` instead of Markdown link syntax. Use absolute workspace paths so the links are clickable.

Relevant Instruction Files:
{instruction_files}

Relevant Requirement Files:
{requirement_files}
