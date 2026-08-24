#!/usr/bin/env python3
"""Feature ID: 3.6. Local web interface for managing task files."""

import argparse
import atexit
import datetime as dt
import getpass
import html
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import uuid
import webbrowser
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse
import urllib.request

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from utils.baseutils import Config, Params


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
COPILOT_PROMPT_TEMPLATE_PATH = PROJECT_ROOT / "build" / "instructions" / "task.md"
COPILOT_REVIEW_PROMPT_TEMPLATE_PATH = PROJECT_ROOT / "build" / "instructions" / "task-review.md"
COPILOT_WORKER_LAUNCH_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "launch_task_agent.ps1"

# PullBase tasks need no title — the Task Manager auto-fills "PullBase {date}".
PULLBASE_TASK_TYPE = "PullBase"
PULLBASE_TITLE_FORMAT = "PullBase {date}"
PULLBASE_TITLE_DATE_FORMAT = "%Y-%m-%d"
GIT_SYNC_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "sync_task_repo.ps1"
STATUS_QUEUE_MCP_SCRIPT_PATH = PROJECT_ROOT / "scripts" / "status_queue_mcp.py"


def _now_iso():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _app_name_from_config(base_dir):
    """Best-effort read of COMMON.APP_NAME from the app's config/base.json.

    Returns an empty string when the config is missing or lacks APP_NAME.
    """
    common = _load_config_file_raw(Path(base_dir).resolve() / "config" / "base.json").get("COMMON")
    if isinstance(common, dict):
        name = common.get("APP_NAME")
        if name:
            return str(name).strip()
    return ""


def _default_task_filename_for_context(base_dir):
    # BaseApp itself manages the base.json ledger; generated instance apps
    # manage app.json. Identify BaseApp by its configured COMMON.APP_NAME so the
    # default is correct even inside a git worktree whose folder name is a task
    # id (e.g. BASE-TASK-260629-0001) rather than "BaseApp". Fall back to the
    # base directory's folder name only when no config is available.
    app_name = _app_name_from_config(base_dir)
    if app_name:
        return "base.json" if app_name.lower() == "baseapp" else "app.json"
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


# Saved Task Manager UI "views" are named sort + per-column filter
# configurations. They live under APP.TASK_MANAGER.views in the layered JSON
# config as a FLAT ARRAY of view objects, each shaped:
#   { "id", "name", "tabs": [status, ...],
#     "sort":   { column: "asc"|"desc", ... },   # ordered, multi-column
#     "filter": { column: [values, ...], ... } } # multi-value per column
# A view is associated with one or more status tabs via its "tabs" list and is
# usable on a tab only when that tab is listed. A set of BUILT-IN default views
# ships in the committed config/base.json; these are read-only -- the user can
# neither rename, delete, nor change the tabs of a built-in. On top of the
# built-ins, user-created views are layered from config/local.json (app-local,
# git-tracked placeholder) and config/machine.json (machine-specific,
# git-ignored), with machine.json taking precedence. The effective set served to
# the UI is built-ins (tagged builtin=true) followed by the user views; the UI
# saves only user views back to config/machine.json, so built-ins always come
# from base.json and a user's personal views stay machine-local without ever
# polluting the committed config. The per-tab "active" (last-applied) view is a
# user preference persisted alongside the user views under
# APP.TASK_MANAGER.active_views as a { tab: viewId } map.
VIEWS_CONFIG_PATH = ("APP", "TASK_MANAGER", "views")
ACTIVE_CONFIG_PATH = ("APP", "TASK_MANAGER", "active_views")
# Built-in (read-only) views ship here; user views are layered on top from the
# override files (lowest to highest precedence). Runtime saves go to the last one.
VIEWS_BASE_CONFIG = "base.json"
VIEWS_USER_LAYERS = ("local.json", "machine.json")
VIEWS_SAVE_CONFIG = "machine.json"


def _config_file_path(base_dir, name):
    """Path to a named JSON file inside the app's config directory."""
    return Path(base_dir).resolve() / "config" / name


def _load_config_file_raw(path):
    """Load a config JSON file as a plain dict, tolerating a missing/invalid file."""
    path = Path(path)
    if path.is_file():
        try:
            with path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (json.JSONDecodeError, OSError, UnicodeDecodeError):
            data = {}
    else:
        data = {}
    return data if isinstance(data, dict) else {}


def _nested_get(data, path):
    """Return the value at a tuple key-path within nested dicts, or None."""
    current = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _views_array_from_config_file(path):
    """Return APP.TASK_MANAGER.views from a config file when it is a list."""
    views = _nested_get(_load_config_file_raw(path), VIEWS_CONFIG_PATH)
    return views if isinstance(views, list) else None


def _active_from_config_file(path):
    """Return APP.TASK_MANAGER.active_views from a config file when it is a dict."""
    active = _nested_get(_load_config_file_raw(path), ACTIVE_CONFIG_PATH)
    return active if isinstance(active, dict) else None


def _builtin_views(base_dir):
    """Flat list of built-in views shipped (read-only) in config/base.json."""
    views = _views_array_from_config_file(_config_file_path(base_dir, VIEWS_BASE_CONFIG))
    return views if isinstance(views, list) else []


def _user_views(base_dir):
    """Flat list of user-saved views from the override layers (machine wins)."""
    result = []
    for name in VIEWS_USER_LAYERS:
        views = _views_array_from_config_file(_config_file_path(base_dir, name))
        if views is not None:
            result = views
    return result


def _active_views(base_dir):
    """Per-tab active-view map from the override layers (machine wins)."""
    result = {}
    for name in VIEWS_USER_LAYERS:
        active = _active_from_config_file(_config_file_path(base_dir, name))
        if active is not None:
            result = active
    return result


def _builtin_view_ids(base_dir):
    """Set of view ids that are built-in (defined in config/base.json)."""
    return {str(v["id"]) for v in _builtin_views(base_dir)
            if isinstance(v, dict) and v.get("id")}


def _tag_views(views, builtin):
    """Return a copy of each valid view dict with a builtin flag stamped on it."""
    out = []
    for view in views or []:
        if isinstance(view, dict) and view.get("id"):
            item = dict(view)
            item["builtin"] = builtin
            out.append(item)
    return out


def _load_views(base_dir):
    """Return the effective views payload: a flat list of read-only built-in
    views from config/base.json (each tagged builtin=true) followed by the user's
    saved views from config/local.json / config/machine.json (tagged
    builtin=false), plus the per-tab active-view map. User views whose id collides
    with a built-in id are dropped so built-ins always win."""
    builtin_ids = _builtin_view_ids(base_dir)
    builtins = _tag_views(_builtin_views(base_dir), True)
    users = [v for v in _tag_views(_user_views(base_dir), False)
             if str(v["id"]) not in builtin_ids]
    active = {str(k): v for k, v in _active_views(base_dir).items() if v}
    return {"views": builtins + users, "active": active}


def _save_views(base_dir, payload):
    """Persist only the user-created views and the active-view map into
    config/machine.json under APP.TASK_MANAGER.views / .active_views
    (machine-specific, git-ignored). Built-in views (those shipped in
    config/base.json) are never written back, so the user can neither delete,
    rename, nor re-tab them; every other key in the file is preserved and the
    write is atomic. Returns the effective (built-in + user) payload."""
    if not isinstance(payload, dict):
        payload = {}
    views = payload.get("views")
    if not isinstance(views, list):
        views = []
    active = payload.get("active")
    if not isinstance(active, dict):
        active = {}

    builtin_ids = _builtin_view_ids(base_dir)
    user_list = []
    for view in views:
        if not isinstance(view, dict) or not view.get("id"):
            continue
        if str(view.get("id")) in builtin_ids or view.get("builtin") is True:
            continue  # never persist built-in views
        user_list.append({k: val for k, val in view.items() if k != "builtin"})
    user_ids = {str(v["id"]) for v in user_list}
    cleaned_active = {str(tab): vid for tab, vid in active.items()
                      if vid and (str(vid) in user_ids or str(vid) in builtin_ids)}

    path = _config_file_path(base_dir, VIEWS_SAVE_CONFIG)
    data = _load_config_file_raw(path)
    _set_nested(data, VIEWS_CONFIG_PATH, user_list)
    _set_nested(data, ACTIVE_CONFIG_PATH, cleaned_active)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=4, ensure_ascii=False)
        handle.write("\n")
    os.replace(tmp, path)
    return _load_views(base_dir)


