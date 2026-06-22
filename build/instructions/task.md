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

When you modify files, end your response with a "Files changed" section. For each modified file, include a clickable VS Code URI string that opens the file. Because the Copilot CLI outputs plain text rather than a rich Markdown renderer, emit a raw URI such as `vscode://file/C:/.../path/to/file` or `file:///C:/.../path/to/file` instead of Markdown link syntax. Use absolute workspace paths so the links are clickable. There is no reliable CLI-native URI for opening the Source Control diff view directly, so prefer a file-opening URI for the changed file.

Relevant Instruction Files:
{instruction_files}

Relevant Requirement Files:
{requirement_files}
