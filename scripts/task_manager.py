#!/usr/bin/env python3
"""Feature ID: 3.6. Local web interface for managing task files."""

import argparse
import datetime as dt
import getpass
import html
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.baseutils import Config


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
COPILOT_PROMPT_TEMPLATE_PATH = PROJECT_ROOT / "build" / "instructions" / "task.md"
COPILOT_REVIEW_PROMPT_TEMPLATE_PATH = PROJECT_ROOT / "build" / "instructions" / "task-review.md"
COPILOT_WORKER_LAUNCH_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "launch_task_agent.ps1"
GIT_SYNC_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "sync_task_repo.ps1"


def _now_iso():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _default_task_filename_for_context(base_dir):
    return "base.json" if Path(base_dir).name.lower() == "baseapp" else "app.json"


def _default_tasks_dir_for_context(base_dir):
    return Path(base_dir).resolve() / "build" / "tasks"


def _resolve_path(raw_path, base_dir):
    if raw_path is None:
        return None
    text = str(raw_path).strip()
    if not text:
        return None
    candidate = Path(text)
    if not candidate.is_absolute():
        candidate = Path(base_dir).resolve() / candidate
    try:
        return candidate.resolve()
    except OSError:
        return None


def _load_tasks_store(tasks_path):
    path = Path(tasks_path)
    if path.is_file():
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    else:
        data = {}

    if not isinstance(data, dict):
        data = {}

    data.setdefault("TASKS", [])
    if not isinstance(data["TASKS"], list):
        data["TASKS"] = []

    return data


def _save_tasks_store(tasks_path, data):
    path = Path(tasks_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=4, ensure_ascii=False)
        handle.write("\n")


def _split_description(description):
    text = "\n".join(description) if isinstance(description, list) else str(description or "")
    lines = [line.rstrip() for line in text.splitlines()]
    meaningful = [line for line in lines if line.strip()]
    if len(meaningful) > 1:
        return meaningful
    if meaningful:
        return meaningful[0]
    return ""


def _normalise_comment(comment):
    if isinstance(comment, str):
        content = comment.strip()
        author = "Task Manager UI"
        timestamp = _now_iso()
    elif isinstance(comment, dict):
        content = str(comment.get("content", "")).strip()
        author = str(comment.get("author", "Task Manager UI")).strip() or "Task Manager UI"
        timestamp = str(comment.get("timestamp", "")).strip() or _now_iso()
    else:
        content = str(comment or "").strip()
        author = "Task Manager UI"
        timestamp = _now_iso()

    return {
        "author": author,
        "content": content,
        "timestamp": timestamp,
    }


def _task_id_prefix_token(tasks_path):
    """Derive the task-id prefix token from the tasks file basename.

    Uses the uppercased basename (without extension) of the tasks file so that,
    for example, ``app.json`` yields ``APP`` and ``base.json`` yields ``BASE``.
    """
    if tasks_path:
        stem = Path(tasks_path).stem.strip()
        if stem:
            return stem.upper()
    return "BASE"


def _next_task_id(tasks, tasks_path=None):
    today = dt.datetime.utcnow().strftime("%y%m%d")
    prefix = f"{_task_id_prefix_token(tasks_path)}-TASK-{today}-"
    next_number = 1
    for task in tasks:
        task_id = str(task.get("id", ""))
        if not task_id.startswith(prefix):
            continue
        try:
            next_number = max(next_number, int(task_id.rsplit("-", 1)[-1]) + 1)
        except ValueError:
            continue
    return f"{prefix}{next_number:04d}"


def _find_task(tasks, task_id):
    for index, task in enumerate(tasks):
        if str(task.get("id", "")) == str(task_id):
            return index, task
    return None, None


def _local_user():
    try:
        return getpass.getuser()
    except Exception:
        return "Task Manager UI"


def _create_task(tasks, payload, tasks_path=None):
    task = {
        key: value
        for key, value in payload.items()
        if key not in {"id", "uuid", "title", "description", "type", "priority", "status", "comments", "comment"}
    }
    task["id"] = str(payload.get("id") or _next_task_id(tasks, tasks_path)).strip()
    task["uuid"] = str(payload.get("uuid") or uuid.uuid4())
    task["title"] = str(payload.get("title", "")).strip()
    task["description"] = _split_description(payload.get("description", ""))
    task["type"] = str(payload.get("type") or "Feature").strip() or "Feature"
    task["priority"] = str(payload.get("priority") or "Medium").strip() or "Medium"
    task["status"] = str(payload.get("status") or "ToDo").strip() or "ToDo"

    creation_author = payload.get("author") or _local_user()
    creation_comment = _normalise_comment({
        "author": creation_author,
        "content": f"Task created: {task['title']}",
        "timestamp": _now_iso(),
    })

    comments = payload.get("comments") or []
    if not isinstance(comments, list):
        comments = [comments]
    task["comments"] = [creation_comment] + [
        _normalise_comment(comment)
        for comment in comments
        if str(comment or "").strip()
    ]

    comment = payload.get("comment")
    if comment:
        task["comments"].append(_normalise_comment(comment))

    tasks.append(task)
    return task