def _set_nested(data, path, value):
    """Set value at a tuple key-path within nested dicts, creating dicts as needed."""
    section = data
    for key in path[:-1]:
        child = section.get(key)
        if not isinstance(child, dict):
            child = {}
            section[key] = child
        section = child
    section[path[-1]] = value


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
    task["type"] = str(payload.get("type") or "Feature").strip() or "Feature"
    title = str(payload.get("title", "")).strip()
    # PullBase tasks need no title: auto-fill "PullBase {date}" so the designer can
    # request a base pull without inventing a title.
    if not title and task["type"] == PULLBASE_TASK_TYPE:
        date_str = dt.datetime.utcnow().strftime(PULLBASE_TITLE_DATE_FORMAT)
        title = PULLBASE_TITLE_FORMAT.format(date=date_str)
    task["title"] = title
    task["description"] = _split_description(payload.get("description", ""))
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


# Fields an agent status-update request is allowed to change on a task. The
# ``id`` is the match key (never written); the rest are applied by value type.
STATUS_UPDATE_ALLOWED_FIELDS = {"id", "status", "comments", "worker_session"}


def _apply_task_fields(existing, incoming):
    """Apply incoming fields onto an existing task dict in place, by value type.

    - list value  -> COMBINE (existing items first, incoming appended);
    - dict value  -> UPDATE recursively (apply only the incoming keys);
    - scalar value -> OVERWRITE.

    The ``id`` key is the match key and is never written.
    """
    for field, value in incoming.items():
        if field == "id":
            continue
        current = existing.get(field)
        if isinstance(value, list):
            existing[field] = list(current or []) + list(value)
        elif isinstance(value, dict) and isinstance(current, dict):
            _apply_task_fields(current, value)
        else:
            existing[field] = value
    return existing


def _apply_task_update(tasks, update_task, allowed_fields=None):
    """Apply one update task (matched by ``id``) onto the in-memory task list.

    Only the fields present in ``update_task`` (optionally restricted to
    ``allowed_fields``) are touched. ``comments`` are normalised before being
    combined with the task's existing comments. Returns the updated task dict,
    or ``None`` when no task matches the id.
    """
    if not isinstance(update_task, dict):
        return None
    task_id = str(update_task.get("id", "")).strip()
    if not task_id:
        return None
    _, existing = _find_task(tasks, task_id)
    if existing is None:
        return None

    if allowed_fields is not None:
        incoming = {key: value for key, value in update_task.items() if key in allowed_fields}
    else:
        incoming = dict(update_task)

    if isinstance(incoming.get("comments"), list):
        normalised = []
        for comment in incoming["comments"]:
            content = comment.get("content", "") if isinstance(comment, dict) else comment
            if str(content or "").strip():
                normalised.append(_normalise_comment(comment))
        incoming["comments"] = normalised

    _apply_task_fields(existing, incoming)
    return existing


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


def _build_copilot_prompt(task, tasks_path, template_path=None, store=None, workspace_override=None):
    tasks_file = Path(tasks_path).resolve()
    build_dir = tasks_file.parent.parent
    workspace_root = Path(workspace_override).resolve() if workspace_override else build_dir.parent

    # Select the instruction template by task type from the configured
    # CONFIG.APP.TASK_MANAGER.templates mapping (e.g. PullBase -> pullbase.md),
    # falling back to the 'default' entry and finally task.md. An explicit
    # template_path (e.g. the review template) always wins.
    if template_path is None:
        task_type = str(task.get("type", "")).strip()
        templates = {}
        task_manager_cfg = getattr(getattr(getattr(store, "config", None), "APP", None), "TASK_MANAGER", None)
        templates_cfg = getattr(task_manager_cfg, "templates", None) if task_manager_cfg else None
        if isinstance(templates_cfg, Params):
            templates = templates_cfg.get_dict()
        elif isinstance(templates_cfg, dict):
            templates = templates_cfg
        template_name = templates.get(task_type) or templates.get("default") or "task.md"
        template_path = build_dir / "instructions" / template_name

    with Path(template_path).open("r", encoding="utf-8") as handle:
        template_text = handle.read()

    status_store_pending = ""
    result_store = ""
    if store is not None:
        paths = _resolve_status_store_paths(store)
        if paths["pending"] is not None:
            status_store_pending = Path(paths["pending"]).as_posix()
        result_dir = _resolve_task_manager_path(store, "result_store")
        if result_dir is not None:
            result_store = result_dir.as_posix()

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
        "status_store": status_store_pending,
        "result_store": result_store,
    })
    return _populate_placeholders(template_text, prompt_params)


def _build_review_prompt(task, tasks_path, store=None, workspace_override=None):
    """Build a review-focused Copilot prompt for a task that is in the Ready state."""
    return _build_copilot_prompt(task, tasks_path, template_path=COPILOT_REVIEW_PROMPT_TEMPLATE_PATH, store=store, workspace_override=workspace_override)


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


def _worktree_root(store):
    """Resolve the container directory that holds per-branch git worktrees.

    The repository uses a bare object store with **every branch -- including
    ``main`` -- checked out as a peer git worktree** under a single ``{APP}``
    container (e.g. ``C:/code/BaseApp/.bare`` shared store, ``C:/code/BaseApp/main``
    for ``main``, ``C:/code/BaseApp/<task-id>`` for each task). The main working
    tree (``base_dir``) is therefore itself one of these peers, and the container
    that holds them all is simply its parent.

    Every ad-hoc task worktree is always created as a sibling of ``main`` under
    that container, so the location is fixed (the parent of the main working
    tree) and is not configurable.
    """
    return _get_store_base_dir(store).parent


def _worktree_path_for_task(store, task_id):
    """Resolve the dedicated worktree directory for a task.

    Each task is worked in its own git worktree at ``<container>/<task-id>``
    (a sibling of the ``main`` worktree, sharing the bare object store) so that
    concurrent task agents each get an isolated checkout of their own
    ``<task-id>`` branch and never share (or stomp on) a single working tree.
    """
    root = _worktree_root(store)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(task_id).strip()).strip("-") or "task"
    return root / safe


def _branch_name_for_task(task_id):
    """Return the short-lived branch name used to work a task in isolation.

    Each task is worked on in its own short-lived, ad-hoc branch (named after
    the task id, e.g. ``<task-id>``) rather than directly on the long-lived
    ``main`` branch, so that concurrent tasks never mix their changes. The
    branch name deliberately carries no ``task/`` prefix so it matches the
    task's worktree folder name exactly (``{APP}/<task-id>``) and never nests
    into sub-folders when materialised as a worktree. The branch is actually
    created and checked out by ``launch_task_agent.ps1`` (via its ``-TaskBranch``
    parameter) when the worker session starts; this helper only computes the
    deterministic branch name so the task manager can pass it to the launcher
    and record it on the worker session.
    """
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", str(task_id).strip()).strip("-") or "task"
    return safe


def _task_branch_exists(base_dir, task_id):
    """Return ``True`` when a branch for the task already exists.

    A pre-existing ``<task-id>`` branch means the task may already be (or have
    been) worked on, so the caller must not silently start a new worker on it.
    Both the local branch and an already-fetched remote-tracking branch
    (``origin/<task-id>``) are considered. Returns ``False`` when the working
    directory is not a git repository.
    """
    repo_info = _detect_git_repo(base_dir)
    if not repo_info["repo_available"]:
        return False

    repo_root = repo_info["repo_root"]
    branch = _branch_name_for_task(task_id)
    for ref in (f"refs/heads/{branch}", f"refs/remotes/origin/{branch}"):
        result = _run_git_command(repo_root, "show-ref", "--verify", "--quiet", ref)
        if result.returncode == 0:
            return True
    return False


def _resolve_ledger_branch(store):
    """Return the git branch the task ledger is synced on.

    The Task Manager is the sole writer of the authoritative ledger and tracks
    the long-lived ledger branch (``main``), while each task is worked on in its
    own short-lived ``<task-id>`` branch. The headless status-sync must therefore
    commit the ledger to this configured branch (``APP.TASK_MANAGER.ledger_branch``,
    default ``main``) -- never onto the worker's task branch. Returns the
    configured value when set, otherwise ``"main"``.
    """
    app_cfg = getattr(getattr(store, "config", None), "APP", None)
    task_manager_cfg = getattr(app_cfg, "TASK_MANAGER", None) if app_cfg else None
    raw = getattr(task_manager_cfg, "ledger_branch", None) if task_manager_cfg else None
    branch = str(raw).strip() if raw else ""
    return branch or "main"


