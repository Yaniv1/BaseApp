Task ID: {id}
Title: {title}
Type: {type}
Priority: {priority}
Status: {status}
Task File: {task_file}
Workspace Root: {workspace_root}

Task Description:
{description}

Please implement this task end-to-end in the workspace. Review the relevant instruction and requirement files, follow the existing code patterns, update the task ledger when appropriate, and run relevant tests or validation before finishing.

## Branch

Work this task on its own short-lived, ad-hoc branch (e.g. `{task_id}`) created off the latest `main`, never directly on `main`. Do all of the task's commits on that branch and push the branch. After approval the task is finalized and pushed to that branch (the `Deployed` status); it is later merged into `main` and dissolved when the task is fully integrated (the `Done` status), under the integration engineer's supervision.

## Status Protocol

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
Create an HTML file that contains a list of the files you changed.
For each modified file, include a clickable VS Code URI string that opens the file in VS Code in diff mode. Because the Copilot CLI outputs plain text rather than a rich Markdown renderer, emit a clickable URI such as `vscode://file/C:/.../path/to/file` or `file:///C:/.../path/to/file` instead of Markdown link syntax. Use absolute workspace paths so the links are clickable. There is no reliable CLI-native URI for opening the Source Control diff view directly, so prefer a file-opening URI for the changed file.
Open the HTML file list in a new window.

Relevant Instruction Files:
{instruction_files}

Relevant Requirement Files:
{requirement_files}
