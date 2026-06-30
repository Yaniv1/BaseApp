"""Feature ID: 5.3.1.7. Task manager interface prep test module."""

import json
import shutil
import tempfile
import threading
import time
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer
from pathlib import Path

import scripts.task_manager as task_manager
from scripts.task_manager import (
    _TaskManagerHandler,
    _append_comment,
    _apply_task_fields,
    _apply_task_update,
    _create_server,
    _create_task,
    _delete_task,
    _load_tasks_store,
    _next_task_id,
    _process_status_inbox,
    _save_tasks_store,
    _status_inbox_dir,
    _task_id_prefix_token,
    _update_task,
)
from types import SimpleNamespace


class _DummyStore:
    pass


def _build_result(status, message, criteria, features, data=None):
    payload = {
        "status": status,
        "message": message,
        "criteria": criteria,
        "features": features,
    }
    if data is not None:
        payload["data"] = data
    return payload


def _request_json(url, method="GET", payload=None):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json; charset=utf-8")
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            body = response.read().decode("utf-8")
            return response.status, json.loads(body) if body else {}
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8")
        return error.code, json.loads(body) if body else {}


def _request_text(url):
    with urllib.request.urlopen(url, timeout=10) as response:
        return response.status, response.read().decode("utf-8")


def test_build_copilot_prompt_uses_plain_task_request():
    task = {
        "id": "BASE-TASK-0001",
        "title": "Sample task",
        "type": "Feature",
        "priority": "Medium",
        "status": "ToDo",
        "description": "Implement the requested behavior.",
    }
    tasks_path = Path(__file__).resolve().parents[3] / "build" / "tasks" / "base.json"
    prompt = task_manager._build_copilot_prompt(task, tasks_path)
    assert prompt.startswith("Task ID: BASE-TASK-0001")
    assert "Implement the requested behavior." in prompt


def test_build_review_prompt_uses_review_template():
    task = {
        "id": "BASE-TASK-0042",
        "title": "Ready sample task",
        "type": "Feature",
        "priority": "High",
        "status": "Ready",
        "description": "Review the requested behavior.",
    }
    tasks_path = Path(__file__).resolve().parents[3] / "build" / "tasks" / "base.json"
    prompt = task_manager._build_review_prompt(task, tasks_path)
    assert "this is a dedicated review session" in prompt.lower()
    assert "Review the requested behavior." in prompt
    assert "BASE-TASK-0042" in prompt


def test_activate_copilot_window_handles_missing_or_dead_session(monkeypatch):
    assert task_manager._activate_copilot_window(None) is False
    assert task_manager._activate_copilot_window({}) is False
    monkeypatch.setattr(task_manager, "_process_alive", lambda pid: False)
    assert task_manager._activate_copilot_window({"pid": 1234, "window_title": "Copilot Task X"}) is False


def test_activate_copilot_window_focuses_live_window(monkeypatch):
    monkeypatch.setattr(task_manager, "_process_alive", lambda pid: True)
    monkeypatch.setattr(task_manager.sys, "platform", "win32")

    class DummyResult:
        returncode = 0

    calls = []
    monkeypatch.setattr(task_manager.subprocess, "run", lambda *a, **k: calls.append((a, k)) or DummyResult())
    assert task_manager._activate_copilot_window({"pid": 4321, "window_title": "Copilot Task X"}) is True
    assert calls, "expected an activation command to be issued"


def test_start_copilot_for_task_requests_dedicated_branch_via_launcher(monkeypatch, tmp_path):
    """Work mode must ask launch_task_agent.ps1 to set up the task's own branch."""
    launched = {}

    class DummyProc:
        pid = 13579

    class DummyStore:
        base_dir = tmp_path
        config = None

    def fake_popen(*args, **kwargs):
        launched["argv"] = args[0] if args else kwargs.get("args")
        return DummyProc()

    monkeypatch.setattr(task_manager.shutil, "which", lambda name: "copilot")
    monkeypatch.setattr(task_manager.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(task_manager, "_build_copilot_prompt", lambda task, tasks_path, store=None, workspace_override=None: "prompt")
    monkeypatch.setattr(task_manager, "_build_review_prompt", lambda task, tasks_path, store=None, workspace_override=None: "review")

    tasks_path = tmp_path / "tasks.json"
    tasks_path.write_text("{}", encoding="utf-8")

    # Work mode: the launcher is invoked with the task's own branch and it is
    # recorded on the worker session.
    work_task = {"id": "BASE-TASK-BRANCH-0001", "title": "Branch worker test"}
    task_manager._start_copilot_for_task(work_task, tasks_path, DummyStore(), mode="work")
    argv = launched["argv"]
    assert "-TaskBranch" in argv
    assert argv[argv.index("-TaskBranch") + 1] == "task/BASE-TASK-BRANCH-0001"
    assert work_task["worker_session"]["branch"] == "task/BASE-TASK-BRANCH-0001"
    assert str(argv[3]).endswith("launch_task_agent.ps1")

    # Review mode runs against the existing branch, so no branch is requested.
    review_task = {"id": "BASE-TASK-BRANCH-0002", "title": "Review", "status": "Ready"}
    task_manager._start_copilot_for_task(review_task, tasks_path, DummyStore(), mode="review")
    assert "-TaskBranch" not in launched["argv"]
    assert review_task["worker_session"]["branch"] is None


def test_launch_task_agent_script_creates_dedicated_branch(tmp_path):
    """End-to-end: launch_task_agent.ps1 checks out the task's own branch."""
    import subprocess as sp
    import shutil as _shutil

    pwsh = _shutil.which("pwsh")
    if not pwsh:
        import pytest
        pytest.skip("pwsh is not available")

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return sp.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)

    git("init")
    git("config", "user.email", "tester@example.com")
    git("config", "user.name", "Tester")
    git("checkout", "-B", "main")
    (repo / "seed.txt").write_text("seed", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "init")

    prompt = tmp_path / "prompt.md"
    prompt.write_text("do the task", encoding="utf-8")

    # A no-op stand-in for the Copilot CLI so the launcher runs to completion.
    stub = tmp_path / "copilot_stub.cmd"
    stub.write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")

    script = Path(task_manager.__file__).resolve().parent / "launch_task_agent.ps1"

    result = sp.run(
        [
            pwsh, "-NoProfile", "-File", str(script),
            "-WorkspaceRoot", str(repo),
            "-TaskId", "BASE-TASK-BRANCH-0001",
            "-PromptFile", str(prompt),
            "-TaskFile", str(repo / "tasks.json"),
            "-CopilotCli", str(stub),
            "-SessionName", "Branch worker test",
            "-WindowTitle", "",
            "-TaskBranch", "task/BASE-TASK-BRANCH-0001",
        ],
        capture_output=True, text=True, check=False,
    )

    current_branch = sp.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    # The launcher must have switched the working tree onto the task branch.
    assert current_branch == "task/BASE-TASK-BRANCH-0001", (result.stdout + result.stderr)