DELETED_STATUS = "Deleted"


def _is_deleted(task):
    return str(task.get("status") or "").strip().lower() == DELETED_STATUS.lower()


def _delete_task(tasks, task_id):
    """Feature ID: 3.6.7. Soft-delete a task, or permanently remove it if already deleted.

    A task that is not yet ``Deleted`` is moved to the ``Deleted`` status. Deleting a
    task that is already ``Deleted`` removes it from the task list entirely. Returns a
    ``(task, removed)`` tuple where ``removed`` indicates a permanent removal.
    """
    index, task = _find_task(tasks, task_id)
    if task is None:
        raise KeyError(task_id)

    if _is_deleted(task):
        tasks.pop(index)
        return task, True

    task["status"] = DELETED_STATUS
    tasks[index] = task
    return task, False


def _update_task(tasks, task_id, payload):
    index, task = _find_task(tasks, task_id)
    if task is None:
        raise KeyError(task_id)

    # A deleted task cannot be acted upon or modified except for its status.
    if _is_deleted(task):
        new_status = payload.get("status")
        if new_status is not None:
            value = str(new_status).strip()
            if value:
                task["status"] = value
        tasks[index] = task
        return task

    for field in ("title", "type", "priority", "status"):
        if field in payload and payload[field] is not None:
            value = str(payload[field]).strip()
            if value:
                task[field] = value

    if "description" in payload and payload["description"] is not None:
        task["description"] = _split_description(payload["description"])

    if "comments" in payload and payload["comments"] is not None:
        comments = payload["comments"]
        if not isinstance(comments, list):
            comments = [comments]
        task["comments"] = [
            _normalise_comment(comment)
            for comment in comments
            if str(comment or "").strip()
        ]

    if payload.get("comment"):
        task.setdefault("comments", []).append(_normalise_comment(payload["comment"]))

    tasks[index] = task
    return task


def _append_comment(tasks, task_id, payload):
    _, task = _find_task(tasks, task_id)
    if task is None:
        raise KeyError(task_id)

    comment = payload.get("comment", payload)
    task.setdefault("comments", []).append(_normalise_comment(comment))
    return task


def _tasks_summary(tasks):
    summary = {}
    for task in tasks:
        status = str(task.get("status") or "ToDo").strip() or "ToDo"
        summary[status] = summary.get(status, 0) + 1
    return summary


def _is_task_store_file(path):
    try:
        with Path(path).open("r", encoding="utf-8") as handle:
            data = json.load(handle)
        return isinstance(data, dict) and isinstance(data.get("TASKS"), list)
    except (OSError, json.JSONDecodeError):
        return False


def _scan_task_files(tasks_dir):
    """Feature ID: 3.6.3. Return available task JSON files from tasks_dir only."""
    directory = Path(tasks_dir).resolve()
    files = []
    if not directory.is_dir():
        return files

    for p in sorted(directory.glob("*.json")):
        rp = p.resolve()
        if p.stem == "template" or not _is_task_store_file(rp):
            continue
        files.append({
            "name": rp.name,
            "path": rp.as_posix(),
            "display": rp.as_posix(),
        })
    return files


def _resolve_requested_tasks_path(raw_path, base_dir, tasks_dir):
    candidate = _resolve_path(raw_path, base_dir)
    if candidate is None:
        return None
    if candidate.suffix.lower() != ".json" or candidate.stem == "template":
        return None
    if candidate.parent != Path(tasks_dir).resolve():
        return None
    if not candidate.exists() or not _is_task_store_file(candidate):
        return None
    return candidate


def _choose_tasks_path(base_dir, tasks_dir, requested_path=None):
    directory = Path(tasks_dir).resolve()
    requested = _resolve_requested_tasks_path(requested_path, base_dir, directory)
    if requested is not None:
        return requested

    default_path = directory / _default_task_filename_for_context(base_dir)
    if default_path.exists() and _is_task_store_file(default_path):
        return default_path

    files = _scan_task_files(directory)
    if files:
        return Path(files[0]["path"]).resolve()

    return default_path


def _description_to_text(description):
    if isinstance(description, list):
        return "\n".join(str(line) for line in description)
    return str(description or "")


def _format_context_file_list(directory, pattern):
    path = Path(directory).resolve()
    if not path.is_dir():
        return "- (directory not found)"

    matches = [item.resolve().as_posix() for item in sorted(path.glob(pattern)) if item.is_file()]
    if not matches:
        return "- (no matching files found)"

    return "\n".join(f"- {match}" for match in matches)