def _sync_task_store_to_git(store, message=None, wait=False, headless=False, branch=None):
    base_dir = _get_store_base_dir(store)
    task_path = Path(getattr(store, "tasks_path", base_dir / "tasks.json")).resolve()
    # Anchor the sync on the ledger's OWN repository/worktree rather than on the
    # server's base_dir. The Task Manager server may be launched inside one git
    # worktree (its base_dir, e.g. a task worktree) while the ledger it edits
    # lives in a different worktree (e.g. main/build/tasks/base.json). Detecting
    # the repo from base_dir would then make task_path.relative_to(repo_root)
    # raise ValueError, and the old fallback silently dropped the build/tasks/
    # prefix so the sync script could not find the ledger. Detecting the repo
    # from the directory that actually contains the ledger guarantees task_path
    # is always under repo_root and yields build/tasks/base.json.
    repo_info = _detect_git_repo(task_path.parent)
    if not repo_info["repo_available"]:
        raise RuntimeError("No linked git repo is available for this working directory.")

    repo_root = repo_info["repo_root"]
    try:
        task_rel = task_path.relative_to(repo_root)
    except ValueError:
        task_rel = Path(task_path.name)

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
    # When a target ledger branch is supplied (e.g. the headless status-inbox
    # sync passes the configured ledger branch), the ledger commit is pushed to
    # that branch in isolation rather than onto whatever branch the worker's
    # working tree happens to be on.
    if branch:
        script_args += ["-Branch", str(branch)]

    if headless:
        # Silent sync used by the status-inbox watcher: no console window, output
        # captured so failures can be surfaced in the server log without blocking.
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
        try:
            completed = subprocess.run(
                ["pwsh", "-NoProfile", "-File", *script_args],
                capture_output=True,
                text=True,
                check=False,
                creationflags=creationflags,
            )
        except OSError as exc:
            raise RuntimeError(f"Unable to run the git sync: {exc}") from exc
        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()
            raise RuntimeError(detail or f"git sync failed (exit code {completed.returncode}).")
        sync_message = "Git sync completed (headless)."
    elif wait:
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


def _resolve_task_manager_path(store, key):
    """Resolve an ``APP.TASK_MANAGER.<key>`` directory to an absolute path.

    Relative paths are resolved against the store base dir. Returns ``None``
    when the key is unset (so callers can fall back to a default location).
    """
    app_cfg = getattr(getattr(store, "config", None), "APP", None)
    task_manager_cfg = getattr(app_cfg, "TASK_MANAGER", None) if app_cfg else None
    raw = getattr(task_manager_cfg, key, None) if task_manager_cfg else None
    if not raw:
        return None
    path = Path(str(raw).strip())
    if not path.is_absolute():
        path = _get_store_base_dir(store) / path
    return path.resolve()


def _resolve_status_store_paths(store):
    """Resolve the task status queue layout (pending/processed/failed) from config.

    The queue root comes from ``APP.TASK_MANAGER.status_queue`` (relative paths
    are resolved against the store base dir), the poll interval from
    ``APP.TASK_MANAGER.request_polling_frequency`` and the on/off switch from
    ``APP.TASK_MANAGER.enable``. Returns a dict describing whether the channel is
    enabled, the poll interval, and the three sub-directories.
    """
    app_cfg = getattr(getattr(store, "config", None), "APP", None)
    task_manager_cfg = getattr(app_cfg, "TASK_MANAGER", None) if app_cfg else None
    enabled = bool(getattr(task_manager_cfg, "enable", False)) if task_manager_cfg else False
    poll = getattr(task_manager_cfg, "request_polling_frequency", 5) if task_manager_cfg else 5
    try:
        poll = max(1, int(poll))
    except (TypeError, ValueError):
        poll = 5

    root = _resolve_task_manager_path(store, "status_queue")
    if root is None:
        return {"enabled": False, "poll_seconds": poll, "root": None,
                "pending": None, "processed": None, "failed": None}

    return {
        "enabled": enabled,
        "poll_seconds": poll,
        "root": root,
        "pending": root / "pending",
        "processed": root / "processed",
        "failed": root / "failed",
    }


# Feature 3.6.9
def _status_inbox_dir(store):
    """Feature ID: 3.6.9. Resolve and create the task status store directories.

    Returns the resolved ``pending``/``processed``/``failed`` layout when the
    channel is enabled and configured, otherwise ``None``.
    """
    paths = _resolve_status_store_paths(store)
    if not paths["enabled"] or paths["root"] is None:
        return None
    for key in ("pending", "processed", "failed"):
        paths[key].mkdir(parents=True, exist_ok=True)
    return paths


