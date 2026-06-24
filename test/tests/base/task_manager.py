"""Feature ID: 5.3.1.7. Task manager interface prep test module."""

import json
import shutil
import tempfile
import threading
import time
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import scripts.task_manager as task_manager
from scripts.task_manager import (
    _TaskManagerHandler,
    _append_comment,
    _create_server,
    _create_task,
    _load_tasks_store,
    _next_task_id,
    _save_tasks_store,
    _task_id_prefix_token,
    _update_task,
)


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
    with urllib.request.urlopen(request, timeout=10) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else {}


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
    assert prompt.startswith("Implement the requested task")
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
    monkeypatch.setattr(task_manager, "_build_review_prompt", lambda task, tasks_path: "review prompt")

    task_manager._start_copilot_for_task(task, tasks_path, DummyStore(), mode="review")
    session = task.get("worker_session")
    assert session and session["pid"] == 24680
    assert session["mode"] == "review"
    assert session["window_title"].startswith("Copilot Review")


def test_task_manager_template_has_ready_tab():
    template_path = Path(__file__).resolve().parents[3] / "resources" / "templates" / "task_manager.html"
    template = template_path.read_text(encoding="utf-8")
    assert 'data-status="Ready"' in template
    assert 'id="cnt-Ready"' in template
    assert "'ToDo', 'InProgress', 'Ready', 'Done'" in template


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
    monkeypatch.setattr(task_manager, "_build_copilot_prompt", lambda task, tasks_path: "prompt")

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