def _stringify_placeholder_value(value):
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        if all(isinstance(item, str) for item in value):
            return "\n".join(value)
        return json.dumps(value, indent=2, ensure_ascii=False)
    if isinstance(value, dict):
        return json.dumps(value, indent=2, ensure_ascii=False)
    return str(value)


def _populate_placeholders(text, params):
    def replace(match):
        key = match.group(1)
        if key not in params:
            return match.group(0)
        return str(params[key])

    return re.sub(r"\{([A-Za-z_][A-Za-z0-9_]*)\}", replace, text)


def _build_copilot_prompt(task, tasks_path, template_path=COPILOT_PROMPT_TEMPLATE_PATH):
    tasks_file = Path(tasks_path).resolve()
    build_dir = tasks_file.parent.parent
    workspace_root = build_dir.parent
    with Path(template_path).open("r", encoding="utf-8") as handle:
        template_text = handle.read()

    prompt_params = {
        key: _stringify_placeholder_value(value)
        for key, value in task.items()
    }
    prompt_params.update({
        "id": str(task.get("id", "")).strip(),
        "title": str(task.get("title", "")).strip(),
        "type": str(task.get("type", "Feature")).strip() or "Feature",
        "priority": str(task.get("priority", "Medium")).strip() or "Medium",
        "status": str(task.get("status", "ToDo")).strip() or "ToDo",
        "description": _description_to_text(task.get("description", "")).strip() or "(no description provided)",
        "task_file": tasks_file.as_posix(),
        "workspace_root": workspace_root.as_posix(),
        "instruction_files": _format_context_file_list(build_dir / "instructions", "*.md"),
        "requirement_files": _format_context_file_list(build_dir / "requirements", "*.json"),
    })
    return _populate_placeholders(template_text, prompt_params)


def _build_review_prompt(task, tasks_path):
    """Build a review-focused Copilot prompt for a task that is in the Ready state."""
    return _build_copilot_prompt(task, tasks_path, template_path=COPILOT_REVIEW_PROMPT_TEMPLATE_PATH)


def _get_store_base_dir(store):
    if hasattr(store, "base_dir") and store.base_dir is not None:
        return Path(store.base_dir).resolve()
    tasks_path = getattr(store, "tasks_path", None)
    if tasks_path is not None:
        task_path = Path(tasks_path).resolve()
        if task_path.exists() and task_path.is_file():
            return task_path.parent
        return task_path.parent
    return PROJECT_ROOT


def _run_git_command(repo_root, *args, check=False):
    result = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        check=False,
    )
    if check and result.returncode != 0:
        stderr = result.stderr.strip()
        stdout = result.stdout.strip()
        raise RuntimeError(stderr or stdout or f"git {' '.join(args)} failed")
    return result


def _detect_git_repo(base_dir):
    repo_root = Path(base_dir).resolve()
    repo_result = _run_git_command(repo_root, "rev-parse", "--show-toplevel")
    if repo_result.returncode != 0:
        return {
            "repo_root": None,
            "repo_remote": None,
            "repo_available": False,
            "repo_display": "(no linked git repo)",
        }

    repo_root = Path(repo_result.stdout.strip()).resolve()
    remote_result = _run_git_command(repo_root, "remote", "get-url", "origin")
    remote_url = remote_result.stdout.strip() if remote_result.returncode == 0 else None
    return {
        "repo_root": repo_root,
        "repo_remote": remote_url,
        "repo_available": True,
        "repo_display": remote_url or repo_root.as_posix(),
    }