def _move_inbox_file(src, dest_dir):
    """Move a processed/failed request file into dest_dir, avoiding name clashes."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        stamp = dt.datetime.utcnow().strftime("%Y%m%d%H%M%S%f")
        dest = dest_dir / f"{src.stem}.{stamp}{src.suffix}"
    shutil.move(str(src), str(dest))
    return dest


# Copilot CLI's per-session event-log root; each session writes one
# <session-id>/events.jsonl there. Its location follows the Copilot CLI's own
# COPILOT_HOME override (default $HOME/.copilot), so the Task Manager always
# reads from wherever the CLI actually writes — no separate config knob.
COPILOT_SESSION_STATE_SUBDIR = "session-state"


def _copilot_home_dir():
    """Return the Copilot CLI home directory (COPILOT_HOME env override, else ~/.copilot).

    Mirrors the Copilot CLI's own resolution so the session-state root the Task
    Manager reads matches wherever the CLI writes. Because workers are launched
    by the Task Manager (via launch_task_agent.ps1) they inherit this same
    environment, so the two never diverge under the normal launch path.
    """
    home = os.environ.get("COPILOT_HOME")
    if home and home.strip():
        return Path(home.strip()).expanduser()
    return Path.home() / ".copilot"


def _normalise_path_key(path):
    """Normalise a filesystem path for case-insensitive worktree matching."""
    try:
        return Path(str(path)).resolve().as_posix().lower()
    except (OSError, ValueError):
        return str(path).replace("\\", "/").rstrip("/").lower()


def _now_ms():
    """Current wall-clock time in epoch milliseconds."""
    return int(time.time() * 1000)


# Feature 3.6.17.3
def _parse_iso_ms(value):
    """Feature ID: 3.6.17.3. Parse an ISO-8601 timestamp into epoch milliseconds.

    Accepts the event-log form (e.g. ``2026-06-24T19:40:37.641Z``) including a
    trailing ``Z`` (UTC) and explicit offsets. Returns an ``int`` number of
    milliseconds since the epoch, or ``None`` when the value is
    missing/malformed so the caller can keep timing best-effort.
    """
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = dt.datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return int(parsed.timestamp() * 1000)


# Feature 3.6.17.1
def _resolve_session_state_dir(store=None):
    """Feature ID: 3.6.17.1. Resolve the Copilot CLI session-state directory.

    Resolves ``<COPILOT_HOME>/session-state`` (COPILOT_HOME from the environment,
    defaulting to ~/.copilot) — the same location the Copilot CLI uses to store
    session state. Returns a ``Path`` when the directory exists, otherwise
    ``None`` (so usage reading is skipped). The ``store`` argument is accepted
    for call-site compatibility but is unused.
    """
    path = _copilot_home_dir() / COPILOT_SESSION_STATE_SUBDIR
    try:
        return path if path.is_dir() else None
    except OSError:
        return None


# Feature 3.6.17.2
def _read_worktree_session_usages(session_state_dir, worktree_paths, now_ms=None):
    """Feature ID: 3.6.17.2. Read usage for multiple worktrees in one scan.

    Scans each ``<session>/events.jsonl`` under ``session_state_dir``, selects
    sessions whose ``session.start`` context cwd matches one of ``worktree_paths``
    (normalised), sums the ``assistant.message`` ``outputTokens`` for the
    cumulative token total, captures the latest model, and measures the task's
    open time from its earliest matching ``session.start`` until the latest
    ``session.shutdown``. If any matching session remains open, the effective
    end is ``now_ms`` (the current wall clock). Returns the start/end timestamps
    and total elapsed milliseconds alongside the existing model/token values.
    Missing directories and malformed lines/timestamps are skipped.
    """
    def empty_usage():
        return {
            "model": None,
            "total_tokens": 0,
            "total_elapsed_ms": 0,
            "started_at_ms": None,
            "ended_at_ms": None,
            "matched": False,
        }

    targets = {
        _normalise_path_key(path)
        for path in worktree_paths or []
        if path
    }
    results = {target: empty_usage() for target in targets}
    if not session_state_dir or not targets:
        return results
    root = Path(session_state_dir)
    if not root.is_dir():
        return results
    timing = {
        target: {"earliest_start": None, "latest_shutdown": None, "any_open": False}
        for target in targets
    }

    for session_dir in sorted(root.iterdir()):
        events = session_dir / "events.jsonl"
        if not events.is_file():
            continue
        matched_target = None
        session_total = 0
        session_model = None
        start_ts = None
        shutdown_ts = None
        session_open = False
        try:
            with events.open("r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except (ValueError, TypeError):
                        continue
                    if not isinstance(event, dict):
                        continue
                    etype = event.get("type")
                    data = event.get("data") or {}
                    if not isinstance(data, dict):
                        continue

                    ts = _parse_iso_ms(event.get("timestamp"))

                    if etype == "session.start":
                        cwd = (data.get("context") or {}).get("cwd") or data.get("cwd")
                        cwd_key = _normalise_path_key(cwd) if cwd else None
                        if cwd_key in targets:
                            matched_target = cwd_key
                            session_open = True
                            if ts is not None and (start_ts is None or ts < start_ts):
                                start_ts = ts
                    elif etype == "session.resume":
                        if matched_target is not None:
                            session_open = True
                    elif etype == "assistant.message":
                        tok = data.get("outputTokens")
                        if isinstance(tok, (int, float)) and not isinstance(tok, bool):
                            session_total += int(tok)
                        if data.get("model"):
                            session_model = data.get("model")
                    elif etype == "session.model_change":
                        if data.get("newModel"):
                            session_model = data.get("newModel")
                    elif etype == "session.shutdown":
                        if matched_target is not None and ts is not None:
                            shutdown_ts = ts
                            session_open = False
        except OSError:
            continue

        if matched_target is None:
            continue

        result = results[matched_target]
        state = timing[matched_target]
        result["matched"] = True
        result["total_tokens"] += session_total
        earliest_start = state["earliest_start"]
        latest_shutdown = state["latest_shutdown"]
        if start_ts is not None and (earliest_start is None or start_ts < earliest_start):
            state["earliest_start"] = start_ts
        if shutdown_ts is not None and (latest_shutdown is None or shutdown_ts > latest_shutdown):
            state["latest_shutdown"] = shutdown_ts
        if session_open:
            state["any_open"] = True
        if session_model:
            result["model"] = session_model

    for target, result in results.items():
        state = timing[target]
        earliest_start = state["earliest_start"]
        if earliest_start is None:
            continue
        effective_end = now_ms if state["any_open"] else state["latest_shutdown"]
        if effective_end is None:
            effective_end = now_ms
        if effective_end is not None:
            result["started_at_ms"] = earliest_start
            result["ended_at_ms"] = None if state["any_open"] else effective_end
            result["total_elapsed_ms"] = max(0, effective_end - earliest_start)
    return results


def _read_worktree_session_usage(session_state_dir, worktree_path, now_ms=None):
    """Read Copilot usage for one task worktree."""
    key = _normalise_path_key(worktree_path) if worktree_path else ""
    usages = _read_worktree_session_usages(
        session_state_dir, [worktree_path], now_ms=now_ms
    )
    return usages.get(key, {
        "model": None,
        "total_tokens": 0,
        "total_elapsed_ms": 0,
        "started_at_ms": None,
        "ended_at_ms": None,
        "matched": False,
    })


def _accumulate_metric(session, key, matched, new_total, status):
    """Accumulate one cumulative {TOTAL, per_state} usage metric.

    Adds the non-negative increase in ``new_total`` since the last recorded
    ``TOTAL`` to ``per_state[status]`` and sets ``TOTAL`` to the new cumulative
    value. A lower reading (e.g. rotated logs, or a shutdown timestamp replacing
    a live ``now``) is ignored so the running totals never regress. When
    ``matched`` is false (no session yet), records an explicit null ``TOTAL``
    -- but only when no real total was ever captured -- so the UI shows a blank
    rather than a misleading 0.
    """
    metric = session.get(key)
    if not isinstance(metric, dict):
        metric = {}
        session[key] = metric
    per_state = metric.get("per_state")
    if not isinstance(per_state, dict):
        per_state = {}
        metric["per_state"] = per_state

    prev_raw = metric.get("TOTAL")
    prev_is_num = isinstance(prev_raw, (int, float)) and not isinstance(prev_raw, bool)

    if not matched:
        if not prev_is_num:
            metric["TOTAL"] = None
        return

    new_total = int(new_total or 0)
    prev_total = int(prev_raw) if prev_is_num else 0
    delta = new_total - prev_total
    if delta < 0:
        delta = 0
        new_total = prev_total

    try:
        prev_state = int(per_state.get(status))
    except (TypeError, ValueError):
        prev_state = 0
    per_state[status] = prev_state + delta
    metric["TOTAL"] = new_total


# Feature 3.6.17
def _record_worker_session_usage(store, task, now_ms=None, usage=None):
    """Feature ID: 3.6.17. Refresh a task's worker-session usage and elapsed time.

    Reads cumulative Copilot usage for the task's worktree and records it onto
    ``worker_session``. Token accounting remains per state; elapsed time is a
    single ``{TOTAL, started_at, ended_at}`` value. ``ended_at`` is null while
    the session is open, allowing the UI to advance the duration against the
    current time. Any read failure leaves existing values unchanged.
    """
    try:
        session = task.get("worker_session")
        if not isinstance(session, dict):
            return False
        worktree = session.get("worktree")
        if not worktree:
            return False
        if now_ms is None:
            now_ms = _now_ms()
        if usage is None:
            state_dir = _resolve_session_state_dir(store)
            if state_dir is None:
                return False
            usage = _read_worktree_session_usage(state_dir, worktree, now_ms=now_ms)

        before = json.dumps(session, sort_keys=True, default=str)
        status = str(task.get("status") or "").strip() or "InProgress"
        matched = bool(usage.get("matched"))

        _accumulate_metric(session, "tokens", matched, usage.get("total_tokens"), status)
        elapsed = session.get("elapsed")
        if not isinstance(elapsed, dict):
            elapsed = {}
            session["elapsed"] = elapsed
        if matched and usage.get("started_at_ms") is not None:
            elapsed["TOTAL"] = int(usage.get("total_elapsed_ms") or 0)
            elapsed["started_at"] = int(usage["started_at_ms"])
            elapsed["ended_at"] = (
                int(usage["ended_at_ms"]) if usage.get("ended_at_ms") is not None else None
            )
        elif not isinstance(elapsed.get("TOTAL"), (int, float)):
            elapsed.update({"TOTAL": None, "started_at": None, "ended_at": None})
        if matched and usage.get("model"):
            session["model"] = usage.get("model")

        after = json.dumps(session, sort_keys=True, default=str)
        return before != after
    except Exception as exc:  # noqa: BLE001 - usage recording must never fail the status update
        print(f"[task-manager] worker-session usage update skipped: {exc}")
        return False


def _apply_status_request_file(store, request_file):
    """Apply one status-update request file to the ledger and sync it to main.

    The request must be ``{"TASKS": [ ... ]}`` where each item is matched by
    ``id`` and restricted to the updatable fields. The ledger is loaded fresh,
    each update applied via :func:`_apply_task_update`, saved, then committed and
    pushed to ``main`` headlessly. Returns the list of applied task ids.
    """
    with request_file.open("r", encoding="utf-8") as handle:
        document = json.load(handle)

    if not isinstance(document, dict) or not isinstance(document.get("TASKS"), list):
        raise ValueError("update request must be an object with a TASKS list")
    update_tasks = document["TASKS"]
    if not update_tasks:
        raise ValueError("update request contains no tasks")

    tasks_path = store.tasks_path
    data = _load_tasks_store(tasks_path)
    tasks = data["TASKS"]

    applied_ids = []
    unmatched = []
    for update_task in update_tasks:
        result = _apply_task_update(tasks, update_task, allowed_fields=STATUS_UPDATE_ALLOWED_FIELDS)
        if result is None:
            unmatched.append(str((update_task or {}).get("id", "")).strip() or "(missing id)")
        else:
            # Capture the Copilot model + token consumption for this task (best
            # effort) so each status transition records the tokens spent to
            # reach it; never let a usage-read failure fail the status update.
            _record_worker_session_usage(store, result)
            applied_ids.append(str(result.get("id", "")))

    if not applied_ids:
        raise ValueError(f"no matching task for ids: {', '.join(unmatched)}")

    _save_tasks_store(tasks_path, data)

    # The ledger is already persisted locally; a git sync failure must NOT
    # re-fail the request (that would reprocess it and duplicate comments). The
    # local change is picked up by the next startup sync, so we only log it.
    try:
        _sync_task_store_to_git(
            store,
            message=f"Apply task status update ({', '.join(applied_ids)})",
            headless=True,
            branch=_resolve_ledger_branch(store),
        )
    except RuntimeError as exc:
        print(f"[task-manager] git sync after status update failed: {exc}")

    return applied_ids


# Feature 3.6.10
def _process_status_inbox(store):
    """Feature ID: 3.6.10. Apply pending status-update requests to the ledger on main.

    Each ``pending/*.json`` request is applied in name order, then moved to
    ``processed/`` on success or ``failed/`` on error. Processing each file
    exactly once keeps the channel idempotent and restart-safe.
    """
    paths = _status_inbox_dir(store)
    if paths is None:
        return {"processed": 0, "failed": 0}

    request_files = sorted(p for p in paths["pending"].glob("*.json") if p.is_file())
    processed_count = 0
    failed_count = 0
    for request_file in request_files:
        try:
            applied_ids = _apply_status_request_file(store, request_file)
        except Exception as exc:  # noqa: BLE001 - any failure routes to failed/
            failed_count += 1
            _move_inbox_file(request_file, paths["failed"])
            print(f"[task-manager] status request {request_file.name} failed: {exc}")
            continue
        processed_count += 1
        _move_inbox_file(request_file, paths["processed"])
        print(f"[task-manager] applied status request {request_file.name} -> {', '.join(applied_ids)}")

    return {"processed": processed_count, "failed": failed_count}


# Feature 3.6.11
def _status_inbox_watcher(store_provider, stop_event=None):
    """Feature ID: 3.6.11. Continuously sample the status store and apply requests.

    Runs as a daemon loop: each cycle resolves the live store, processes its
    pending requests when the channel is enabled, then sleeps the configured
    poll interval. Server restarts are safe because pending files persist.
    """
    while not (stop_event is not None and stop_event.is_set()):
        poll_seconds = 5
        try:
            store = store_provider() if callable(store_provider) else store_provider
            if store is not None:
                paths = _resolve_status_store_paths(store)
                poll_seconds = paths["poll_seconds"]
                if paths["enabled"]:
                    _process_status_inbox(store)
        except Exception as exc:  # noqa: BLE001 - keep the watcher alive
            print(f"[task-manager] status inbox watcher error: {exc}")
        if stop_event is not None:
            if stop_event.wait(poll_seconds):
                break
        else:
            time.sleep(poll_seconds)


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


def _write_agent_mcp_config(store, safe_task_id, config_dir):
    """Write a per-agent MCP config wiring the stdio status-queue server.

    When the durable task status store is enabled, this returns the path to a
    JSON file that registers ``scripts/status_queue_mcp.py`` as a local (stdio)
    MCP server for the worker agent, exposing the ``enqueue_status_update``
    tool. The server writes durable request files into the store's ``pending``
    directory; its lifecycle is bound to this one agent. Returns ``None`` when
    the status store is not enabled/configured (the agent then has no enqueue
    tool and falls back to writing request files directly).
    """
    paths = _resolve_status_store_paths(store)
    if not paths["enabled"] or paths["pending"] is None:
        return None

    pending_dir = Path(paths["pending"])
    pending_dir.mkdir(parents=True, exist_ok=True)

    config = {
        "mcpServers": {
            "task-status-queue": {
                "type": "local",
                "command": sys.executable,
                "args": [str(STATUS_QUEUE_MCP_SCRIPT_PATH)],
                "env": {
                    "TASK_STATUS_PENDING_DIR": str(pending_dir),
                    "TASK_STATUS_TASK_ID": safe_task_id,
                    "TASK_STATUS_AUTHOR": f"{safe_task_id} worker",
                },
                "tools": ["*"],
            }
        }
    }

    config_dir = Path(config_dir)
    config_dir.mkdir(parents=True, exist_ok=True)
    config_path = config_dir / f"{safe_task_id}.mcp.json"
    config_path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return config_path


def _start_copilot_for_task(task, tasks_path, store, enable_full_read=False, enable_full_edit=False, enable_full_execution=False, mode="work"):
    copilot_cli = shutil.which("copilot") or shutil.which("github-copilot-cli")
    if not copilot_cli:
        raise RuntimeError("Copilot CLI is not available in PATH (expected 'copilot')")

    caller_root = store.base_dir
    # Worker prompt files ({task.id}.md) live in the configured prompt store
    # (APP.TASK_MANAGER.prompt_store); fall back to OUTPUT_PATH/OUTPUT_PREFIX or
    # the in-repo build/tasks location when no config is available.
    prompts_dir = _resolve_task_manager_path(store, "prompt_store")
    if prompts_dir is None:
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
    # Each task is worked in its own dedicated git worktree (a separate checkout
    # directory at <container>/<task-id>, a sibling of the main worktree that
    # shares the bare object store) so that concurrent task agents each operate
    # on their own ``<task-id>`` branch without sharing - or stomping on - a single
    # working tree. The launch script materialises the worktree (via -Worktree)
    # and runs the session there. Reviews reuse the task's existing worktree.
    worktree_path = _worktree_path_for_task(store, safe_task_id)
    # Name each Copilot window "<title> (<id>)" so it is identifiable by its
    # task (and unique via the task id) rather than the auto-generated session
    # name the Copilot CLI would otherwise apply.
    window_title = f"{session_name} ({safe_task_id})"
    prompt_suffix = "-review" if is_review else ""
    prompt_path = prompts_dir / f"{safe_task_id}{prompt_suffix}.md"
    # Point the worker's prompt at its worktree so all the work it does lands in
    # the isolated checkout rather than the main tree.
    workspace_override = str(worktree_path)
    prompt_text = (
        _build_review_prompt(task, tasks_path, store=store, workspace_override=workspace_override)
        if is_review
        else _build_copilot_prompt(task, tasks_path, store=store, workspace_override=workspace_override)
    )
    prompt_path.write_text(prompt_text, encoding="utf-8")

    # Work each task on its own short-lived, ad-hoc branch so concurrent tasks
    # do not mix their changes. The launch script creates and checks out this
    # branch (via -TaskBranch) inside the task's worktree before the worker
    # session begins. Reviews run against the existing branch/worktree, so no
    # new branch is requested for them.
    task_branch = None if is_review else _branch_name_for_task(safe_task_id)

    # Give the worker a per-agent stdio MCP server (the status queue) so it can
    # request ledger status/comment updates via a validated 'enqueue' tool
    # instead of hand-writing request files. The config is bound to this agent.
    mcp_config_path = _write_agent_mcp_config(store, safe_task_id, prompts_dir)

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
    if worktree_path is not None:
        launch_args.extend(["-Worktree", str(worktree_path)])
    if task_branch:
        launch_args.extend(["-TaskBranch", task_branch])
    if mcp_config_path is not None:
        launch_args.extend(["-McpConfig", str(mcp_config_path)])
    if enable_full_read:
        launch_args.extend(["-EnableFullRead"])
    if enable_full_edit:
        launch_args.extend(["-EnableFullEdit"])
    if enable_full_execution:
        launch_args.extend(["-EnableFullExecution"])

    # The worker runs in its own console that intentionally outlives the server,
    # so it breaks away from the server's kill-on-close job (Feature 3.6.18).
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_CONSOLE | CREATE_BREAKAWAY_FROM_JOB
    process = subprocess.Popen(
        launch_args,
        cwd=str(caller_root),
        creationflags=creationflags,
    )

    # Record the launched session on the task so a later "Ready" review can
    # trace and re-focus this console window instead of starting from scratch.
    # Preserve previously recorded usage across relaunches and reviews.
    prior_session = task.get("worker_session") if isinstance(task.get("worker_session"), dict) else {}
    prior_tokens = prior_session.get("tokens")
    if not isinstance(prior_tokens, dict):
        prior_tokens = {"TOTAL": None, "per_state": {}}

    prior_elapsed = prior_session.get("elapsed")
    if not isinstance(prior_elapsed, dict):
        prior_elapsed = {"TOTAL": None, "started_at": None, "ended_at": None}

    task["worker_session"] = {
        "pid": getattr(process, "pid", None),
        "window_title": window_title,
        "mode": "review" if is_review else "work",
        "branch": task_branch,
        "worktree": worktree_path.as_posix() if worktree_path is not None else None,
        "prompt_file": prompt_path.as_posix(),
        "started_at": _now_iso(),
        "model": prior_session.get("model"),
        "tokens": prior_tokens,
        "elapsed": prior_elapsed,
    }
    return prompt_path


# ---------------------------------------------------------------------------
# Feature 3.6.18: server lifecycle (single-instance guard, kill-on-close job,
# browser heartbeat auto-shutdown, and one orderly-shutdown path).
# ---------------------------------------------------------------------------

# Windows creation flag that lets a child process leave the server's
# kill-on-close job so intentionally user-facing windows survive the server.
CREATE_BREAKAWAY_FROM_JOB = getattr(subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0x01000000)
INSTANCE_LOCK_FILENAME = "task_manager.lock"

# Shared lifecycle state populated by run(). Holds the live server, the thread
# stop events, and the store so any shutdown trigger (signal handler, atexit,
# /api/shutdown, or the heartbeat monitor) can reach them.
_LIFECYCLE = {"server": None, "stop_events": [], "store": None}
_SHUTDOWN_EVENT = threading.Event()

# Helper child PIDs the server owns (e.g. detached git-sync windows). The Windows
# job object is the hard-kill safety net; this registry drives cooperative reaping
# in the orderly-shutdown path (and is the only teardown available off-Windows).
_HELPER_CHILD_PIDS = set()
_HELPER_CHILD_LOCK = threading.Lock()

# Kept alive for the server's whole lifetime: closing this handle (on process
# exit) is what makes Windows kill the remaining processes in the job.
_KILL_ON_CLOSE_JOB = None


class PortInUseError(RuntimeError):
    """Raised when the configured Task Manager port is already bound."""


def _task_manager_cfg_value(store, key, default):
    """Read an APP.TASK_MANAGER.<key> scalar from the store config with a default."""
    app_cfg = getattr(getattr(store, "config", None), "APP", None)
    task_manager_cfg = getattr(app_cfg, "TASK_MANAGER", None) if app_cfg else None
    value = getattr(task_manager_cfg, key, default) if task_manager_cfg else default
    return default if value is None else value


def _heartbeat_seconds(store):
    """Client heartbeat interval (seconds) from config; sane lower bound of 1."""
    try:
        return max(1, int(_task_manager_cfg_value(store, "browser_heartbeat_seconds", 3)))
    except (TypeError, ValueError):
        return 3


def _heartbeat_timeout(store):
    """Server grace period (seconds) before a silent browser triggers shutdown."""
    try:
        return max(4, int(_task_manager_cfg_value(store, "browser_heartbeat_timeout", 12)))
    except (TypeError, ValueError):
        return 12


def _register_helper_pid(pid):
    """Track a helper child PID the server owns so it can be reaped on shutdown."""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return
    if pid <= 0:
        return
    with _HELPER_CHILD_LOCK:
        _HELPER_CHILD_PIDS.add(pid)


def _terminate_helper_children():
    """Best-effort teardown of tracked helper child processes (PID-targeted)."""
    with _HELPER_CHILD_LOCK:
        pids = list(_HELPER_CHILD_PIDS)
        _HELPER_CHILD_PIDS.clear()
    for pid in pids:
        try:
            if not _process_alive(pid):
                continue
            if sys.platform == "win32":
                # PID-targeted tree kill (never name-based).
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, check=False,
                )
            else:
                os.kill(int(pid), signal.SIGTERM)
        except Exception:
            pass


def _instance_lock_path(store):
    """Feature 3.6.18.1. Path to the machine-wide single-instance lock file
    (<status_queue_root>/task_manager.lock), or None when unconfigured."""
    root = _resolve_task_manager_path(store, "status_queue")
    if root is None:
        return None
    return root / INSTANCE_LOCK_FILENAME


def _read_lock_info(path):
    """Read a lock file into a dict, or None when missing/unreadable/malformed."""
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _acquire_instance_lock(store, host, port):
    """Feature 3.6.18.2. Acquire the single-instance lock.

    Returns the live holder's info dict when another running instance already
    holds the lock (so the caller refuses to start a duplicate). Otherwise
    (no lock, or a stale lock left by a dead pid) atomically claims the lock for
    this process and returns None. Returns None when single-instance is disabled.
    """
    if not bool(_task_manager_cfg_value(store, "single_instance", True)):
        return None
    path = _instance_lock_path(store)
    if path is None:
        return None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    existing = _read_lock_info(path)
    if existing and _process_alive(existing.get("pid")):
        return existing
    info = {"pid": os.getpid(), "host": host, "port": int(port), "started": _now_iso()}
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    try:
        with tmp.open("w", encoding="utf-8") as handle:
            json.dump(info, handle)
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return None
    return None


def _release_instance_lock(store):
    """Feature 3.6.18.3. Remove the single-instance lock when owned by this pid."""
    if store is None:
        return
    path = _instance_lock_path(store)
    if path is None:
        return
    info = _read_lock_info(path)
    if info and str(info.get("pid")) == str(os.getpid()):
        try:
            path.unlink()
        except OSError:
            pass


def _ensure_kill_on_close_job():
    """Feature 3.6.18.4. On Windows, assign this process to a Job Object with
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE so helper child processes are terminated
    by the OS when the server (the only job-handle holder) dies -- even on a hard
    kill. BREAKAWAY_OK lets intentionally user-facing windows opt out via the
    CREATE_BREAKAWAY_FROM_JOB flag. No-op (returns None) off-Windows or on error."""
    global _KILL_ON_CLOSE_JOB
    if sys.platform != "win32":
        return None
    if _KILL_ON_CLOSE_JOB is not None:
        return _KILL_ON_CLOSE_JOB
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_void_p),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JOB_OBJECT_LIMIT_BREAKAWAY_OK = 0x00000800
        JobObjectExtendedLimitInformation = 9

        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.CreateJobObjectW.argtypes = [wintypes.LPVOID, wintypes.LPCWSTR]
        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            return None

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = (
            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE | JOB_OBJECT_LIMIT_BREAKAWAY_OK
        )
        kernel32.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD,
        ]
        if not kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
        ):
            kernel32.CloseHandle(job)
            return None

        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            # Fails only if this process cannot be added to a new job; not fatal
            # (the cooperative shutdown path still reaps helpers).
            kernel32.CloseHandle(job)
            return None

        _KILL_ON_CLOSE_JOB = job
        return job
    except Exception:
        return None


def _shutdown_server(reason="shutdown"):
    """Feature 3.6.18.5. The single, idempotent orderly-shutdown path.

    Signals the watcher/monitor threads to stop, reaps tracked helper children,
    releases the single-instance lock, and stops the HTTP server from a worker
    thread (server.shutdown() must not run on a request-handler thread). Safe to
    call from a signal handler, atexit, a request handler, or the monitor.
    """
    if _SHUTDOWN_EVENT.is_set():
        return
    _SHUTDOWN_EVENT.set()
    print(f"[task-manager] shutting down ({reason})...")
    for event in list(_LIFECYCLE.get("stop_events") or []):
        try:
            event.set()
        except Exception:
            pass
    try:
        _terminate_helper_children()
    except Exception:
        pass
    try:
        _release_instance_lock(_LIFECYCLE.get("store"))
    except Exception:
        pass
    server = _LIFECYCLE.get("server")
    if server is not None:
        threading.Thread(target=server.shutdown, daemon=True).start()


# Grace window (seconds) after an unload beacon before a tab is considered gone.
# Shared by the lifecycle monitor and GET /api/sessions so both agree on which
# tabs are still alive.
_CLOSE_GRACE = 2.0


def _lifecycle_monitor(store, stop_event, timeout_seconds):
    """Feature 3.6.18.6 / 3.6.18.8. Watch the per-tab browser heartbeats and
    shut the server down only once *every* tab is gone. Acts only after at least
    one heartbeat has been seen, so a UI that never loads does not self-terminate.

    Each tab has its own session id; a tab is dropped when its unload beacon
    (POST /api/close) ages past the grace with no newer heartbeat (a real close),
    or when its heartbeats stop for the full timeout. A page reload's fresh
    heartbeat supersedes its own close beacon. The server stops when the live
    tab count reaches zero -- so closing one tab never tears the server out from
    under another that is still open."""
    while not stop_event.wait(1.0):
        if _SHUTDOWN_EVENT.is_set():
            return
        if not _TaskManagerHandler.heartbeat_seen:
            continue
        now = time.monotonic()
        live = _TaskManagerHandler._live_session_count(now, timeout_seconds, _CLOSE_GRACE)
        if live == 0:
            _shutdown_server(reason="browser closed (all tabs gone)")
            return


def _probe_active_sessions(url):
    """Feature 3.6.18.8: ask an already-running server how many UI tabs are
    currently alive (GET <url>api/sessions -> {"active": N}). Returns the count,
    or None when it can't be determined (unreachable, or an older server without
    the endpoint) so callers can fall back to the previous open-a-tab behaviour."""
    try:
        endpoint = url.rstrip("/") + "/api/sessions"
        with urllib.request.urlopen(endpoint, timeout=2) as response:
            data = json.loads(response.read().decode("utf-8") or "{}")
        active = data.get("active")
        return int(active) if active is not None else None
    except Exception:
        return None


def _open_browser(url):
    """Open the UI in a browser window that breaks away from the kill-on-close
    job so the browser is not torn down with the server (the UI closes itself via
    its own heartbeat when the server stops)."""
    if sys.platform == "win32":
        creationflags = CREATE_BREAKAWAY_FROM_JOB
        browser_candidates = [
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        ]
        browser_path = next((p for p in browser_candidates if Path(p).exists()), None)
        try:
            if browser_path:
                subprocess.Popen([browser_path, "--new-window", url], shell=False, creationflags=creationflags)
            else:
                subprocess.Popen(["cmd", "/c", "start", "", url], shell=False, creationflags=creationflags)
        except OSError:
            webbrowser.open(url, new=1, autoraise=True)
    else:
        webbrowser.open(url, new=1, autoraise=True)


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
        "__HEARTBEAT_SECONDS__": str(_heartbeat_seconds(store)),
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
    # Browser heartbeat state (Feature 3.6.18). The UI is tracked *per tab*
    # (Feature 3.6.18.8): each open tab has its own session id and the server
    # only shuts down once *every* tab is gone. This keeps the single-instance
    # guard's "open a tab against the already-running server" behaviour safe --
    # closing one tab must never tear a server out from under another tab.
    #
    # ``sessions`` maps a client session id -> {"last": monotonic-heartbeat,
    # "closed": monotonic-unload-beacon}. A heartbeat newer than a session's
    # close beacon supersedes it (a page reload). Access is guarded by
    # ``sessions_lock`` because heartbeats, close beacons and the monitor thread
    # all touch it concurrently.
    sessions = {}
    sessions_lock = threading.Lock()
    # ``heartbeat_seen`` flips once any tab has beat at least once, so the
    # lifecycle monitor never shuts down a server whose UI never loaded.
    heartbeat_seen = False
    # Back-compat mirrors of the most recent activity across all sessions, kept
    # so older single-tab callers/tests that read these scalars still work.
    last_heartbeat = 0.0
    close_requested_at = 0.0

    @classmethod
    def _reset_sessions(cls):
        with cls.sessions_lock:
            cls.sessions = {}
        cls.heartbeat_seen = False
        cls.last_heartbeat = 0.0
        cls.close_requested_at = 0.0

    @classmethod
    def _touch_session(cls, sid, now):
        """Record a heartbeat for ``sid``; a fresh beat cancels a prior close."""
        with cls.sessions_lock:
            state = cls.sessions.setdefault(sid, {"last": 0.0, "closed": 0.0})
            state["last"] = now
            state["closed"] = 0.0
            cls.heartbeat_seen = True
            cls.last_heartbeat = now

    @classmethod
    def _close_session(cls, sid, now):
        """Arm a short-grace close for ``sid`` (unload beacon)."""
        with cls.sessions_lock:
            state = cls.sessions.setdefault(sid, {"last": 0.0, "closed": 0.0})
            state["closed"] = now
            cls.close_requested_at = now

    @classmethod
    def _live_session_count(cls, now, timeout_seconds, close_grace):
        """Return the number of tabs still considered alive, pruning any that
        closed (unload beacon past the grace) or went stale (no heartbeat within
        the timeout). Shared by the lifecycle monitor and GET /api/sessions."""
        with cls.sessions_lock:
            live = 0
            for sid in list(cls.sessions.keys()):
                state = cls.sessions[sid]
                last = state.get("last", 0.0)
                closed = state.get("closed", 0.0)
                # A close is pending purely by call order: every heartbeat zeroes
                # ``closed`` under the lock, so a reload's later heartbeat cancels
                # a close even when both land in the same monotonic tick (Windows
                # clock granularity makes timestamp comparison unreliable here).
                is_closed = closed > 0.0 and (now - closed) > close_grace
                is_stale = (now - last) > timeout_seconds
                if is_closed or is_stale:
                    del cls.sessions[sid]
                else:
                    live += 1
            return live

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

    def _get_tasks(self, refresh_usage=False):
        data = _load_tasks_store(self.__class__.store.tasks_path)
        if not refresh_usage:
            return data
        now_ms = _now_ms()
        tasks = data.get("TASKS") or []
        timed_tasks = []
        for task in tasks:
            session = task.get("worker_session")
            if isinstance(session, dict) and session.get("worktree"):
                timed_tasks.append(task)
        state_dir = _resolve_session_state_dir(self.__class__.store)
        usages = _read_worktree_session_usages(
            state_dir,
            [task["worker_session"]["worktree"] for task in timed_tasks],
            now_ms=now_ms,
        )
        for task in timed_tasks:
            worktree = task["worker_session"]["worktree"]
            usage = usages.get(_normalise_path_key(worktree))
            if usage is not None:
                _record_worker_session_usage(
                    self.__class__.store, task, now_ms=now_ms, usage=usage
                )
        return data

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

        if parsed.path == "/api/sessions":
            # Feature 3.6.18.8: report how many UI tabs are currently alive so a
            # second launch can decide whether to open another tab or just point
            # the user at the already-open one.
            timeout = _heartbeat_timeout(self.__class__.store)
            active = self.__class__._live_session_count(time.monotonic(), timeout, _CLOSE_GRACE)
            self._send_json(200, {"active": active})
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
            data = self._get_tasks(refresh_usage=True)
            self._send_json(200, {
                "tasks": data["TASKS"],
                "summary": _tasks_summary(data["TASKS"]),
                "tasks_path": self.__class__.store.tasks_path.resolve().as_posix(),
            })
            return

        if parsed.path == "/api/views":
            base_dir = _get_store_base_dir(self.__class__.store)
            self._send_json(200, _load_views(base_dir))
            return

        if parsed.path.startswith("/api/tasks/"):
            task_id = parsed.path.removeprefix("/api/tasks/").split("/", 1)[0]
            data = self._get_tasks(refresh_usage=True)
            _, task = _find_task(data["TASKS"], task_id)
            if task is None:
                self._send_json(404, {"error": "task not found", "task_id": task_id})
                return
            self._send_json(200, {"task": task})
            return

        self._send_json(404, {"error": "not found"})

    def do_POST(self):
        parsed = urlparse(self.path)

        # Lifecycle endpoints (Feature 3.6.18) are handled before loading the
        # task store: the browser heartbeat fires frequently and must stay cheap,
        # and shutdown must not depend on a readable ledger.
        if parsed.path == "/api/heartbeat":
            sid = (parse_qs(parsed.query).get("sid", [""])[0] or "_default").strip() or "_default"
            self.__class__._touch_session(sid, time.monotonic())
            self._read_body()
            self._send_json(200, {"ok": True})
            return

        if parsed.path == "/api/shutdown":
            # Explicit Close button: always an immediate, unconditional shutdown.
            try:
                self._read_body()
            except Exception:
                pass
            self._send_json(200, {"ok": True, "shutting_down": True})
            _shutdown_server(reason="close button")
            return

        if parsed.path == "/api/close":
            # Unload beacon: only *arms* a short-grace shutdown for this tab. A
            # page reload fires this too, so a fresh heartbeat for the same sid
            # arriving right after (the reloaded page) cancels it; a real tab
            # close sends no further heartbeat and the monitor drops the session
            # after the grace. The server only stops once *all* tabs are gone.
            try:
                self._read_body()
            except Exception:
                pass
            sid = (parse_qs(parsed.query).get("sid", [""])[0] or "_default").strip() or "_default"
            self.__class__._close_session(sid, time.monotonic())
            self._send_json(200, {"ok": True})
            return

        data = self._get_tasks()
        if parsed.path == "/api/tasks":
            task = _create_task(data["TASKS"], self._read_body(), self.__class__.store.tasks_path)
            self._write_tasks(data)
            # Immediately push new tasks to the ledger branch so a task created
            # in the UI is never left sitting uncommitted in the working tree.
            # The task is already persisted locally, so a sync failure is logged
            # but never fails the creation (the next sync flushes it to origin).
            try:
                _sync_task_store_to_git(
                    self.__class__.store,
                    message=f"Create task ({task.get('id', '')}) {task.get('title', '')}",
                    headless=True,
                    branch=_resolve_ledger_branch(self.__class__.store),
                )
            except RuntimeError as error:
                print(f"[task-manager] git sync after task creation failed: {error}")
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
            # If a branch already exists for this task, it may already be (or
            # have been) worked on. Do not silently start another worker: ask
            # the engineer to confirm first, and only proceed when they opt in
            # via 'confirm_existing_branch'.
            if not bool(payload.get("confirm_existing_branch", False)):
                if _task_branch_exists(self.__class__.store.base_dir, task_id):
                    branch = _branch_name_for_task(task_id)
                    self._send_json(409, {
                        "task_id": task_id,
                        "branch": branch,
                        "branch_exists": True,
                        "requires_confirmation": True,
                        "message": (
                            f"A branch '{branch}' already exists for this task, "
                            "so it may already be being worked on. Confirm to start "
                            "another worker on it, or cancel."
                        ),
                    })
                    return
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

        if parsed.path == "/api/views":
            payload = self._read_body()
            if not isinstance(payload, dict):
                self._send_json(400, {"error": "views payload must be an object"})
                return
            base_dir = _get_store_base_dir(self.__class__.store)
            saved = _save_views(base_dir, payload)
            self._send_json(200, saved)
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
    parser.add_argument(
        "--port", default=None, type=int,
        help="Explicit port to bind (overrides the per-app port band); fails fast when busy",
    )
    parser.add_argument("--browser-off", action="store_true", help="Do not open the UI in a browser after startup")
    parser.add_argument("--no-startup-sync", action="store_true", help="Do not auto-sync each app's task file with its git repo on startup")
    parser.add_argument("--no-status-inbox", action="store_true", help="Do not run the task status inbox watcher that applies agent status-update requests")
    parser.add_argument("--no-single-instance", action="store_true", help="Do not enforce the single-instance lock (allow a second server to start)")
    parser.add_argument("--no-auto-shutdown", action="store_true", help="Do not shut the server down when the browser UI is closed (disable heartbeat auto-shutdown)")
    return parser.parse_args(argv)


def _pick_available_port(host, port):
    """Bind the configured port and return it, failing fast when it is busy.

    Previously this silently fell back to an OS-assigned ephemeral port when the
    preferred one was busy, which hid duplicate servers accumulating in the
    background. It now raises PortInUseError so a second launch fails loudly and
    the single-instance guard can surface the already-running instance instead.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, int(port)))
            return sock.getsockname()[1]
    except OSError as exc:
        raise PortInUseError(f"Port {port} on {host} is already in use.") from exc


def _app_identity(store):
    """Best-effort stable identity for the running app (COMMON.APP_NAME, else the
    base directory's folder name, else 'app'). Drives the per-app port band."""
    base_dir = getattr(store, "base_dir", None)
    name = ""
    if base_dir is not None:
        try:
            name = _app_name_from_config(base_dir)
        except Exception:
            name = ""
        if not name:
            try:
                name = Path(base_dir).name
            except Exception:
                name = ""
    return name or "app"


def _app_port_band(store):
    """Feature 3.6.18.7. Deterministic, non-overlapping per-app port band.

    Each app hashes its identity (COMMON.APP_NAME) onto a slot in a bounded pool
    of fixed-width bands anchored at ``port_base``; the band it owns is therefore
    reproducible across launches with no central registry and nothing written
    into per-app config. BaseApp is pinned to slot 0 so its band is stable and
    every other app hashes into slots 1..N-1, avoiding BaseApp's band. Returns
    ``(band_start, band_end, pool_end)`` (all inclusive)."""
    base = int(_task_manager_cfg_value(store, "port_base", DEFAULT_PORT))
    width = max(1, int(_task_manager_cfg_value(store, "port_band_width", 10)))
    pool = max(1, int(_task_manager_cfg_value(store, "port_pool_bands", 200)))
    name = _app_identity(store)
    if name.lower() == "baseapp" or pool <= 1:
        slot = 0
    else:
        slot = 1 + (zlib.crc32(name.encode("utf-8")) % (pool - 1))
    band_start = base + slot * width
    band_end = band_start + width - 1
    pool_end = base + pool * width - 1
    return band_start, band_end, pool_end


def _select_server_port(store, host, explicit_port=None):
    """Feature 3.6.18.7. Choose the port this app's server should bind.

    An explicit ``--port`` override is honoured verbatim and fails fast when
    busy. Otherwise the app's deterministic band is scanned first, then the
    search hops forward through the remaining pool bands (wrapping to the lower
    pool once the top is reached) so a band collision self-heals onto a free
    port. When the whole pool is exhausted it raises PortInUseError rather than
    falling back to an arbitrary port, preserving the fail-loud guarantee."""
    if explicit_port is not None:
        return _pick_available_port(host, explicit_port)
    band_start, band_end, pool_end = _app_port_band(store)
    base = int(_task_manager_cfg_value(store, "port_base", DEFAULT_PORT))
    band_end = min(band_end, pool_end)
    # Own band first, then hop forward, then wrap to the lower pool bands.
    candidates = list(range(band_start, band_end + 1))
    candidates += list(range(band_end + 1, pool_end + 1))
    candidates += list(range(base, band_start))
    for candidate in candidates:
        try:
            return _pick_available_port(host, candidate)
        except PortInUseError:
            continue
    raise PortInUseError(
        f"No free port available in the pool {base}-{pool_end} on {host}; "
        "every band is occupied."
    )


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

    # Single-instance guard (Feature 3.6.18): refuse to start a second server
    # when a live instance already holds the lock; surface the running one.
    single_instance = (not args.no_single_instance) and bool(
        _task_manager_cfg_value(initial_store, "single_instance", True)
    )

    # Resolve the port up front from this app's deterministic band (or an
    # explicit --port override) so the instance lock records the real port and
    # separate apps never collide (Feature 3.6.18.7).
    try:
        selected_port = _select_server_port(initial_store, args.host, args.port)
    except PortInUseError as error:
        print(f"[task-manager] {error} Not starting a duplicate server.")
        return 1

    if single_instance:
        holder = _acquire_instance_lock(initial_store, args.host, selected_port)
        if holder is not None:
            existing_url = f"http://{holder.get('host', args.host)}:{holder.get('port', '?')}/"
            active = _probe_active_sessions(existing_url)
            print(
                f"A Task Manager for {initial_store.app_name} is already running "
                f"(pid {holder.get('pid')}) at {existing_url}."
            )
            if active and active > 0:
                # Feature 3.6.18.8: a UI tab is already open against the running
                # server. Don't open a second tab (a second tab shares the same
                # server and closing it could race the first). Point the user at
                # the existing one instead -- most terminals render this URL as a
                # clickable link.
                print(
                    f"A UI tab is already open ({active} active); not opening another. "
                    f"Open the existing instance here: {existing_url}"
                )
            elif not args.browser_off:
                # No UI is currently attached -- reconnect one.
                print("Opening the UI for the already-running instance.")
                _open_browser(existing_url)
            else:
                print(f"Open it here: {existing_url}")
            return 0

    if not args.no_startup_sync:
        _sync_selected_app_on_startup(initial_store)

    # Place this process in a kill-on-close job so helper children die with it.
    _ensure_kill_on_close_job()

    try:
        server = _create_server(args.host, selected_port, initial_store)
    except PortInUseError as error:
        print(f"[task-manager] {error} Another server may already be running; not starting a duplicate.")
        _release_instance_lock(initial_store)
        return 1

    _LIFECYCLE["server"] = server
    _LIFECYCLE["store"] = initial_store
    url = _build_query_url(server.server_address[0], server.server_address[1], initial_store)

    stop_events = []
    if not args.no_status_inbox:
        status_stop = threading.Event()
        stop_events.append(status_stop)
        status_thread = threading.Thread(
            target=_status_inbox_watcher,
            args=(lambda: _TaskManagerHandler.store,),
            kwargs={"stop_event": status_stop},
            daemon=True,
        )
        status_thread.start()

    # Browser-coupled auto-shutdown (Feature 3.6.18): watch the UI heartbeat and
    # stop the server when the tab/window is closed.
    auto_shutdown = (not args.no_auto_shutdown) and bool(
        _task_manager_cfg_value(initial_store, "shutdown_on_browser_close", True)
    )
    if auto_shutdown:
        monitor_stop = threading.Event()
        stop_events.append(monitor_stop)
        monitor_thread = threading.Thread(
            target=_lifecycle_monitor,
            args=(initial_store, monitor_stop, _heartbeat_timeout(initial_store)),
            daemon=True,
        )
        monitor_thread.start()

    _LIFECYCLE["stop_events"] = stop_events

    # Interrupted-session teardown (Feature 3.6.18): SIGINT/SIGTERM and process
    # exit all funnel through the one orderly-shutdown path.
    def _handle_signal(signum, _frame):
        _shutdown_server(reason=f"signal {signum}")

    for sig_name in ("SIGINT", "SIGTERM"):
        sig = getattr(signal, sig_name, None)
        if sig is not None:
            try:
                signal.signal(sig, _handle_signal)
            except (ValueError, OSError):
                pass
    atexit.register(lambda: _shutdown_server(reason="atexit"))

    if not args.browser_off:
        _open_browser(url)
    try:
        print(f"Task manager listening at {url}")
        print(f"Caller: {initial_store.app_name}")
        print(f"Tasks dir: {initial_store.tasks_dir.resolve()}")
        print(f"Editing: {initial_store.tasks_path.resolve()}")
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        _shutdown_server(reason="serve loop exit")
        for event in stop_events:
            try:
                event.set()
            except Exception:
                pass
        try:
            server.server_close()
        except Exception:
            pass
        _release_instance_lock(initial_store)
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