def test_launch_task_agent_script_creates_dedicated_worktree(tmp_path):
    """End-to-end: launch_task_agent.ps1 checks the task branch out into its own
    worktree (leaving the main working tree on its original branch)."""
    import subprocess as sp
    import shutil as _shutil

    pwsh = _shutil.which("pwsh")
    if not pwsh:
        import pytest
        pytest.skip("pwsh is not available")

    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return sp.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)

    git("init")
    git("config", "user.email", "tester@example.com")
    git("config", "user.name", "Tester")
    git("checkout", "-B", "main")
    (repo / "seed.txt").write_text("seed", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "init")

    prompt = tmp_path / "prompt.md"
    prompt.write_text("do the task", encoding="utf-8")

    stub = tmp_path / "copilot_stub.cmd"
    stub.write_text("@echo off\r\nexit /b 0\r\n", encoding="ascii")

    script = Path(task_manager.__file__).resolve().parent / "launch_task_agent.ps1"
    worktree = tmp_path / "repo.worktrees" / "BASE-TASK-WT-0001"

    result = sp.run(
        [
            pwsh, "-NoProfile", "-File", str(script),
            "-WorkspaceRoot", str(repo),
            "-TaskId", "BASE-TASK-WT-0001",
            "-PromptFile", str(prompt),
            "-TaskFile", str(repo / "tasks.json"),
            "-CopilotCli", str(stub),
            "-SessionName", "Worktree worker test",
            "-WindowTitle", "",
            "-TaskBranch", "task/BASE-TASK-WT-0001",
            "-Worktree", str(worktree),
        ],
        capture_output=True, text=True, check=False,
    )

    # The main working tree must stay on main (not be switched in place).
    main_branch = sp.run(
        ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert main_branch == "main", (result.stdout + result.stderr)

    # A dedicated worktree on the task branch must have been created.
    assert worktree.exists(), (result.stdout + result.stderr)
    worktree_branch = sp.run(
        ["git", "-C", str(worktree), "rev-parse", "--abbrev-ref", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    assert worktree_branch == "task/BASE-TASK-WT-0001", (result.stdout + result.stderr)


def test_start_copilot_for_task_records_worker_session(monkeypatch, tmp_path):
    class DummyProc:
        pid = 24680

    class DummyStore:
        base_dir = tmp_path
        config = None

    task = {"id": "BASE-TASK-7777", "title": "Session test"}
    tasks_path = tmp_path / "tasks.json"
    tasks_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(task_manager.shutil, "which", lambda name: "copilot")
    monkeypatch.setattr(task_manager.subprocess, "Popen", lambda *args, **kwargs: DummyProc())
    monkeypatch.setattr(task_manager, "_build_review_prompt", lambda task, tasks_path, store=None, workspace_override=None: "review prompt")

    task_manager._start_copilot_for_task(task, tasks_path, DummyStore(), mode="review")
    session = task.get("worker_session")
    assert session and session["pid"] == 24680
    assert session["mode"] == "review"
    assert session["window_title"] == "Session test (BASE-TASK-7777)"


def test_task_manager_template_has_ready_tab():
    template_path = Path(__file__).resolve().parents[3] / "resources" / "templates" / "task_manager.html"
    template = template_path.read_text(encoding="utf-8")
    assert 'data-status="Ready"' in template
    assert 'id="cnt-Ready"' in template
    assert "'ToDo', 'InProgress', 'Specified', 'Ready', 'Deployed', 'Approved', 'Done'" in template


def test_task_manager_template_has_deleted_tab():
    template_path = Path(__file__).resolve().parents[3] / "resources" / "templates" / "task_manager.html"
    template = template_path.read_text(encoding="utf-8")
    assert 'data-status="Deleted"' in template
    assert 'id="cnt-Deleted"' in template
    assert "'ToDo', 'InProgress', 'Specified', 'Ready', 'Deployed', 'Approved', 'Done', 'Deleted'" in template
    assert 'id="delete-task-btn"' in template


def test_task_manager_template_has_deployed_tab():
    template_path = Path(__file__).resolve().parents[3] / "resources" / "templates" / "task_manager.html"
    template = template_path.read_text(encoding="utf-8")
    assert 'data-status="Deployed"' in template
    assert 'id="cnt-Deployed"' in template
    assert ".s-deployed{" in template
    # Deployed (pushed to its own branch) precedes Done (merged into main) in the lifecycle order.
    assert "'ToDo', 'InProgress', 'Specified', 'Ready', 'Deployed', 'Approved', 'Done', 'Deleted'" in template


def test_task_manager_template_has_specified_and_approved_tabs():
    template_path = Path(__file__).resolve().parents[3] / "resources" / "templates" / "task_manager.html"
    template = template_path.read_text(encoding="utf-8")
    assert 'data-status="Specified"' in template
    assert 'id="cnt-Specified"' in template
    assert ".s-specified{" in template
    assert 'data-status="Approved"' in template
    assert 'id="cnt-Approved"' in template
    assert ".s-approved{" in template
    # Tab order: ToDo, InProgress, Specified, Ready, Deployed, Approved, Done.
    assert "'ToDo', 'InProgress', 'Specified', 'Ready', 'Deployed', 'Approved', 'Done', 'Deleted'" in template


def test_task_manager_template_has_other_catch_all_tab():
    template_path = Path(__file__).resolve().parents[3] / "resources" / "templates" / "task_manager.html"
    template = template_path.read_text(encoding="utf-8")
    assert 'data-status="Other"' in template
    assert 'id="cnt-Other"' in template
    assert ".s-other{" in template
    # 'Other' is the final, pathological catch-all tab in STATUS_TABS.
    assert "'Done', 'Deleted', 'Other'" in template
    # Unrecognized statuses normalize to 'Other' rather than collapsing into 'ToDo'.
    assert "return 'Other';" in template


def test_task_manager_template_has_task_search():
    """Feature 3.6 / BASE-REQ-014.14: a search bar above the status tabs locates a
    task by id, switches to its status tab, and opens its details."""
    template_path = Path(__file__).resolve().parents[3] / "resources" / "templates" / "task_manager.html"
    template = template_path.read_text(encoding="utf-8")
    # Search bar markup is present.
    assert 'id="task-search-input"' in template
    assert 'id="task-search-btn"' in template
    assert 'id="task-search-msg"' in template
    assert 'class="search-bar"' in template
    # The search bar is positioned above (before) the status tab bar.
    assert template.index('class="search-bar"') < template.index('class="tab-bar"')
    # Search logic is wired through dedicated helpers and the init sequence.
    assert "function searchTask(" in template
    assert "function findTaskById(" in template
    assert "function initSearch()" in template
    assert "initSearch();" in template


def test_task_manager_template_has_resizable_split_panes():
    """Feature 3.6: task list and task details are two stacked, independently
    scrolling blocks separated by a draggable divider (default 60/40 split)."""
    template_path = Path(__file__).resolve().parents[3] / "resources" / "templates" / "task_manager.html"
    template = template_path.read_text(encoding="utf-8")
    # Two stacked panes plus the draggable gutter between them.
    assert 'class="list-pane"' in template
    assert 'class="detail-pane"' in template
    assert 'id="split-divider"' in template
    # Each pane scrolls on its own.
    assert ".list-pane{" in template and "overflow:auto" in template
    assert ".detail-pane{" in template
    # The gutter is a horizontal, row-resize separator.
    assert "cursor:row-resize" in template
    assert 'aria-orientation="horizontal"' in template
    # Default division is 60% list / 40% details and is wired through JS.
    assert "DEFAULT_RATIO = 0.6" in template
    assert "function initSplit()" in template


def test_task_manager_template_has_column_sorting_and_filtering():
    """BASE-REQ-014.13: the task list supports per-column sorting
    (none/asc/desc) and per-column value filtering."""
    template_path = Path(__file__).resolve().parents[3] / "resources" / "templates" / "task_manager.html"
    template = template_path.read_text(encoding="utf-8")
    # Every column header is sortable and carries a sort indicator.
    for col in ("id", "title", "type", "priority"):
        assert 'data-col="' + col + '"' in template
        assert 'id="sort-ind-' + col + '"' in template
    assert 'class="sortable"' in template
    # Each column has a filter control: text inputs for id/title, selects for type/priority.
    assert 'id="filter-id"' in template
    assert 'id="filter-title"' in template
    assert 'id="filter-type"' in template
    assert 'id="filter-priority"' in template
    # Sorting cycles none -> asc -> desc and is wired through JS.
    assert "function cycleSort(" in template
    assert "function getVisibleTasks()" in template
    assert "function initColumnControls()" in template
    # Priority sorts by severity rank rather than alphabetically.
    assert "PRIORITY_RANK" in template


def test_delete_task_soft_deletes_then_removes():
    tasks = [
        {"id": "BASE-TASK-0001", "title": "Keep", "status": "ToDo"},
        {"id": "BASE-TASK-0002", "title": "Drop", "status": "InProgress"},
    ]
    task, removed = _delete_task(tasks, "BASE-TASK-0002")
    assert removed is False
    assert task["status"] == "Deleted"
    assert len(tasks) == 2

    task, removed = _delete_task(tasks, "BASE-TASK-0002")
    assert removed is True
    assert len(tasks) == 1
    assert all(t["id"] != "BASE-TASK-0002" for t in tasks)


def test_update_task_on_deleted_only_changes_status():
    tasks = [{
        "id": "BASE-TASK-0003",
        "title": "Original",
        "type": "Feature",
        "priority": "High",
        "status": "Deleted",
        "description": "Original description",
    }]
    updated = _update_task(tasks, "BASE-TASK-0003", {
        "title": "Hacked title",
        "priority": "Low",
        "description": "New description",
        "status": "ToDo",
    })
    assert updated["title"] == "Original"
    assert updated["priority"] == "High"
    assert updated["description"] == "Original description"
    assert updated["status"] == "ToDo"


def test_start_copilot_for_task_passes_permission_flags(monkeypatch, tmp_path):
    launched = []

    class DummyStore:
        base_dir = tmp_path
        config = None

    task = {"id": "BASE-TASK-9999", "title": "Permission test"}
    tasks_path = tmp_path / "tasks.json"
    tasks_path.write_text("{}", encoding="utf-8")

    monkeypatch.setattr(task_manager.shutil, "which", lambda name: "copilot")
    monkeypatch.setattr(task_manager.subprocess, "Popen", lambda *args, **kwargs: launched.append((args, kwargs)) or object())
    monkeypatch.setattr(task_manager, "_build_copilot_prompt", lambda task, tasks_path, store=None, workspace_override=None: "prompt")

    prompt_path = task_manager._start_copilot_for_task(
        task,
        tasks_path,
        DummyStore(),
        enable_full_read=True,
        enable_full_edit=True,
        enable_full_execution=True,
    )

    assert prompt_path.exists()
    assert launched, "expected Copilot launcher to be invoked"
    launch_args = launched[0][0][0]
    assert "-EnableFullRead" in launch_args
    assert "-EnableFullEdit" in launch_args
    assert "-EnableFullExecution" in launch_args


def test_task_manager_template_defaults_permission_checkboxes():
    template_path = Path(__file__).resolve().parents[3] / "resources" / "templates" / "task_manager.html"
    template = template_path.read_text(encoding="utf-8")
    assert 'id="full-read-perm" type="checkbox" checked' in template
    assert 'id="full-edit-perm" type="checkbox"' in template
    assert 'id="full-exec-perm" type="checkbox"' in template
    assert 'id="full-edit-perm" type="checkbox" checked' not in template


def test_sync_task_store_launches_sync_window_even_when_task_file_is_clean(monkeypatch):
    launched = []

    class DummyResult:
        def __init__(self, stdout="", stderr="", returncode=0):
            self.stdout = stdout
            self.stderr = stderr
            self.returncode = returncode

    class DummyStore:
        base_dir = Path(__file__).resolve().parents[3]
        tasks_path = Path(__file__).resolve().parents[3] / "build" / "tasks" / "base.json"

    def fake_run_git_command(repo_root, *args, check=False):
        if args[:2] == ("rev-parse", "--show-toplevel"):
            return DummyResult(stdout=str(repo_root) + "\n")
        if args[:2] == ("remote", "get-url"):
            return DummyResult(stdout="https://example.com/repo.git\n")
        if args[:2] == ("status", "--porcelain"):
            return DummyResult(stdout="")
        return DummyResult(stdout="")

    monkeypatch.setattr(task_manager, "_run_git_command", fake_run_git_command)
    monkeypatch.setattr(task_manager, "_detect_git_repo", lambda base_dir: {
        "repo_root": Path(base_dir).resolve(),
        "repo_remote": "https://example.com/repo.git",
        "repo_available": True,
        "repo_display": "https://example.com/repo.git",
    })
    monkeypatch.setattr(task_manager.subprocess, "Popen", lambda *args, **kwargs: launched.append(args) or object())

    result = task_manager._sync_task_store_to_git(DummyStore(), message="test")
    assert result["success"] is True
    assert launched


def test_sync_selected_app_on_startup_syncs_only_selected_store(monkeypatch, tmp_path):
    source_tasks = Path(__file__).resolve().parents[3] / "build" / "tasks" / "base.json"
    selected = tmp_path / "base.json"
    shutil.copyfile(source_tasks, selected)

    class DummyStore:
        base_dir = tmp_path
        tasks_path = selected

    synced = []
    monkeypatch.setattr(task_manager, "_detect_git_repo", lambda base_dir: {
        "repo_root": Path(base_dir).resolve(),
        "repo_remote": "https://example.com/repo.git",
        "repo_available": True,
        "repo_display": "https://example.com/repo.git",
    })
    monkeypatch.setattr(
        task_manager,
        "_sync_task_store_to_git",
        lambda store, message=None, wait=False: synced.append((Path(store.tasks_path).name, message, wait))
        or {"success": True, "message": "synced", "repo_root": str(store.base_dir)},
    )

    result = task_manager._sync_selected_app_on_startup(DummyStore())

    assert [name for name, _, _ in synced] == ["base.json"]
    assert synced[0][2] is True
    assert result["synced"] is True


def test_sync_selected_app_on_startup_skips_when_no_repo(monkeypatch, tmp_path):
    source_tasks = Path(__file__).resolve().parents[3] / "build" / "tasks" / "base.json"
    selected = tmp_path / "base.json"
    shutil.copyfile(source_tasks, selected)

    class DummyStore:
        base_dir = tmp_path
        tasks_path = selected

    called = []
    monkeypatch.setattr(task_manager, "_detect_git_repo", lambda base_dir: {
        "repo_root": None,
        "repo_remote": None,
        "repo_available": False,
        "repo_display": "(no linked git repo)",
    })
    monkeypatch.setattr(
        task_manager,
        "_sync_task_store_to_git",
        lambda store, message=None, wait=False: called.append(store) or {"success": True, "message": "synced", "repo_root": ""},
    )

    result = task_manager._sync_selected_app_on_startup(DummyStore())

    assert called == []
    assert result["synced"] is False


def test_activate_endpoint_focuses_or_starts_review(monkeypatch, tmp_path):
    source_tasks = Path(__file__).resolve().parents[3] / "build" / "tasks" / "base.json"
    temp_tasks = tmp_path / "base.json"
    shutil.copyfile(source_tasks, temp_tasks)

    data = _load_tasks_store(temp_tasks)
    ready = _create_task(data["TASKS"], {
        "title": "Ready review task",
        "description": "Awaiting review",
        "status": "Ready",
    }, temp_tasks)
    _save_tasks_store(temp_tasks, data)

    server = _create_server("127.0.0.1", 0, temp_tasks)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}/api/tasks/{ready['id']}/activate"
    try:
        # Traceable window: should activate in place without starting a session.
        monkeypatch.setattr(task_manager, "_activate_copilot_window", lambda session: True)
        status, payload = _request_json(base, method="POST", payload={})
        assert status == 200 and payload.get("mode") == "activated"

        # Not traceable: should fall back to a dedicated review session.
        started = {}
        monkeypatch.setattr(task_manager, "_activate_copilot_window", lambda session: False)
        monkeypatch.setattr(
            task_manager,
            "_start_copilot_for_task",
            lambda task, tasks_path, store, **kwargs: started.update(kwargs) or Path(tasks_path),
        )
        status, payload = _request_json(base, method="POST", payload={})
        assert status == 200 and payload.get("mode") == "review"
        assert started.get("mode") == "review"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_task_branch_exists_detects_existing_branch(tmp_path):
    """_task_branch_exists is True only once a task/<id> branch has been created."""
    import subprocess as sp

    repo = tmp_path

    def git(*args):
        return sp.run(["git", "-C", str(repo), *args], capture_output=True, text=True, check=True)

    git("init")
    git("config", "user.email", "tester@example.com")
    git("config", "user.name", "Tester")
    git("checkout", "-B", "main")
    (repo / "seed.txt").write_text("seed", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "init")

    assert task_manager._task_branch_exists(repo, "BASE-TASK-EXISTS-0001") is False
    git("branch", "task/BASE-TASK-EXISTS-0001")
    assert task_manager._task_branch_exists(repo, "BASE-TASK-EXISTS-0001") is True
    # A non-repository path is reported as having no branch.
    assert task_manager._task_branch_exists(repo / "missing", "BASE-TASK-EXISTS-0001") is False


def test_start_agent_requires_confirmation_when_branch_exists(monkeypatch, tmp_path):
    """Starting a worker on a task that already has a branch must be confirmed."""
    source_tasks = Path(__file__).resolve().parents[3] / "build" / "tasks" / "base.json"
    temp_tasks = tmp_path / "base.json"
    shutil.copyfile(source_tasks, temp_tasks)

    data = _load_tasks_store(temp_tasks)
    todo = _create_task(data["TASKS"], {
        "title": "Branch guard task",
        "description": "Already has a branch",
        "status": "ToDo",
    }, temp_tasks)
    _save_tasks_store(temp_tasks, data)

    started = []
    monkeypatch.setattr(task_manager, "_task_branch_exists", lambda base_dir, task_id: True)
    monkeypatch.setattr(
        task_manager,
        "_start_copilot_for_task",
        lambda task, tasks_path, store, **kwargs: started.append(kwargs) or Path(tasks_path),
    )

    server = _create_server("127.0.0.1", 0, temp_tasks)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    time.sleep(0.2)
    port = server.server_address[1]
    base = f"http://127.0.0.1:{port}/api/tasks/{todo['id']}/start-agent"
    try:
        # Without confirmation: the worker must NOT launch; the engineer is asked.
        status, payload = _request_json(base, method="POST", payload={})
        assert status == 409
        assert payload.get("requires_confirmation") is True
        assert payload.get("branch") == "task/" + todo["id"]
        assert started == []

        # With confirmation: the worker is launched.
        status, payload = _request_json(base, method="POST", payload={"confirm_existing_branch": True})
        assert status == 200
        assert len(started) == 1
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_create_server_falls_back_to_free_port_when_requested_port_is_busy():
    occupied_server = None
    server = None
    try:
        occupied_server = ThreadingHTTPServer(("127.0.0.1", 0), _TaskManagerHandler)
        requested_port = occupied_server.server_address[1]
        server = _create_server("127.0.0.1", requested_port, _DummyStore())
        assert server.server_address[1] != requested_port
    finally:
        if server is not None:
            server.server_close()
        if occupied_server is not None:
            occupied_server.server_close()


def test_task_manager_interface(manager=None, message=None, **kwargs):
    """Feature ID: 5.3.1.7.1. Verify the local task manager can load, create, update, and persist tasks.

    Covers features:
      - 3.6.1 (parse_args for the local task manager)
      - 3.6.2 (run/create server flow for the local task manager)
    """
    features = ["3.6.1", "3.6.2"]
    criteria = []
    workdir = tempfile.mkdtemp(prefix="basetask_ui_")
    source_tasks = Path(__file__).resolve().parents[3] / "build" / "tasks" / "base.json"
    temp_tasks = Path(workdir) / "base.json"
    shutil.copyfile(source_tasks, temp_tasks)

    server = None
    thread = None

    try:
        data = _load_tasks_store(temp_tasks)
        original_count = len(data["TASKS"])

        store_loaded = isinstance(data, dict) and isinstance(data.get("TASKS"), list) and original_count > 0
        criteria.append({
            "name": "task_store_loads_expected_json_shape",
            "operator": "eq",
            "actual": original_count,
            "expected": len(data["TASKS"]),
            "success": store_loaded,
            "status": "PASS" if store_loaded else "FAIL",
        })

        created = _create_task(data["TASKS"], {
            "title": "UI smoke task",
            "description": "First line\nSecond line",
            "type": "Feature",
            "priority": "Low",
            "status": "InProgress",
            "comment": "Seeded by the prep test",
        }, temp_tasks)
        _save_tasks_store(temp_tasks, data)
        reloaded = _load_tasks_store(temp_tasks)
        persisted = len(reloaded["TASKS"]) == original_count + 1 and any(task.get("id") == created["id"] for task in reloaded["TASKS"])
        criteria.append({
            "name": "create_task_persists_new_record",
            "operator": "eq",
            "actual": created["id"],
            "expected": created["id"],
            "success": persisted,
            "status": "PASS" if persisted else "FAIL",
        })

        # The task-id prefix must derive from the uppercased basename of the
        # tasks file, so a base.json store yields BASE-TASK-* and an app.json
        # store yields APP-TASK-*.
        base_prefix_ok = created["id"].startswith("BASE-TASK-")
        app_id = _next_task_id(data["TASKS"], Path(workdir) / "app.json")
        prefix_ok = base_prefix_ok and app_id.startswith("APP-TASK-") and _task_id_prefix_token("config/app.json") == "APP"
        criteria.append({
            "name": "task_id_prefix_uses_tasks_file_basename",
            "operator": "eq",
            "actual": app_id.split("-TASK-", 1)[0],
            "expected": "APP",
            "success": prefix_ok,
            "status": "PASS" if prefix_ok else "FAIL",
        })

        updated = _update_task(reloaded["TASKS"], created["id"], {
            "title": "UI smoke task updated",
            "status": "Done",
            "description": "Updated description",
        })
        updated_ok = updated["title"] == "UI smoke task updated" and updated["status"] == "Done" and updated["description"] == "Updated description"
        criteria.append({
            "name": "update_task_changes_fields",
            "operator": "eq",
            "actual": updated.get("status"),
            "expected": "Done",
            "success": updated_ok,
            "status": "PASS" if updated_ok else "FAIL",
        })

        commented = _append_comment(reloaded["TASKS"], created["id"], {"comment": "Added through API", "author": "Copilot"})
        comment_count = len(commented.get("comments", []))
        comments_ok = comment_count >= 2 and any(comment.get("content") == "Added through API" for comment in commented.get("comments", []))
        criteria.append({
            "name": "append_comment_persists_comment",
            "operator": "eq",
            "actual": comment_count,
            "expected": comment_count,
            "success": comments_ok,
            "status": "PASS" if comments_ok else "FAIL",
        })

        _save_tasks_store(temp_tasks, reloaded)

        server = _create_server("127.0.0.1", 0, temp_tasks)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        time.sleep(0.2)
        port = server.server_address[1]
        root_status, root_html = _request_text(f"http://127.0.0.1:{port}/")
        api_status, api_payload = _request_json(f"http://127.0.0.1:{port}/api/tasks")
        http_ok = root_status == 200 and api_status == 200 and "Task Manager</title>" in root_html and any(task.get("id") == created["id"] for task in api_payload.get("tasks", []))
        criteria.append({
            "name": "http_interface_round_trip",
            "operator": "eq",
            "actual": {"root_status": root_status, "api_status": api_status},
            "expected": {"root_status": 200, "api_status": 200},
            "success": http_ok,
            "status": "PASS" if http_ok else "FAIL",
        })

        http_created_status, http_created = _request_json(f"http://127.0.0.1:{port}/api/tasks", method="POST", payload={
            "title": "HTTP created task",
            "description": "Created through the UI service",
            "type": "Bug",
            "priority": "High",
            "status": "ToDo",
            "comment": "Created through the HTTP endpoint",
        })
        http_created_id = http_created.get("task", {}).get("id")
        http_update_status, _ = _request_json(f"http://127.0.0.1:{port}/api/tasks/{http_created_id}", method="PUT", payload={
            "status": "InProgress",
            "priority": "Medium",
            "title": "HTTP created task updated",
        })
        http_comment_status, _ = _request_json(f"http://127.0.0.1:{port}/api/tasks/{http_created_id}/comments", method="POST", payload={
            "comment": "Persisted through the endpoint",
        })
        final_store = _load_tasks_store(temp_tasks)
        final_task = next(task for task in final_store["TASKS"] if task.get("id") == http_created_id)
        http_round_trip_ok = (
            http_created_status == 201
            and http_update_status == 200
            and http_comment_status == 200
            and http_created_id
            and final_task.get("status") == "InProgress"
            and final_task.get("title") == "HTTP created task updated"
            and any(comment.get("content") == "Persisted through the endpoint" for comment in final_task.get("comments", []))
        )
        criteria.append({
            "name": "http_endpoints_persist_changes",
            "operator": "eq",
            "actual": {
                "create": http_created_status,
                "update": http_update_status,
                "comment": http_comment_status,
            },
            "expected": {
                "create": 201,
                "update": 200,
                "comment": 200,
            },
            "success": http_round_trip_ok,
            "status": "PASS" if http_round_trip_ok else "FAIL",
        })

        # Delete the HTTP-created task: first call soft-deletes (moves to the
        # Deleted status), the second call permanently removes it from the store.
        http_softdelete_status, http_softdelete = _request_json(
            f"http://127.0.0.1:{port}/api/tasks/{http_created_id}", method="DELETE")
        soft_store = _load_tasks_store(temp_tasks)
        soft_task = next((task for task in soft_store["TASKS"] if task.get("id") == http_created_id), None)
        http_harddelete_status, http_harddelete = _request_json(
            f"http://127.0.0.1:{port}/api/tasks/{http_created_id}", method="DELETE")
        hard_store = _load_tasks_store(temp_tasks)
        delete_ok = (
            http_softdelete_status == 200
            and http_softdelete.get("removed") is False
            and soft_task is not None
            and soft_task.get("status") == "Deleted"
            and http_harddelete_status == 200
            and http_harddelete.get("removed") is True
            and all(task.get("id") != http_created_id for task in hard_store["TASKS"])
        )
        criteria.append({
            "name": "http_delete_soft_then_permanent",
            "operator": "eq",
            "actual": {
                "soft_status": http_softdelete_status,
                "soft_removed": http_softdelete.get("removed"),
                "hard_status": http_harddelete_status,
                "hard_removed": http_harddelete.get("removed"),
            },
            "expected": {
                "soft_status": 200,
                "soft_removed": False,
                "hard_status": 200,
                "hard_removed": True,
            },
            "success": delete_ok,
            "status": "PASS" if delete_ok else "FAIL",
        })

        overall = "PASS" if all(item["success"] for item in criteria) else "FAIL"
        return _build_result(
            status=overall,
            message=message or "Validated task manager UI round-trips against build/tasks/base.json",
            criteria=criteria,
            features=features,
            data={
                "original_count": original_count,
                "created_task_id": created["id"],
                "http_created_task_id": http_created_id,
            },
        )
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None:
            thread.join(timeout=5)

def _make_status_store(tmp_dir):
    store_root = Path(tmp_dir) / "status_queue"
    config = SimpleNamespace(APP=SimpleNamespace(TASK_MANAGER=SimpleNamespace(
        status_queue=str(store_root),
        prompt_store=str(Path(tmp_dir) / "task_prompts"),
        result_store=str(Path(tmp_dir) / "task_results"),
        request_polling_frequency=1,
        enable=True,
    )))
    tasks_path = Path(tmp_dir) / "tasks.json"
    store = SimpleNamespace(config=config, tasks_path=str(tasks_path), base_dir=Path(tmp_dir))
    return store, store_root, tasks_path


def test_apply_task_update_overwrites_status_and_appends_comment():
    tasks = [{
        "id": "BASE-TASK-0001",
        "status": "ToDo",
        "comments": [{"author": "a", "content": "first", "timestamp": "t0"}],
    }]
    result = _apply_task_update(tasks, {
        "id": "BASE-TASK-0001",
        "status": "InProgress",
        "comments": [{"author": "worker", "content": "started", "timestamp": "t1"}],
    }, allowed_fields=task_manager.STATUS_UPDATE_ALLOWED_FIELDS)
    assert result["status"] == "InProgress"
    assert len(result["comments"]) == 2
    assert result["comments"][0]["content"] == "first"
    assert result["comments"][1]["content"] == "started"


def test_apply_task_update_updates_dict_field_recursively():
    tasks = [{
        "id": "T1",
        "status": "InProgress",
        "worker_session": {"pid": 1, "mode": "work", "window_title": "old"},
    }]
    result = _apply_task_update(tasks, {
        "id": "T1",
        "worker_session": {"pid": 99, "started_at": "t2"},
    }, allowed_fields=task_manager.STATUS_UPDATE_ALLOWED_FIELDS)
    session = result["worker_session"]
    assert session["pid"] == 99
    assert session["mode"] == "work"
    assert session["started_at"] == "t2"


def test_apply_task_update_unknown_id_returns_none():
    tasks = [{"id": "T1", "status": "ToDo"}]
    assert _apply_task_update(tasks, {"id": "T9", "status": "Done"}) is None


def test_apply_task_update_ignores_unlisted_fields():
    tasks = [{"id": "T1", "status": "ToDo", "title": "keep"}]
    result = _apply_task_update(tasks, {
        "id": "T1", "status": "Done", "title": "hacked", "priority": "Low",
    }, allowed_fields=task_manager.STATUS_UPDATE_ALLOWED_FIELDS)
    assert result["status"] == "Done"
    assert result["title"] == "keep"
    assert "priority" not in result


def test_apply_task_fields_combines_lists_and_overwrites_scalars():
    existing = {"status": "ToDo", "tags": ["a"]}
    _apply_task_fields(existing, {"status": "Done", "tags": ["b", "c"]})
    assert existing["status"] == "Done"
    assert existing["tags"] == ["a", "b", "c"]


def test_process_status_inbox_applies_request_and_moves_to_processed(monkeypatch, tmp_path):
    store, _store_root, tasks_path = _make_status_store(tmp_path)
    _save_tasks_store(tasks_path, {"TASKS": [
        {"id": "BASE-TASK-0001", "status": "ToDo", "comments": []}
    ]})
    monkeypatch.setattr(task_manager, "_sync_task_store_to_git", lambda *a, **k: {"message": "ok"})

    paths = _status_inbox_dir(store)
    request = {"TASKS": [{
        "id": "BASE-TASK-0001",
        "status": "InProgress",
        "comments": [{"author": "worker", "content": "started", "timestamp": "t1"}],
    }]}
    (paths["pending"] / "req1.json").write_text(json.dumps(request), encoding="utf-8")

    summary = _process_status_inbox(store)
    assert summary == {"processed": 1, "failed": 0}

    data = _load_tasks_store(tasks_path)
    task = data["TASKS"][0]
    assert task["status"] == "InProgress"
    assert task["comments"][-1]["content"] == "started"
    assert not list(paths["pending"].glob("*.json"))
    assert (paths["processed"] / "req1.json").exists()


def test_process_status_inbox_routes_unmatched_to_failed(monkeypatch, tmp_path):
    store, _store_root, tasks_path = _make_status_store(tmp_path)
    _save_tasks_store(tasks_path, {"TASKS": [{"id": "BASE-TASK-0001", "status": "ToDo"}]})
    monkeypatch.setattr(task_manager, "_sync_task_store_to_git", lambda *a, **k: {"message": "ok"})

    paths = _status_inbox_dir(store)
    (paths["pending"] / "bad.json").write_text(
        json.dumps({"TASKS": [{"id": "NOPE", "status": "Done"}]}), encoding="utf-8")

    summary = _process_status_inbox(store)
    assert summary == {"processed": 0, "failed": 1}
    assert (paths["failed"] / "bad.json").exists()
    # The unmatched request must not mutate the ledger.
    assert _load_tasks_store(tasks_path)["TASKS"][0]["status"] == "ToDo"


def test_process_status_inbox_processes_each_file_once(monkeypatch, tmp_path):
    store, _store_root, tasks_path = _make_status_store(tmp_path)
    _save_tasks_store(tasks_path, {"TASKS": [{"id": "T1", "status": "ToDo", "comments": []}]})
    monkeypatch.setattr(task_manager, "_sync_task_store_to_git", lambda *a, **k: {"message": "ok"})

    paths = _status_inbox_dir(store)
    (paths["pending"] / "r.json").write_text(
        json.dumps({"TASKS": [{"id": "T1", "comments": [
            {"author": "w", "content": "c", "timestamp": "t"}]}]}), encoding="utf-8")

    _process_status_inbox(store)
    _process_status_inbox(store)

    data = _load_tasks_store(tasks_path)
    assert len(data["TASKS"][0]["comments"]) == 1


def test_status_inbox_dir_disabled_returns_none(tmp_path):
    store, _store_root, _tasks_path = _make_status_store(tmp_path)
    store.config.APP.TASK_MANAGER.enable = False
    assert _status_inbox_dir(store) is None


# ---------------------------------------------------------------------------
# Per-agent stdio enqueue MCP server (scripts/status_queue_mcp.py)
# ---------------------------------------------------------------------------

import scripts.status_queue_mcp as status_queue_mcp


def test_mcp_build_request_includes_only_supplied_fields():
    req = status_queue_mcp.build_status_update_request("T1", status="InProgress")
    assert req == {"TASKS": [{"id": "T1", "status": "InProgress"}]}

    req = status_queue_mcp.build_status_update_request(
        "T1", comment="hello", author="me", timestamp="t0")
    assert req["TASKS"][0] == {
        "id": "T1",
        "comments": [{"author": "me", "content": "hello", "timestamp": "t0"}],
    }


def test_mcp_build_request_defaults_author_to_worker():
    req = status_queue_mcp.build_status_update_request("T1", comment="hi", timestamp="t0")
    assert req["TASKS"][0]["comments"][0]["author"] == "T1 worker"


def test_mcp_build_request_requires_status_or_comment():
    import pytest

    with pytest.raises(ValueError):
        status_queue_mcp.build_status_update_request("T1")
    with pytest.raises(ValueError):
        status_queue_mcp.build_status_update_request("", status="Done")


def test_mcp_write_request_atomically_is_valid_and_unique(tmp_path):
    pending = tmp_path / "pending"
    req = {"TASKS": [{"id": "T1", "status": "Ready"}]}
    p1 = status_queue_mcp.write_request_atomically(pending, req, "T1")
    p2 = status_queue_mcp.write_request_atomically(pending, req, "T1")

    assert p1.exists() and p2.exists() and p1 != p2
    # No half-written temp files are left behind.
    assert not list(pending.glob("*.tmp"))
    assert json.loads(p1.read_text(encoding="utf-8")) == req


def test_mcp_enqueue_output_is_consumed_by_inbox_processor(monkeypatch, tmp_path):
    """The file the MCP enqueue writes must be applied by the Task Manager."""
    store, _store_root, tasks_path = _make_status_store(tmp_path)
    _save_tasks_store(tasks_path, {"TASKS": [
        {"id": "BASE-TASK-0001", "status": "ToDo", "comments": []}
    ]})
    monkeypatch.setattr(task_manager, "_sync_task_store_to_git", lambda *a, **k: {"message": "ok"})

    paths = _status_inbox_dir(store)
    monkeypatch.setenv("TASK_STATUS_PENDING_DIR", str(paths["pending"]))
    monkeypatch.setenv("TASK_STATUS_TASK_ID", "BASE-TASK-0001")
    monkeypatch.setenv("TASK_STATUS_AUTHOR", "BASE-TASK-0001 worker")

    result = status_queue_mcp._handle_enqueue({"status": "InProgress", "comment": "started"})
    assert result["isError"] is False
    assert list(paths["pending"].glob("*.json"))

    summary = _process_status_inbox(store)
    assert summary == {"processed": 1, "failed": 0}

    task = _load_tasks_store(tasks_path)["TASKS"][0]
    assert task["status"] == "InProgress"
    assert task["comments"][-1]["content"] == "started"
    assert task["comments"][-1]["author"] == "BASE-TASK-0001 worker"


def test_mcp_enqueue_errors_without_configuration(monkeypatch):
    monkeypatch.delenv("TASK_STATUS_PENDING_DIR", raising=False)
    monkeypatch.setenv("TASK_STATUS_TASK_ID", "T1")
    result = status_queue_mcp._handle_enqueue({"status": "Done"})
    assert result["isError"] is True


def test_mcp_dispatch_initialize_and_tools_list():
    init = status_queue_mcp._dispatch("initialize", {"protocolVersion": "2025-06-18"})
    assert init["protocolVersion"] == "2025-06-18"
    assert init["serverInfo"]["name"] == status_queue_mcp.SERVER_NAME

    listing = status_queue_mcp._dispatch("tools/list", {})
    names = [tool["name"] for tool in listing["tools"]]
    assert "enqueue_status_update" in names


def test_mcp_dispatch_unknown_method_and_tool_raise():
    import pytest

    with pytest.raises(status_queue_mcp._RpcError):
        status_queue_mcp._dispatch("does/not/exist", {})
    with pytest.raises(status_queue_mcp._RpcError):
        status_queue_mcp._dispatch("tools/call", {"name": "nope", "arguments": {}})


def test_start_copilot_for_task_wires_status_queue_mcp(monkeypatch, tmp_path):
    """When the status store is enabled, the launcher gets a per-agent MCP config."""
    store, _store_root, tasks_path = _make_status_store(tmp_path)
    tasks_path.write_text("{}", encoding="utf-8")
    store.config.COMMON = SimpleNamespace(OUTPUT_PATH=str(tmp_path / "out"))

    launched = {}

    class DummyProc:
        pid = 24680

    def fake_popen(*args, **kwargs):
        launched["argv"] = args[0] if args else kwargs.get("args")
        return DummyProc()

    monkeypatch.setattr(task_manager.shutil, "which", lambda name: "copilot")
    monkeypatch.setattr(task_manager.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(task_manager, "_build_copilot_prompt", lambda task, tasks_path, store=None, workspace_override=None: "prompt")

    task = {"id": "BASE-TASK-MCP-0001", "title": "MCP wiring test"}
    task_manager._start_copilot_for_task(task, tasks_path, store, mode="work")

    argv = launched["argv"]
    assert "-McpConfig" in argv
    config_path = Path(argv[argv.index("-McpConfig") + 1])
    assert config_path.exists()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    server = config["mcpServers"]["task-status-queue"]
    assert server["type"] == "local"
    assert server["args"][0].endswith("status_queue_mcp.py")
    assert server["env"]["TASK_STATUS_TASK_ID"] == "BASE-TASK-MCP-0001"
    assert Path(server["env"]["TASK_STATUS_PENDING_DIR"]).name == "pending"


def test_start_copilot_for_task_omits_mcp_when_store_disabled(monkeypatch, tmp_path):
    store, _store_root, tasks_path = _make_status_store(tmp_path)
    store.config.APP.TASK_MANAGER.enable = False
    tasks_path.write_text("{}", encoding="utf-8")
    store.config.COMMON = SimpleNamespace(OUTPUT_PATH=str(tmp_path / "out"))

    launched = {}

    class DummyProc:
        pid = 24681

    monkeypatch.setattr(task_manager.shutil, "which", lambda name: "copilot")
    monkeypatch.setattr(task_manager.subprocess, "Popen",
                        lambda *a, **k: launched.update(argv=a[0] if a else k.get("args")) or DummyProc())
    monkeypatch.setattr(task_manager, "_build_copilot_prompt", lambda task, tasks_path, store=None, workspace_override=None: "prompt")

    task = {"id": "BASE-TASK-MCP-0002", "title": "MCP disabled test"}
    task_manager._start_copilot_for_task(task, tasks_path, store, mode="work")
    assert "-McpConfig" not in launched["argv"]


def test_build_copilot_prompt_renders_result_store(tmp_path):
    store, _store_root, _tasks_path = _make_status_store(tmp_path)
    tasks_path = Path(__file__).resolve().parents[3] / "build" / "tasks" / "base.json"
    task = {"id": "BASE-TASK-RS-0001", "title": "t", "status": "ToDo", "description": "d"}
    prompt = task_manager._build_copilot_prompt(task, tasks_path, store=store)
    assert (Path(tmp_path) / "task_results").as_posix() in prompt
    assert (Path(tmp_path) / "status_queue" / "pending").as_posix() in prompt


def test_start_copilot_for_task_writes_prompt_into_prompt_store(monkeypatch, tmp_path):
    store, _store_root, tasks_path = _make_status_store(tmp_path)
    tasks_path.write_text("{}", encoding="utf-8")

    class DummyProc:
        pid = 31415

    monkeypatch.setattr(task_manager.shutil, "which", lambda name: "copilot")
    monkeypatch.setattr(task_manager.subprocess, "Popen", lambda *a, **k: DummyProc())
    monkeypatch.setattr(task_manager, "_build_copilot_prompt", lambda task, tasks_path, store=None, workspace_override=None: "prompt")

    task = {"id": "BASE-TASK-PS-0001", "title": "prompt store test"}
    prompt_path = task_manager._start_copilot_for_task(task, tasks_path, store, mode="work")
    assert prompt_path.parent == (Path(tmp_path) / "task_prompts").resolve()
    assert prompt_path.exists()


def test_worktree_path_for_task_is_peer_under_app_container(tmp_path):
    store, _store_root, _tasks_path = _make_status_store(tmp_path)
    path = task_manager._worktree_path_for_task(store, "BASE-TASK-WT-0001")
    base = Path(tmp_path).resolve()
    # The worktree location is fixed: one level up from the main working tree
    # (base_dir) to the {APP} container, so the task worktree is a sibling of main.
    expected = base.parent / "BASE-TASK-WT-0001"
    assert path == expected
    # The worktree lives beside the main working tree, never nested inside it.
    assert base not in path.parents
    assert path.parent == base.parent


def test_worktree_root_is_parent_of_main_working_tree(tmp_path):
    store, _store_root, _tasks_path = _make_status_store(tmp_path)
    base = Path(tmp_path).resolve()
    # The worktree root is fixed (not configurable): always the {APP} container,
    # i.e. the parent of the main working tree.
    assert task_manager._worktree_root(store) == base.parent


def test_start_copilot_for_task_passes_worktree_to_launcher(monkeypatch, tmp_path):
    store, _store_root, tasks_path = _make_status_store(tmp_path)
    tasks_path.write_text("{}", encoding="utf-8")

    captured = {}

    class DummyProc:
        pid = 27182

    def fake_popen(args, *a, **k):
        captured["args"] = args
        return DummyProc()

    monkeypatch.setattr(task_manager.shutil, "which", lambda name: "copilot")
    monkeypatch.setattr(task_manager.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(task_manager, "_build_copilot_prompt", lambda task, tasks_path, store=None, workspace_override=None: "prompt")

    task = {"id": "BASE-TASK-WT-0003", "title": "worktree wiring"}
    task_manager._start_copilot_for_task(task, tasks_path, store, mode="work")

    args = captured["args"]
    assert "-Worktree" in args
    worktree_value = args[args.index("-Worktree") + 1]
    expected = Path(tmp_path).resolve().parent / "BASE-TASK-WT-0003"
    assert Path(worktree_value) == expected
    assert task["worker_session"]["worktree"] == expected.as_posix()


def test_build_copilot_prompt_uses_workspace_override(tmp_path):
    store, _store_root, _tasks_path = _make_status_store(tmp_path)
    tasks_path = Path(__file__).resolve().parents[3] / "build" / "tasks" / "base.json"
    task = {"id": "BASE-TASK-WT-0004", "title": "t", "status": "ToDo", "description": "d"}
    override = (Path(tmp_path) / "wt-override")
    prompt = task_manager._build_copilot_prompt(task, tasks_path, store=store, workspace_override=str(override))
    assert override.resolve().as_posix() in prompt


def test_build_copilot_prompt_routes_template_by_type(tmp_path):
    """A PullBase task must render from the pullbase.md instruction template."""
    store, _store_root, _tasks_path = _make_status_store(tmp_path)
    store.config.APP.TASK_MANAGER.templates = {
        "Feature": "task.md", "Bug": "task.md", "PullBase": "pullbase.md", "default": "task.md",
    }
    tasks_path = Path(__file__).resolve().parents[3] / "build" / "tasks" / "base.json"
    task = {"id": "BASE-TASK-PB-0001", "title": "PullBase 2026-06-26", "type": "PullBase",
            "status": "ToDo", "description": "Pull the latest base."}
    prompt = task_manager._build_copilot_prompt(task, tasks_path, store=store)
    assert "## PullBase Workflow" in prompt
    assert "scripts/pullbase.py" in prompt


def test_build_copilot_prompt_defaults_to_task_md_for_unmapped_type(tmp_path):
    """An unmapped type falls back to the 'default' template (task.md)."""
    store, _store_root, _tasks_path = _make_status_store(tmp_path)
    store.config.APP.TASK_MANAGER.templates = {"PullBase": "pullbase.md", "default": "task.md"}
    tasks_path = Path(__file__).resolve().parents[3] / "build" / "tasks" / "base.json"
    task = {"id": "BASE-TASK-FT-0001", "title": "A feature", "type": "Feature",
            "status": "ToDo", "description": "Do the feature."}
    prompt = task_manager._build_copilot_prompt(task, tasks_path, store=store)
    assert prompt.startswith("Task ID: BASE-TASK-FT-0001")
    assert "PullBase task" not in prompt


def test_create_task_autofills_pullbase_title():
    """A PullBase task with no title gets an auto-filled 'PullBase {date}' title."""
    import datetime
    data = {"TASKS": []}
    created = _create_task(
        data["TASKS"], {"type": "PullBase", "description": "Pull base."},
        pullbase_type="PullBase", pullbase_title_format="PullBase {date}", pullbase_date_format="%Y-%m-%d",
    )
    expected = "PullBase " + datetime.datetime.utcnow().strftime("%Y-%m-%d")
    assert created["type"] == "PullBase"
    assert created["title"] == expected


def test_create_task_keeps_explicit_title_for_pullbase():
    """An explicitly provided title is preserved even for PullBase tasks."""
    data = {"TASKS": []}
    created = _create_task(
        data["TASKS"], {"type": "PullBase", "title": "Custom pull", "description": "d"},
        pullbase_type="PullBase", pullbase_title_format="PullBase {date}", pullbase_date_format="%Y-%m-%d",
    )
    assert created["title"] == "Custom pull"


def test_create_task_does_not_autofill_non_pullbase_title():
    """Non-PullBase tasks are never auto-titled."""
    data = {"TASKS": []}
    created = _create_task(
        data["TASKS"], {"type": "Feature", "description": "d"},
        pullbase_type="PullBase", pullbase_title_format="PullBase {date}", pullbase_date_format="%Y-%m-%d",
    )
    assert created["title"] == ""


# ---------------------------------------------------------------------------
# Live MCP server health check (utils.testutils.mcp_server_status)
# ---------------------------------------------------------------------------

import sys

import utils.testutils as testutils

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _make_tm_config(status_queue_root, prompt_store=None):
    task_manager_ns = SimpleNamespace(status_queue=str(status_queue_root))
    if prompt_store is not None:
        task_manager_ns.prompt_store = str(prompt_store)
    return SimpleNamespace(APP=SimpleNamespace(TASK_MANAGER=task_manager_ns))


def _write_agent_mcp_config(prompt_store, stem, script_path, pending_dir=None):
    """Write a per-agent <stem>.mcp.json wiring one stdio status-queue server."""
    prompt_store.mkdir(parents=True, exist_ok=True)
    env = {"TASK_STATUS_TASK_ID": stem}
    if pending_dir is not None:
        env["TASK_STATUS_PENDING_DIR"] = str(pending_dir)
    config = {
        "mcpServers": {
            "task-status-queue": {
                "type": "local",
                "command": sys.executable,
                "args": [str(script_path)],
                "env": env,
                "tools": ["*"],
            }
        }
    }
    (prompt_store / f"{stem}.mcp.json").write_text(
        json.dumps(config, indent=2), encoding="utf-8")


def test_mcp_server_status_reports_good_and_active_rate(tmp_path):
    queue_root = tmp_path / "status_queue"
    pending = queue_root / "pending"
    pending.mkdir(parents=True)
    (pending / "a.json").write_text("{}", encoding="utf-8")
    (pending / "b.json").write_text("{}", encoding="utf-8")
    (pending / "ignore.txt").write_text("x", encoding="utf-8")

    manager = SimpleNamespace(base_dir=str(PROJECT_ROOT))
    result = testutils.mcp_server_status(
        config=_make_tm_config(queue_root), manager=manager, timeout=20)

    assert result["status"] == "GOOD"
    assert result["data"]["servers_total"] == 1
    assert result["data"]["servers_up"] == 1
    assert result["data"]["servers_down"] == 0
    assert result["data"]["active_rate"] == 1.0
    assert result["data"]["queue_item_count"] == 2
    rate_criterion = next(c for c in result["criteria"] if c["name"] == "mcp_servers_active_rate")
    assert rate_criterion["status"] == "GOOD"


def test_mcp_server_status_discovers_all_agent_servers(tmp_path):
    queue_root = tmp_path / "status_queue"
    (queue_root / "pending").mkdir(parents=True)
    prompt_store = tmp_path / "prompts"
    real_script = PROJECT_ROOT / "scripts" / "status_queue_mcp.py"
    _write_agent_mcp_config(prompt_store, "TASK-1", real_script)
    _write_agent_mcp_config(prompt_store, "TASK-2", real_script)

    manager = SimpleNamespace(base_dir=str(PROJECT_ROOT))
    result = testutils.mcp_server_status(
        config=_make_tm_config(queue_root, prompt_store=prompt_store),
        manager=manager, timeout=20)

    assert result["status"] == "GOOD"
    assert result["data"]["servers_total"] == 2
    assert result["data"]["servers_up"] == 2
    assert result["data"]["active_rate"] == 1.0


def test_mcp_server_status_warns_when_one_server_down(tmp_path):
    queue_root = tmp_path / "status_queue"
    (queue_root / "pending").mkdir(parents=True)
    prompt_store = tmp_path / "prompts"
    real_script = PROJECT_ROOT / "scripts" / "status_queue_mcp.py"
    missing_script = tmp_path / "nope" / "missing_mcp.py"
    _write_agent_mcp_config(prompt_store, "UP-1", real_script)
    _write_agent_mcp_config(prompt_store, "DOWN-1", missing_script)

    manager = SimpleNamespace(base_dir=str(PROJECT_ROOT))
    result = testutils.mcp_server_status(
        config=_make_tm_config(queue_root, prompt_store=prompt_store),
        manager=manager, timeout=20)

    assert result["status"] == "WARN"
    assert result["data"]["servers_total"] == 2
    assert result["data"]["servers_up"] == 1
    assert result["data"]["servers_down"] == 1
    assert result["data"]["active_rate"] == 0.5


def test_mcp_server_status_fails_when_multiple_servers_down(tmp_path):
    queue_root = tmp_path / "status_queue"
    (queue_root / "pending").mkdir(parents=True)
    prompt_store = tmp_path / "prompts"
    missing_script = tmp_path / "nope" / "missing_mcp.py"
    _write_agent_mcp_config(prompt_store, "DOWN-1", missing_script)
    _write_agent_mcp_config(prompt_store, "DOWN-2", missing_script)

    manager = SimpleNamespace(base_dir=str(PROJECT_ROOT))
    result = testutils.mcp_server_status(
        config=_make_tm_config(queue_root, prompt_store=prompt_store),
        manager=manager, timeout=20)

    assert result["status"] == "FAIL"
    assert result["data"]["servers_total"] == 2
    assert result["data"]["servers_down"] == 2
    assert result["data"]["active_rate"] == 0.0


def test_mcp_server_status_warns_when_base_server_missing(tmp_path):
    manager = SimpleNamespace(base_dir=str(tmp_path))  # no scripts/ here
    result = testutils.mcp_server_status(
        config=_make_tm_config(tmp_path / "status_queue"), manager=manager, timeout=10)
    assert result["status"] == "WARN"
    assert result["data"]["servers_total"] == 1
    assert result["data"]["servers_down"] == 1
    assert result["data"]["queue_item_count"] == 0


def test_probe_mcp_server_handshake_against_real_server():
    script = str(PROJECT_ROOT / "scripts" / "status_queue_mcp.py")
    alive, detail = testutils._probe_mcp_server(script, None, task_id="hc", timeout=20)
    assert alive is True, detail


def test_sync_task_repo_isolates_ledger_commit_from_worker_tree(tmp_path):
    """Feature 3.7 / BASE-REQ-014.12 regression test.

    The headless status-sync must commit ONLY the task ledger to the ledger
    branch (``main``) without touching the worker's primary working tree. This
    reproduces the bug scenario: the primary tree is on a ``task/<id>`` branch
    with uncommitted edits to both the ledger and an unrelated worker file, and
    asserts the sync (1) pushes exactly one ledger-only commit to ``origin/main``,
    (2) leaves the worker's uncommitted file edit intact byte-for-byte, (3) keeps
    the primary HEAD on the task branch with no new commit, so the ledger commit
    never lands on the task branch.
    """
    import subprocess as sp
    import shutil as _shutil

    pwsh = _shutil.which("pwsh")
    if not pwsh:
        import pytest
        pytest.skip("pwsh is not available")
    if not _shutil.which("git"):
        import pytest
        pytest.skip("git is not available")

    origin = tmp_path / "origin.git"
    work = tmp_path / "work"
    origin.mkdir()
    work.mkdir()

    def git(*args, cwd=work, check=True):
        # safe.bareRepository=all lets the test query the bare origin via -C even
        # when the user's global config sets safe.bareRepository=explicit.
        return sp.run(
            ["git", "-c", "safe.bareRepository=all", "-C", str(cwd), *args],
            capture_output=True, text=True, check=check,
        )

    sp.run(["git", "init", "--bare", str(origin)], capture_output=True, text=True, check=True)
    git("init")
    git("config", "user.email", "tester@example.com")
    git("config", "user.name", "Tester")
    git("config", "core.autocrlf", "false")
    git("config", "commit.gpgsign", "false")
    git("checkout", "-B", "main")

    ledger_rel = "build/tasks/base.json"
    ledger_path = work / ledger_rel
    ledger_path.parent.mkdir(parents=True, exist_ok=True)
    ledger_path.write_text('{"TASKS": [{"id": "X", "status": "ToDo"}]}\n', encoding="utf-8")
    worker_file = work / "worker_code.py"
    worker_file.write_text("print('original')\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-m", "init")
    git("remote", "add", "origin", str(origin))
    git("push", "-u", "origin", "main")

    # Worker starts: primary tree is checked out on the task branch.
    git("checkout", "-b", "task/BASE-TASK-260626-0001")
    task_head_before = git("rev-parse", "HEAD").stdout.strip()

    # Server applies a ledger status update into the primary working tree
    # (uncommitted), and the worker has its own uncommitted code edit.
    new_ledger = '{"TASKS": [{"id": "X", "status": "InProgress"}]}\n'
    ledger_path.write_text(new_ledger, encoding="utf-8")
    worker_edit = "print('WORKER UNCOMMITTED EDIT')\n"
    worker_file.write_text(worker_edit, encoding="utf-8")

    count_before = int(git("rev-list", "--count", "main", cwd=origin).stdout.strip())

    script = PROJECT_ROOT / "scripts" / "sync_task_repo.ps1"
    result = sp.run(
        [
            pwsh, "-NoProfile", "-File", str(script),
            "-RepoRoot", str(work),
            "-TaskFile", ledger_rel,
            "-CommitMessage", "Apply task status update (X)",
            "-Branch", "main",
        ],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, (result.stdout + result.stderr)

    # (2) The worker's uncommitted edit must be preserved byte-for-byte.
    assert worker_file.read_text(encoding="utf-8") == worker_edit, (result.stdout + result.stderr)

    # (3) The primary tree stays on the task branch with no new commit, so the
    # ledger commit never landed on the task branch.
    current_branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert current_branch == "task/BASE-TASK-260626-0001"
    assert git("rev-parse", "HEAD").stdout.strip() == task_head_before

    # (1) Exactly one new commit on origin/main, changing ONLY the ledger.
    count_after = int(git("rev-list", "--count", "main", cwd=origin).stdout.strip())
    assert count_after == count_before + 1, (result.stdout + result.stderr)
    new_sha = git("rev-parse", "main", cwd=origin).stdout.strip()
    changed = git("diff-tree", "--no-commit-id", "--name-only", "-r", new_sha, cwd=origin).stdout.strip()
    assert changed.splitlines() == [ledger_rel], changed
    pushed_ledger = git("show", f"{new_sha}:{ledger_rel}", cwd=origin).stdout
    assert pushed_ledger == new_ledger