def _sync_task_store_to_git(store, message=None, wait=False):
    base_dir = _get_store_base_dir(store)
    repo_info = _detect_git_repo(base_dir)
    if not repo_info["repo_available"]:
        raise RuntimeError("No linked git repo is available for this working directory.")

    repo_root = repo_info["repo_root"]
    task_path = Path(getattr(store, "tasks_path", base_dir / "tasks.json")).resolve()
    try:
        task_rel = task_path.relative_to(repo_root)
    except ValueError:
        task_rel = task_path.name

    status = _run_git_command(repo_root, "status", "--porcelain", "--", str(task_rel), check=False)
    if status.returncode != 0:
        raise RuntimeError(status.stderr.strip() or status.stdout.strip() or "Unable to inspect git status")

    changes_detected = bool(status.stdout.strip())
    commit_message = str(message).strip() or f"Update task store {task_path.name}"
    script_args = [
        str(GIT_SYNC_SCRIPT_PATH),
        "-RepoRoot",
        str(repo_root),
        "-TaskFile",
        str(task_rel.as_posix()),
        "-CommitMessage",
        commit_message,
    ]

    if wait:
        # Blocking sync that is still visible: open a new console window so the
        # user can watch the git output, but wait for it to finish before the
        # caller (UI startup) continues, so the task list reflects the sync.
        if sys.platform == "win32":
            launch_args = ["pwsh", "-NoProfile", "-File", *script_args]
            creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
            try:
                proc = subprocess.Popen(launch_args, creationflags=creationflags)
                returncode = proc.wait()
            except OSError as exc:
                raise RuntimeError(f"Unable to run the git sync: {exc}") from exc
            if returncode != 0:
                raise RuntimeError(f"git sync failed (exit code {returncode}). See the Git Sync window for details.")
        else:
            try:
                completed = subprocess.run(["pwsh", "-NoProfile", "-File", *script_args], check=False)
            except OSError as exc:
                raise RuntimeError(f"Unable to run the git sync: {exc}") from exc
            if completed.returncode != 0:
                raise RuntimeError(f"git sync failed (exit code {completed.returncode}).")
        sync_message = "Git sync completed."
    elif sys.platform == "win32":
        launch_args = [
            "cmd.exe",
            "/c",
            "start",
            "Git Sync",
            "pwsh",
            "-NoProfile",
            "-NoExit",
            "-File",
            *script_args,
        ]
        creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
        try:
            subprocess.Popen(launch_args, creationflags=creationflags)
        except OSError as exc:
            raise RuntimeError(f"Unable to launch the git sync window: {exc}") from exc
        sync_message = "Git sync launched in a visible terminal window."
    else:
        try:
            subprocess.Popen(["pwsh", "-NoProfile", "-NoExit", "-File", *script_args])
        except OSError as exc:
            raise RuntimeError(f"Unable to launch the git sync window: {exc}") from exc
        sync_message = "Git sync launched in a visible terminal window."

    return {
        "success": True,
        "message": sync_message + (" No task-file changes were detected." if not changes_detected else ""),
        "task_file": task_path.as_posix(),
        "repo_root": repo_root.as_posix(),
        "repo_remote": repo_info["repo_remote"],
        "commit_message": commit_message,
    }


# Feature 3.6.8
def _sync_selected_app_on_startup(store, message=None):
    """Feature ID: 3.6.8. Automatically sync the selected app's task file with its git repo on UI startup.

    Only the currently selected task store (``store.tasks_path``) is synced
    against its own linked git repository (vis-a-vis its repo), reusing
    :func:`_sync_task_store_to_git` synchronously (``wait=True``) so the caller
    blocks until the sync finishes before the task list is served. The sync is
    skipped when no git repo is linked, and any failure is reported but never
    blocks UI startup.
    """
    task_path = Path(getattr(store, "tasks_path", "")).resolve()
    repo_info = _detect_git_repo(_get_store_base_dir(store))
    if not repo_info["repo_available"]:
        print(f"[task-manager] Skipping git sync for {task_path.name}: no linked git repo.")
        return {"task_file": task_path.as_posix(), "synced": False, "reason": "no linked git repo"}

    commit_message = (message or "").strip() or f"Auto-sync {task_path.name} on task manager startup"
    try:
        result = _sync_task_store_to_git(store, message=commit_message, wait=True)
    except RuntimeError as exc:
        print(f"[task-manager] Git sync failed for {task_path.name}: {exc}")
        return {"task_file": task_path.as_posix(), "synced": False, "reason": str(exc)}

    print(f"[task-manager] {result['message']} ({task_path.name})")
    return {"task_file": task_path.as_posix(), "synced": True, "repo_root": result["repo_root"]}


def _load_app_config(base_dir):
    """Load the full app config using the app's own Config framework."""
    base_config_path = Path(base_dir).resolve() / "config" / "base.json"
    if not base_config_path.is_file():
        return None
    try:
        return Config(base_config_path=str(base_config_path)).config
    except Exception:
        return None


def _process_alive(pid):
    """Return True if a process with the given pid is currently running."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    if sys.platform == "win32":
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"PID eq {pid}", "/NH"],
                capture_output=True,
                text=True,
                check=False,
            )
        except OSError:
            return False
        return str(pid) in (result.stdout or "")
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError):
        return False
    return True


def _activate_copilot_window(session):
    """Bring an existing Copilot worker console window to the foreground.

    Returns True when a traceable, still-running window was activated, and False
    when the session is missing or the window can no longer be traced (e.g. it
    was closed or the task was completed elsewhere).
    """
    if not isinstance(session, dict):
        return False
    pid = session.get("pid")
    window_title = str(session.get("window_title") or "").strip()
    if not _process_alive(pid):
        return False
    if sys.platform != "win32":
        # Best-effort: the process is alive, but we cannot raise a console
        # window on non-Windows hosts, so report it as traceable.
        return True

    targets = [str(int(pid))]
    if window_title:
        targets.append(window_title)
    activate_expr = ";".join(
        "if($s.AppActivate('" + t.replace("'", "''") + "')){exit 0}"
        for t in targets
    )
    command = "$s=New-Object -ComObject WScript.Shell;" + activate_expr + ";exit 1"
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def _start_copilot_for_task(task, tasks_path, store, enable_full_read=False, enable_full_edit=False, enable_full_execution=False, mode="work"):
    copilot_cli = shutil.which("copilot") or shutil.which("github-copilot-cli")
    if not copilot_cli:
        raise RuntimeError("Copilot CLI is not available in PATH (expected 'copilot')")

    caller_root = store.base_dir
    output_prefix = getattr(getattr(store.config, "COMMON", None), "OUTPUT_PREFIX", None) if store.config else None
    output_path = getattr(getattr(store.config, "COMMON", None), "OUTPUT_PATH", None) if store.config else None
    if output_path:
        prompts_dir = Path(str(output_path).strip()) / "agent_prompts"
    elif output_prefix:
        prompts_dir = Path(str(output_prefix).strip()) / "agent_prompts"
    else:
        prompts_dir = caller_root / "build" / "tasks" / "agent_prompts"
    prompts_dir.mkdir(parents=True, exist_ok=True)

    safe_task_id = str(task.get("id", "task")).replace("/", "_").replace("\\", "_")
    task_title = str(task.get("title", "")).strip()
    session_name = (task_title or f"Task {safe_task_id}")[:100]
    is_review = str(mode).strip().lower() == "review"
    # Keep the task title in the window title so each Copilot window is named
    # after its task (and stays unique via the task id) rather than the
    # auto-generated session name the Copilot CLI would otherwise apply.
    window_label = f"Copilot {'Review' if is_review else 'Task'} {safe_task_id}"
    window_title = f"{window_label} - {session_name}" if task_title else window_label
    prompt_suffix = "-review" if is_review else ""
    prompt_path = prompts_dir / f"{safe_task_id}{prompt_suffix}.md"
    prompt_text = _build_review_prompt(task, tasks_path) if is_review else _build_copilot_prompt(task, tasks_path)
    prompt_path.write_text(prompt_text, encoding="utf-8")

    launch_args = [
        "pwsh", "-NoExit", "-File", str(COPILOT_WORKER_LAUNCH_SCRIPT_PATH),
        "-WorkspaceRoot", str(caller_root),
        "-TaskId", safe_task_id,
        "-PromptFile", str(prompt_path),
        "-TaskFile", str(Path(tasks_path).resolve()),
        "-CopilotCli", copilot_cli,
        "-SessionName", session_name,
        "-WindowTitle", window_title,
    ]
    if enable_full_read:
        launch_args.extend(["-EnableFullRead"])
    if enable_full_edit:
        launch_args.extend(["-EnableFullEdit"])
    if enable_full_execution:
        launch_args.extend(["-EnableFullExecution"])

    creationflags = subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0
    process = subprocess.Popen(
        launch_args,
        cwd=str(caller_root),
        creationflags=creationflags,
    )

    # Record the launched session on the task so a later "Ready" review can
    # trace and re-focus this console window instead of starting from scratch.
    task["worker_session"] = {
        "pid": getattr(process, "pid", None),
        "window_title": window_title,
        "mode": "review" if is_review else "work",
        "prompt_file": prompt_path.as_posix(),
        "started_at": _now_iso(),
    }
    return prompt_path


def _build_query_url(host, port, store):
    query = urlencode({
        "base_dir": store.base_dir.as_posix(),
        "tasks_dir": store.tasks_dir.as_posix(),
        "tasks_file": store.tasks_path.name,
    })
    return f"http://{host}:{port}/?{query}"


def _render_index_html(store):
    """Load and render the task manager HTML template from resources/templates/task_manager.html."""
    template_path = PROJECT_ROOT / "resources" / "templates" / "task_manager.html"
    with template_path.open("r", encoding="utf-8") as handle:
        template = handle.read()

    repo_info = _detect_git_repo(_get_store_base_dir(store))
    replacements = {
        "__TASKS_PATH__": html.escape(store.tasks_path.resolve().as_posix()),
        "__TASKS_DIR__": html.escape(store.tasks_dir.resolve().as_posix()),
        "__APP_NAME__": html.escape(store.app_name),
        "__GIT_REPO__": html.escape(repo_info["repo_root"].as_posix() if repo_info["repo_root"] else ""),
        "__GIT_REPO_DISPLAY__": html.escape(repo_info["repo_display"] or ""),
        "__GIT_REPO_AVAILABLE__": "true" if repo_info["repo_available"] else "false",
    }
    for key, value in replacements.items():
        template = template.replace(key, value)
    return template


class _TaskStore:
    """Container for mutable task manager state for a caller app context."""

    def __init__(self, base_dir=None, tasks_dir=None, tasks_path=None):
        self.base_dir = Path(base_dir or PROJECT_ROOT).resolve()
        self.config = _load_app_config(self.base_dir)
        self.tasks_dir = Path(tasks_dir or _default_tasks_dir_for_context(self.base_dir)).resolve()
        self.tasks_path = _choose_tasks_path(self.base_dir, self.tasks_dir, tasks_path)
        app_name = getattr(getattr(self.config, "COMMON", None), "APP_NAME", None) if self.config else None
        self.app_name = str(app_name).strip() if app_name else (self.base_dir.name or "App")


class _TaskManagerHandler(BaseHTTPRequestHandler):
    """HTTP request handler for the task manager JSON API and UI."""

    store = None

    def _send_json(self, status_code, payload):
        encoded = json.dumps(payload, ensure_ascii=False, indent=4).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
        self.send_header("Pragma", "no-cache")
        self.send_header("Expires", "0")
        self.end_headers()
        self.wfile.write(encoded)

    def _read_body(self):
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length).decode("utf-8")
        return json.loads(raw or "{}")

    def _get_tasks(self):
        return _load_tasks_store(self.__class__.store.tasks_path)

    def _write_tasks(self, data):
        _save_tasks_store(self.__class__.store.tasks_path, data)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            query = parse_qs(parsed.query)
            current = self.__class__.store

            base_dir_q = query.get("base_dir", [""])[0]
            tasks_dir_q = query.get("tasks_dir", [""])[0]
            tasks_file_q = query.get("tasks_file", [""])[0]
            if not tasks_file_q:
                for key in ("tasks", "tasks_path", "file"):
                    values = query.get(key, [])
                    if values:
                        tasks_file_q = values[0]
                        break

            base_dir = _resolve_path(base_dir_q, current.base_dir) if base_dir_q else current.base_dir
            if base_dir is None:
                base_dir = current.base_dir

            tasks_dir = _resolve_path(tasks_dir_q, base_dir) if tasks_dir_q else None
            if tasks_dir is None:
                tasks_dir = current.tasks_dir if isinstance(current, _TaskStore) else _default_tasks_dir_for_context(base_dir)
            if tasks_dir is None:
                tasks_dir = _default_tasks_dir_for_context(base_dir)

            # Preserve the currently selected tasks file on a bare reload so a
            # plain GET "/" without query params does not silently reset the
            # store to the default context.
            if not tasks_file_q and isinstance(current, _TaskStore):
                tasks_file_q = current.tasks_path

            self.__class__.store = _TaskStore(base_dir=base_dir, tasks_dir=tasks_dir, tasks_path=tasks_file_q or None)

            body = _render_index_html(self.__class__.store).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()
            self.wfile.write(body)
            return

        if parsed.path == "/api/config":
            store = self.__class__.store
            files = _scan_task_files(store.tasks_dir)
            repo_info = _detect_git_repo(_get_store_base_dir(store))
            resolved_store_path = store.tasks_path.resolve().as_posix()
            if not any(file_info.get("path") == resolved_store_path for file_info in files):
                files.append({
                    "name": store.tasks_path.name,
                    "path": resolved_store_path,
                    "display": resolved_store_path,
                })

            files.sort(key=lambda item: item.get("display", "").lower())
            query_url = _build_query_url(self.server.server_address[0], self.server.server_address[1], store)
            self._send_json(200, {
                "tasks_path": resolved_store_path,
                "tasks_display": resolved_store_path,
                "tasks_dir": store.tasks_dir.resolve().as_posix(),
                "app_name": store.app_name,
                "url": query_url,
                "available_files": files,
                "git_repo": repo_info["repo_root"].as_posix() if repo_info["repo_root"] else None,
                "git_repo_display": repo_info["repo_display"],
                "git_repo_available": repo_info["repo_available"],
            })
            return

        if parsed.path == "/api/tasks":
            data = self._get_tasks()
            self._send_json(200, {
                "tasks": data["TASKS"],
                "summary": _tasks_summary(data["TASKS"]),
                "tasks_path": self.__class__.store.tasks_path.resolve().as_posix(),
            })
            return

        if parsed.path.startswith("/api/tasks/"):
            task_id = parsed.path.removeprefix("/api/tasks/").split("/", 1)[0]
            data = self._get_tasks()
            _, task = _find_task(data["TASKS"], task_id)
            if task is None:
                self._send_json(404, {"error": "task not found", "task_id": task_id})
                return
            self._send_json(200, {"task": task})
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)
        data = self._get_tasks()
        if parsed.path == "/api/tasks":
            task = _create_task(data["TASKS"], self._read_body(), self.__class__.store.tasks_path)
            self._write_tasks(data)
            self._send_json(201, {"task": task})
            return

        if parsed.path == "/api/tasks/sync-git":
            payload = self._read_body()
            try:
                result = _sync_task_store_to_git(self.__class__.store, message=payload.get("message"))
            except RuntimeError as error:
                self._send_json(500, {"success": False, "error": str(error)})
                return
            self._send_json(200, result)
            return

        if parsed.path.startswith("/api/tasks/") and parsed.path.endswith("/start-agent"):
            task_id = parsed.path.removeprefix("/api/tasks/").removesuffix("/start-agent").strip("/")
            _, task = _find_task(data["TASKS"], task_id)
            if task is None:
                self._send_json(404, {"error": "task not found", "task_id": task_id})
                return
            if _is_deleted(task):
                self._send_json(409, {"error": "deleted tasks cannot be acted upon", "task_id": task_id})
                return
            payload = self._read_body()
            try:
                prompt_path = _start_copilot_for_task(
                    task,
                    self.__class__.store.tasks_path,
                    self.__class__.store,
                    enable_full_read=bool(payload.get("full_read", False)),
                    enable_full_edit=bool(payload.get("full_edit", False)),
                    enable_full_execution=bool(payload.get("full_execution", False)),
                )
            except RuntimeError as error:
                self._send_json(500, {"error": str(error), "task_id": task_id})
                return
            task["status"] = "InProgress"
            self._write_tasks(data)
            self._send_json(200, {
                "task_id": task_id,
                "prompt_file": prompt_path.as_posix(),
                "message": "Started a Copilot CLI terminal session with a task-focused prompt.",
                "task": task,
                "permissions": {
                    "full_read": bool(payload.get("full_read", False)),
                    "full_edit": bool(payload.get("full_edit", False)),
                    "full_execution": bool(payload.get("full_execution", False)),
                },
            })
            return

        if parsed.path.startswith("/api/tasks/") and parsed.path.endswith("/activate"):
            task_id = parsed.path.removeprefix("/api/tasks/").removesuffix("/activate").strip("/")
            _, task = _find_task(data["TASKS"], task_id)
            if task is None:
                self._send_json(404, {"error": "task not found", "task_id": task_id})
                return
            payload = self._read_body()

            # First, try to bring an existing, still-running worker window to the
            # foreground so the engineer can review the modifications in place.
            if _activate_copilot_window(task.get("worker_session")):
                self._send_json(200, {
                    "task_id": task_id,
                    "mode": "activated",
                    "message": "Brought the existing Copilot window to the foreground for review.",
                    "task": task,
                })
                return

            # The window is not traceable (closed, or the task was completed
            # elsewhere): start a dedicated review session instead.
            try:
                prompt_path = _start_copilot_for_task(
                    task,
                    self.__class__.store.tasks_path,
                    self.__class__.store,
                    enable_full_read=bool(payload.get("full_read", True)),
                    enable_full_edit=bool(payload.get("full_edit", False)),
                    enable_full_execution=bool(payload.get("full_execution", False)),
                    mode="review",
                )
            except RuntimeError as error:
                self._send_json(500, {"error": str(error), "task_id": task_id})
                return
            self._write_tasks(data)
            self._send_json(200, {
                "task_id": task_id,
                "mode": "review",
                "prompt_file": prompt_path.as_posix(),
                "message": "Started a dedicated Copilot review session for this Ready task.",
                "task": task,
            })
            return

        if parsed.path.startswith("/api/tasks/") and parsed.path.endswith("/comments"):
            task_id = parsed.path.removeprefix("/api/tasks/").removesuffix("/comments").strip("/")
            _, existing = _find_task(data["TASKS"], task_id)
            if existing is not None and _is_deleted(existing):
                self._send_json(409, {"error": "deleted tasks cannot be modified", "task_id": task_id})
                return
            try:
                task = _append_comment(data["TASKS"], task_id, self._read_body())
            except KeyError:
                self._send_json(404, {"error": "task not found", "task_id": task_id})
                return
            self._write_tasks(data)
            self._send_json(200, {"task": task})
            return

        self._send_json(404, {"error": "not found"})

    def do_PUT(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/config":
            payload = self._read_body()
            current = self.__class__.store

            base_dir_raw = payload.get("base_dir")
            tasks_dir_raw = payload.get("tasks_dir")
            tasks_path_raw = payload.get("tasks_path")

            base_dir = _resolve_path(base_dir_raw, current.base_dir) if base_dir_raw is not None else current.base_dir
            if base_dir is None:
                self._send_json(400, {"error": "invalid base_dir"})
                return

            tasks_dir = _resolve_path(tasks_dir_raw, base_dir) if tasks_dir_raw is not None else current.tasks_dir
            if tasks_dir is None:
                self._send_json(400, {"error": "invalid tasks_dir"})
                return

            if tasks_path_raw is not None and not str(tasks_path_raw).strip().endswith(".json"):
                self._send_json(400, {"error": "tasks_path must be a .json file"})
                return

            self.__class__.store = _TaskStore(base_dir=base_dir, tasks_dir=tasks_dir, tasks_path=tasks_path_raw)
            self._send_json(200, {
                "tasks_path": self.__class__.store.tasks_path.resolve().as_posix(),
                "tasks_dir": self.__class__.store.tasks_dir.resolve().as_posix(),
                "app_name": self.__class__.store.app_name,
            })
            return

        if not parsed.path.startswith("/api/tasks/"):
            self._send_json(404, {"error": "not found"})
            return

        task_id = parsed.path.removeprefix("/api/tasks/").strip("/")
        data = self._get_tasks()
        try:
            task = _update_task(data["TASKS"], task_id, self._read_body())
        except KeyError:
            self._send_json(404, {"error": "task not found", "task_id": task_id})
            return
        self._write_tasks(data)
        self._send_json(200, {"task": task})

    def do_DELETE(self):
        parsed = urlparse(self.path)
        if not parsed.path.startswith("/api/tasks/"):
            self._send_json(404, {"error": "not found"})
            return

        task_id = parsed.path.removeprefix("/api/tasks/").strip("/")
        data = self._get_tasks()
        try:
            task, removed = _delete_task(data["TASKS"], task_id)
        except KeyError:
            self._send_json(404, {"error": "task not found", "task_id": task_id})
            return
        self._write_tasks(data)
        self._send_json(200, {
            "task_id": task_id,
            "removed": removed,
            "task": None if removed else task,
            "message": (
                "Task permanently removed from the task list."
                if removed
                else "Task moved to the Deleted status."
            ),
        })

    def log_message(self, format, *args):
        return


# Feature 3.6.1
def parse_args(argv=None):
    """Feature ID: 3.6.1. Parse CLI arguments for the local task manager."""
    parser = argparse.ArgumentParser(description="Run the BaseApp task manager UI.")
    parser.add_argument("--base-dir", default=str(PROJECT_ROOT), help="Caller app base directory")
    parser.add_argument("--tasks-dir", default="", help="Task files directory (defaults to <base-dir>/build/tasks)")
    parser.add_argument("--tasks-path", default="", help="Task file path or file name inside tasks-dir")
    parser.add_argument("--host", default=DEFAULT_HOST, help="Host interface to bind")
    parser.add_argument("--port", default=DEFAULT_PORT, type=int, help="Port to bind")
    parser.add_argument("--browser-off", action="store_true", help="Do not open the UI in a browser after startup")
    parser.add_argument("--no-startup-sync", action="store_true", help="Do not auto-sync each app's task file with its git repo on startup")
    return parser.parse_args(argv)


def _pick_available_port(host, port):
    """Return a free port, falling back to an ephemeral port when the preferred one is busy."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, int(port)))
            return sock.getsockname()[1]
    except OSError:
        return 0


def _create_server(host, port, store):
    """Create and return a ThreadingHTTPServer bound to the given host/port.

    ``store`` may be a :class:`_TaskStore` or a path-like pointing at a tasks
    file; a bare path is wrapped in a :class:`_TaskStore` so callers can pass
    either form.
    """
    if not isinstance(store, _TaskStore):
        path = Path(store)
        store = _TaskStore(tasks_dir=path.parent, tasks_path=path)
    _TaskManagerHandler.store = store
    effective_port = _pick_available_port(host, port)
    server = ThreadingHTTPServer((host, effective_port), _TaskManagerHandler)
    server.allow_reuse_address = True
    return server


# Feature 3.6.2
def run(argv=None):
    """Feature ID: 3.6.2. Run the local task manager web app."""
    args = parse_args(argv)
    base_dir = Path(args.base_dir).resolve()
    tasks_dir = Path(args.tasks_dir).resolve() if args.tasks_dir else _default_tasks_dir_for_context(base_dir)
    initial_store = _TaskStore(base_dir=base_dir, tasks_dir=tasks_dir, tasks_path=(args.tasks_path or None))

    if not args.no_startup_sync:
        _sync_selected_app_on_startup(initial_store)

    server = _create_server(args.host, args.port, initial_store)
    url = _build_query_url(server.server_address[0], server.server_address[1], initial_store)
    if not args.browser_off:
        if sys.platform == "win32":
            browser_candidates = [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            ]
            browser_path = next((p for p in browser_candidates if Path(p).exists()), None)
            if browser_path:
                subprocess.Popen([browser_path, "--new-window", url], shell=False)
            else:
                subprocess.Popen(["cmd", "/c", "start", "", url], shell=False)
        else:
            webbrowser.open(url, new=1, autoraise=True)
    try:
        print(f"Task manager listening at {url}")
        print(f"Caller: {initial_store.app_name}")
        print(f"Tasks dir: {initial_store.tasks_dir.resolve()}")
        print(f"Editing: {initial_store.tasks_path.resolve()}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
